# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import abc
import dataclasses
import hashlib
import http
import http.server
import os
import re
import shutil
import socketserver
import stat
import threading
import time
from pathlib import Path

import cachecontrol
import cachecontrol.caches
import RangeHTTPServer
import requests
import requests_mock
import urllib3

from fingertip.util import log, path, reflink


# HTTPCache attempts caching of requests it performs using cachecontrol.
# In order to be an even better netizen, one could mirror stuff in advance
# and point HTTPCache at the alternative sources.
# The natural logical order of sources to try would be
# a local mirror, then your own HTTP mirrors (with or without local caching),
# and only then falling back to direct fetches.
# Using a mirror comes with the responsibility of ensuring it doesn't go stale.
# Negative results will never get cached.
#
# See also:
# https://github.com/t184256/fingertip/blob/master/fingertip/plugins/saviour.py
# https://github.com/t184256/fingertip/blob/master/saviour.example.yml


BIG = 2**30  # too big for caching
STRIP_HEADERS = ('TE', 'Transfer-Encoding', 'Keep-Alive', 'Trailer', 'Upgrade',
                 'Connection', 'Host', 'Accept')
SAVIOUR_DEFAULTS = 'local,cached+direct'
RETRIES_MAX = 7
COOLDOWN = 20
WARN_ON_DIRECT = os.getenv('FINGERTIP_SAVIOUR_WARN_ON_DIRECT', None) == '1'


class FetchSource(abc.ABC):
    """Base class for fetch sources. Cannot be instantiated directly."""

    @abc.abstractmethod
    def __init__(self):
        pass


@dataclasses.dataclass(frozen=True)
class FetchSourceLocal(FetchSource):
    """Try local_path/http://serv.er/file first. Cannot be cached."""
    local_path: Path


@dataclasses.dataclass(frozen=True)
class FetchSourceSaviour(FetchSource):
    """Try http://saviour/url/http://serv.er/file.

    Saviours are simplistic mirrors that should offer files at urls
    concatenated with saviour urls. The default protocol is HTTP.
    A saviour_url = 'saviour/url' would be expanded to 'http://saviour/url',
    and a query to https://serv.er/file will first go to
    http://saviour/url/https://serve.er/file
    This is also useful for mounting HTTP downgrade attacks,
    in the name of better caching and netiquette, of course.
    """
    saviour_url: str
    cached: bool = True

    def _url_on_saviour(self, url):
        saviour_url = self.saviour_url
        if '://' not in saviour_url:
            saviour_url = 'http://' + saviour_url
        return saviour_url + '/' + url


@dataclasses.dataclass(frozen=True)
class FetchSourceDirect(FetchSource):
    """Try http://serv.er/file; https://serv.er/file if port 80 is closed."""
    cached: bool = True


def _saviour_sources():
    s = os.getenv('FINGERTIP_SAVIOUR', SAVIOUR_DEFAULTS) or SAVIOUR_DEFAULTS
    sources = []
    for t in s.split(','):
        cached = t.startswith('cached+')
        source_name = t[len('cached+'):] if cached else t

        if source_name == 'local':
            sources.append(FetchSourceLocal(Path(path.SAVIOUR)))
        elif source_name == 'direct':
            sources.append(FetchSourceDirect(cached=cached))
        else:  # saviour URL
            sources.append(FetchSourceSaviour(source_name, cached=cached))

    assert sources, 'FINGERTIP_SAVIOUR must define at least one source'
    return sources


def _how_do_I_fetch(fetch_sources, url, allow_redirects=False, timeout=2,
                    fallback_to_last=False):
    UPGRADABLE_ERRORS = (urllib3.exceptions.ConnectionError,
                         requests.exceptions.ConnectionError)
    NON_UPGRADABLE_ERRORS = (requests.exceptions.BaseHTTPError,
                             urllib3.exceptions.HTTPError,
                             requests.exceptions.Timeout, OSError)

    def _head(url):
        r = requests.head(url,
                          allow_redirects=allow_redirects, timeout=timeout)
        return r.status_code < (300 if allow_redirects else 400)

    ret_url = url  # set here for local only + fallback_to_last case
    for fetch_source in fetch_sources:
        if isinstance(fetch_source, FetchSourceLocal):
            local_path = str(fetch_source.local_path / url)
            if os.path.exists(local_path):
                return fetch_source, local_path
            continue
        elif isinstance(fetch_source, FetchSourceDirect):
            try_url = url
        else:
            assert isinstance(fetch_source, FetchSourceSaviour)
            try_url = fetch_source._url_on_saviour(url)
        # now for both FetchSourceSaviour and FetchSourceDirect cases
        try:
            if _head(try_url):
                if (isinstance(fetch_source, FetchSourceDirect)
                        and WARN_ON_DIRECT):
                    log.warning('FINGERTIP_SAVIOUR_WARN_ON_DIRECT: '
                                f'{url} not found on any mirror')
                return fetch_source, ret_url
        except UPGRADABLE_ERRORS as ex:
            if (isinstance(fetch_source, FetchSourceDirect)
                    and url.startswith('http://')):
                # one other thing we can do is try upgrading from HTTP to HTTPS
                try_url = ret_url = url.replace('http://', 'https://', 1)
                log.debug(f'we can still try upgrading to HTTPS: {try_url}')
                try:
                    if _head(try_url):
                        if WARN_ON_DIRECT:
                            log.warning('FINGERTIP_SAVIOUR_WARN_ON_DIRECT: '
                                        f'{url} not found on any mirror')
                        return fetch_source, ret_url  # https-upgraded
                except UPGRADABLE_ERRORS + NON_UPGRADABLE_ERRORS:
                    pass
            log.warning(f'{ex}')
        except NON_UPGRADABLE_ERRORS as ex:
            log.warning(f'{ex}')
    if fallback_to_last:
        return fetch_sources[-1], ret_url  # could be https-upgraded (consider
                                           # last FetchSourceDirect 404ing)
    return None, None


class HTTPCache:
    @staticmethod
    def _validate_sources_get_local_path(sources):
        """Validate that there is at most one FetchSourceLocal in sources."""
        local_path = None
        for s in sources:
            if isinstance(s, FetchSourceLocal):
                if local_path is None:
                    local_path = s.local_path
                else:
                    raise ValueError('Only one FetchSourceLocal allowed, got '
                                     f'{local_path} and {s.local_path}')
        return local_path or '/var/empty'

    def __init__(self, host='127.0.0.1', port=0, sources=None):
        if sources is None:
            #sources = [FetchSourceDirect(cached=True)]
            sources = _saviour_sources()
        local_path = self._validate_sources_get_local_path(sources)
        self.sources = tuple(sources)
        self.host = host
        self._mocks = {}
        self._local_files_to_serve = {}
        http_cache = self

        class Handler(RangeHTTPServer.RangeRequestHandler):
            protocol_version = 'HTTP/1.1'

            def __init__(self, *args, directory=None, **kwargs):
                super().__init__(*args, directory=local_path, **kwargs)

            def _status_and_headers(self, status_code, headers):
                self.send_response(status_code)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()

            @staticmethod
            def _demangle(uri):
                # wget2 mangles http://10.0.2.224:8080/https://smth
                # into a broken http://10.0.2.224:8080/https:/smth
                return re.sub(r':/([^/])', r'://\1', uri)

            def _serve(self, uri, headers, meth='GET'):
                uri = self._demangle(uri.lstrip('/'))
                if uri in http_cache._mocks:
                    return self._serve_http(uri, headers, meth, cache=False)
                fetch_source, url = _how_do_I_fetch(http_cache.sources, uri,
                                                    fallback_to_last=True)
                if isinstance(fetch_source, FetchSourceLocal):
                    if meth == 'GET':
                        return super().do_GET()
                    elif meth == 'HEAD':
                        return super().do_HEAD()
                elif isinstance(fetch_source, FetchSourceSaviour):
                    url = fetch_source._url_on_saviour(url)
                return self._serve_http(url, headers, meth,
                                        cache=fetch_source.cached)

            def _serve_http(self, uri, headers, meth='GET', cache=True,
                            retries=RETRIES_MAX):
                sess = http_cache._get_requests_session(direct=not cache)
                sess_dir = http_cache._get_requests_session(direct=True)
                basename = os.path.basename(uri)

                headers = {k: v for k, v in headers.items() if
                           not (k in STRIP_HEADERS or k.startswith('Proxy-'))}
                headers['Accept-Encoding'] = 'identity'
                log.debug(f'{meth} {basename} ({uri})')
                for k, v in headers.items():
                    log.debug(f'{k}: {v}')

                error = None
                try:
                    if meth == 'GET':
                        # direct streaming or trickery might be required...
                        preview = sess.head(uri, headers=headers,
                                            allow_redirects=False)
                        if (300 <= preview.status_code < 400 and
                                'Location' in preview.headers):
                            nu = preview.headers['Location']
                            if nu.startswith('https://'):
                                # no point in serving that, we have to pretend
                                # that never happened
                                log.debug(f'suppressing HTTPS redirect {nu}')
                                return self._serve_http(nu, headers, meth=meth,
                                                        cache=cache,
                                                        retries=retries)
                        direct = []
                        if not cache:
                            direct.append('caching disabled for this source')
                        if int(preview.headers.get('Content-Length', 0)) > BIG:
                            direct.append(f'file bigger than {BIG}')
                        if 'Range' in headers:
                            # There seems to be a bug in CacheControl
                            # that serves contents in full if a range request
                            # hits a non-ranged cached entry.
                            direct.append('ranged request, playing safe')
                        if direct:
                            # Don't cache, don't reencode, stream it as is
                            log.debug(f'streaming {basename} directly '
                                      f'from {uri} ({", ".join(direct)})')
                            r = sess_dir.get(uri, headers=headers, stream=True)
                            self._status_and_headers(r.status_code, r.headers)
                            shutil.copyfileobj(r.raw, self.wfile)
                            return

                    # fetch with caching
                    m_func = getattr(sess, meth.lower())
                    r = m_func(uri if '://' in uri else 'http://self' + uri,
                               headers=headers, allow_redirects=False)
                    data = r.content
                    if meth == 'GET':
                        if 'Content-Length' in r.headers:
                            length = int(r.headers['Content-Length'])
                            if len(data) != length:
                                data = http_cache._hack_around_unpacking(
                                        uri, headers, data
                                )
                            assert len(data) == length
                except BrokenPipeError:
                    error = f'Upwards broken pipe for {meth} {uri}'
                except ConnectionResetError as connreseterr:
                    error = (f'Upwards connection reset for {meth} {uri} '
                             f'({connreseterr})')
                except requests.exceptions.ConnectionError as connerr:
                    error = (f'Upwards connection error for {meth} {uri} '
                             f'({connerr})')
                if error:
                    # delay a re-request
                    if retries:
                        log.warning(f'{error} (will retry x{retries})')
                        t = (RETRIES_MAX - retries) / RETRIES_MAX * COOLDOWN
                        time.sleep(t)
                        return self._serve_http(uri, headers, meth=meth,
                                                cache=cache,
                                                retries=retries-1)
                    else:
                        log.error(f'{error} (out of retries)')
                        self.send_error(http.HTTPStatus.SERVICE_UNAVAILABLE)
                        return
                log.debug(f'{meth} {basename} fetched {r.status_code} ({uri})')
                try:
                    self._status_and_headers(r.status_code, r.headers)
                    if meth == 'GET':
                        self.wfile.write(data)
                except BrokenPipeError:
                    log.warning(f'Downwards broken pipe for {meth} {uri}')
                except ConnectionResetError:
                    log.warning(f'Downwards connection reset for {meth} {uri}')
                except requests.exceptions.ConnectionError:
                    log.warning(f'Downwards connection error for {meth} {uri}')
                log.debug(f'{meth} {basename} served ({uri})')

            def do_HEAD(self):
                if self.path in http_cache._local_files_to_serve:
                    return super().do_HEAD()  # act as a HTTP server
                self._serve(uri=self.path, headers=self.headers, meth='HEAD')

            def do_GET(self):
                if self.path in http_cache._local_files_to_serve:
                    return super().do_GET()  # act as a HTTP server
                self._serve(uri=self.path, headers=self.headers, meth='GET')

            def log_message(self, format, *args):  # supress existing logging
                return

            def translate_path(self, http_path):  # directly serve local files
                if http_path in http_cache._local_files_to_serve:
                    local_path = http_cache._local_files_to_serve[http_path]
                else:
                    local_path = super().translate_path(http_path)
                log.debug(f'serving {os.path.basename(http_path)} '
                          f'directly from {local_path}')
                return local_path

        class ThreadingHTTPServer(socketserver.ThreadingMixIn,
                                  http.server.HTTPServer):
            daemon_threads = True

        httpd = ThreadingHTTPServer((host, port), Handler)
        _, self.port = httpd.socket.getsockname()
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

    @staticmethod
    def _is_cache_group_writeable():
        if os.path.exists(path.CACHE):
            mode = stat.S_IMODE(os.stat(path.CACHE).st_mode)
            return bool(mode & 0o020)

    def _get_requests_session(self, direct=False):
        if not direct:
            kwargs = ({'filemode': 0o0660, 'dirmode': 0o0770}
                      if self._is_cache_group_writeable() else {})
            cache = cachecontrol.caches.FileCache(path.downloads('cache'),
                                                  **kwargs)
            sess = cachecontrol.CacheControl(requests.Session(), cache=cache)
        else:
            sess = requests.Session()
        for uri, kwargs in self._mocks.items():
            adapter = requests_mock.Adapter()
            adapter.register_uri('HEAD', uri, **kwargs)
            adapter.register_uri('GET', uri, **kwargs)
            sess.mount(uri, adapter)
        return sess

    def is_fetcheable(self, url, allow_redirects=False, timeout=2,
                      sources=None):
        sources = self.sources if sources is None else sources
        return _how_do_I_fetch(sources, url, allow_redirects=allow_redirects,
                               timeout=timeout) != (None, None)

    def fetch(self, url, out_path):
        fetch_source, url = _how_do_I_fetch(self.sources, url,
                                            fallback_to_last=True)
        if isinstance(fetch_source, FetchSourceLocal):
            reflink.auto(url, out_path)
            return
        sess = self._get_requests_session(direct=not fetch_source.cached)
        if isinstance(fetch_source, FetchSourceSaviour):
            url = fetch_source._url_on_saviour(url)
        log.debug(f'fetching{"/caching" if fetch_source.cached else ""} '
                  f'{os.path.basename(url)} from {url}')
        r = sess.get(url)  # not raw because that punctures cache
        with open(out_path, 'wb') as f:
            f.write(r.content)
        return

    def mock(self, uri, text):
        """
        Mock a text file at some location.
        Examples:
          * mock('http://self/test', text='TEST')
            and access through proxy as `http://self/test`
        """
        content_length = {'Content-Length': str(len(text.encode()))}
        self._mocks[uri] = {'text': text, 'headers': content_length}

    def mock_custom(self, uri, **kwargs):
        """
        Mock a custom HTTP response.
        See ``help(requests_mock.Adapter.register_uri)`` for parameters.
        """
        self._mocks[uri] = kwargs

    def serve_local_file(self, http_path, local_path):
        """
        Serve this local file as an HTTP server.
        Examples:
          * serve('test', '/some/file')
            and access directly as `http://<host>:<port>/test`
          * serve('http://test', '/some/file')
            and access through proxy as `http://test`
        """
        self._local_files_to_serve[http_path] = local_path

    def _hack_around_unpacking(self, uri, headers, wrong_content):
        """Hack around requests uncompressing Content-Encoding: gzip"""
        log.warning(f're-fetching correct content for {uri}')
        r = requests.get(uri, headers=headers, stream=True,
                         allow_redirects=False)
        h = hashlib.sha256(wrong_content).hexdigest()
        cachefile = path.downloads('fixups', h, makedirs=True)
        if not os.path.exists(cachefile):
            with path.wip(cachefile) as wip:
                with open(wip, 'wb') as f:
                    shutil.copyfileobj(r.raw, f)
        with open(cachefile, 'rb') as f:
            return f.read()
