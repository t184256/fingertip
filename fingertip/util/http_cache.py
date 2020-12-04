# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import hashlib
import http
import http.server
import os
import shutil
import socketserver
import stat
import threading
import time
import urllib3

import requests
import requests_mock
import cachecontrol
import cachecontrol.caches
import RangeHTTPServer

from fingertip.util import log, path, reflink


BIG = 2**30  # too big for caching
STRIP_HEADERS = ('TE', 'Transfer-Encoding', 'Keep-Alive', 'Trailer', 'Upgrade',
                 'Connection', 'Host', 'Accept')
SAVIOUR_DEFAULTS = 'local,cached+direct'
RETRIES_MAX = 7
COOLDOWN = 20


def is_cache_group_writeable():
    if os.path.exists(path.CACHE):
        mode = stat.S_IMODE(os.stat(path.CACHE).st_mode)
        return bool(mode & 0o020)


def saviour_sources():
    return [(t[len('cached+'):] if t.startswith('cached+') else t,
             t.startswith('cached+'))
            for t in
            os.getenv('FINGERTIP_SAVIOUR', SAVIOUR_DEFAULTS).split(',')]


def is_fetcheable(source, url, timeout=2):
    if source == 'local':
        return os.path.exists(path.saviour(url))
    elif source != 'direct':
        url = source + '/' + url
        url = 'http://' + url if '://' not in source else url
    try:
        r = requests.head(url, allow_redirects=False, timeout=timeout)
        return r.status_code < 400
    except (requests.exceptions.BaseHTTPError, urllib3.exceptions.HTTPError,
            requests.exceptions.Timeout, OSError) as ex:
        log.warning(f'{ex}')
        return False
    return False


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


class HTTPCache:
    def __init__(self, host='127.0.0.1', port=0):
        self.host = host
        self._mocks = {}
        self._local_files_to_serve = {}
        http_cache = self

        class Handler(RangeHTTPServer.RangeRequestHandler):
            protocol_version = 'HTTP/1.1'

            def __init__(self, *args, directory=None, **kwargs):
                super().__init__(*args, directory=path.SAVIOUR, **kwargs)

            def _status_and_headers(self, status_code, headers):
                self.send_response(status_code)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()

            def _serve(self, uri, headers, meth='GET'):
                uri = uri.lstrip('/')
                if uri in http_cache._mocks:
                    return self._serve_http(uri, headers, meth, cache=False)
                sources = saviour_sources()
                for i, (source, cache) in enumerate(sources):
                    if is_fetcheable(source, uri) or i == len(sources) - 1:
                        if source == 'local':
                            if meth == 'GET':
                                return super().do_GET()
                            elif meth == 'HEAD':
                                return super().do_HEAD()
                        elif source == 'direct':
                            su = uri
                        else:
                            su = source + '/' + uri
                            su = 'http://' + su if '://' not in source else su
                        return self._serve_http(su, headers, meth, cache=cache)

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
                                log.info(f'suppressing HTTPS redirect {nu}')
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
                            log.info(f'streaming {basename} directly '
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
                    if 'Content-Length' in r.headers:
                        length = int(r.headers['Content-Length'])
                        if len(data) != length:
                            data = hack_around_unpacking(uri, headers, data)
                        assert len(data) == length
                except BrokenPipeError:
                    error = f'Upwards broken pipe for {meth} {uri}'
                except ConnectionResetError:
                    error = f'Upwards connection reset for {meth} {uri}'
                except requests.exceptions.ConnectionError:
                    error = f'Upwards connection error for {meth} {uri}'
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
                log.info(f'{meth} {basename} served ({uri})')

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
                log.info(f'serving {os.path.basename(http_path)} '
                         f'directly from {local_path}')
                return local_path

        httpd = ThreadingHTTPServer((host, port), Handler)
        _, self.port = httpd.socket.getsockname()
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def _get_requests_session(self, direct=False):
        if not direct:
            kwargs = ({'filemode': 0o0660, 'dirmode': 0o0770}
                      if is_cache_group_writeable() else {})
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

    def is_fetcheable(self, url):
        return any((is_fetcheable(src, url) for src in saviour_sources()))

    def fetch(self, url, out_path):
        sources = saviour_sources()
        for i, (source, cache) in enumerate(sources):
            if is_fetcheable(source, url) or i == len(sources) - 1:
                if source == 'local':
                    reflink.auto(path.saviour(url), out_path)
                    return
                sess = self._get_requests_session(direct=not cache)
                if source == 'direct':
                    surl = url
                else:
                    surl = source + '/' + url
                    surl = 'http://' + surl if '://' not in source else surl
                log.debug(f'fetching{"/caching" if cache else ""} '
                          f'{os.path.basename(url)} from {surl}')
                r = sess.get(surl)  # not raw because that punctures cache
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


# Hack around requests uncompressing Content-Encoding: gzip


def hack_around_unpacking(uri, headers, wrong_content):
    log.warning(f're-fetching correct content for {uri}')
    r = requests.get(uri, headers=headers, stream=True, allow_redirects=False)
    h = hashlib.sha256(wrong_content).hexdigest()
    cachefile = path.downloads('fixups', h, makedirs=True)
    if not os.path.exists(cachefile):
        with path.wip(cachefile) as wip:
            with open(wip, 'wb') as f:
                shutil.copyfileobj(r.raw, f)
    with open(cachefile, 'rb') as f:
        return f.read()
