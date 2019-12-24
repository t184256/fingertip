# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import http.server
import os
import socketserver
import threading

import requests
import requests_mock
import cachecontrol
import cachecontrol.caches

from fingertip.util import path, log


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    pass


class HTTPCache:
    def __init__(self, host='127.0.0.1', port=0):
        self.host = host
        self._mocks = []
        http_cache = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def _serve(self, uri, meth='GET'):
                log.debug(f'{meth} {uri}')
                try:
                    sess = http_cache._get_requests_session()
                    m_func = getattr(sess, meth.lower())
                    r = m_func(uri if '://' in uri else 'http://self' + uri)
                    self.send_response(r.status_code)
                    for k, v in r.headers.items():
                        self.send_header(k, v)
                    self.end_headers()
                    if meth == 'GET':
                        self.wfile.write(r.content)
                    log.debug(f'{meth} served: {self.path}')
                except ConnectionResetError:
                    log.warn(f'Connection reset for {meth} {self.path}')

            def do_HEAD(self):
                self._serve(uri=self.path, meth='HEAD')

            def do_GET(self):
                self._serve(uri=self.path, meth='GET')

            def log_message(self, format, *args):  # supress existing logging
                return

        httpd = ThreadingHTTPServer((host, port), Handler)
        _, self.port = httpd.socket.getsockname()
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def _get_requests_session(self):
        cache = cachecontrol.caches.FileCache(path.DOWNLOADS)
        sess = cachecontrol.CacheControl(requests.Session(), cache=cache)
        for uri, kwargs in self._mocks:
            adapter = requests_mock.Adapter()
            adapter.register_uri('HEAD', uri, **kwargs)
            adapter.register_uri('GET', uri, **kwargs)
            sess.mount(uri, adapter)
        log.debug(f'session created {sess}')
        return sess

    def fetch(self, url, out_path):
        sess = self._get_requests_session()
        r = sess.get(url)
        with open(out_path, 'wb') as f:
            f.write(r.content)

    def mock(self, uri, text):
        """
        Examples:
          * mock('http://self/test', 'TEST')
            and access directly as `http://<host>:<port>/test`
          * mock('http://anything/test', 'ANYTHINGT')
            and access through proxy as `http://anything/test`
        """
        content_length = {'Content-Length': str(len(text.encode()))}
        self._mocks.append((uri, {'text': text, 'headers': content_length}))


# Fully offline cache utilization functionality


def c_r_offline(self, request):
    cache_url = self.cache_url(request.url)
    log.debug(f'looking up {cache_url} in the cache')
    cache_data = self.cache.get(cache_url)
    if cache_data is None:
        log.error(f'{cache_url} not in cache and fingertip is offline')
        return False
    resp = self.serializer.loads(request, cache_data)
    if not resp:
        log.error(f'{cache_url} cache entry deserialization failed, ignored')
        return False
    log.warn(f'Using {cache_url} from offline cache')
    return resp


if os.getenv('FINGERTIP_OFFLINE', '0') != '0':
    cachecontrol.controller.CacheController.cached_request = c_r_offline
