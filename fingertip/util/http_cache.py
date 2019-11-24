# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import http.server
import os
import socketserver
import threading
import urllib
import urllib.request

from fingertip.util import path, log

instances = {}


class ThreadingHTTPServer(http.server.HTTPServer, socketserver.ThreadingMixIn):
    pass


class HTTPCache:
    def path_by_url(self, url, force_protocol=None):
        u = urllib.parse.urlparse(url, allow_fragments=False)
        assert not (u.params or u.query), 'url too complex'
        protocol = u.scheme
        if protocol and force_protocol:
            assert protocol == force_protocol, f'{protocol}!={force_protocol}'
        r = (u.netloc + u.path).split('/')
        p = (path.downloads(protocol, *r) if r[0] != 'cache' else
             path.downloads(*r[1:]))
        assert os.path.abspath(p).startswith(path.CACHE)
        return p

    def fetch(self, url, force_protocol=None, always=False):
        cached_path = self.path_by_url(url, force_protocol=force_protocol)
        if not os.path.exists(cached_path) and not self.offline or always:
            log.info(f'fetching: {url}')
            try:
                with path.wip(cached_path, makedirs=True) as wip:
                    urllib.request.urlretrieve(url, wip)
                log.debug(f'fetched: {cached_path}')
            except urllib.error.HTTPError as e:
                if str(e) == 'HTTP Error 404: Not Found':
                    log.warn(f'404: Not found on {url}')
                else:
                    raise
        return cached_path

    def __init__(self, host='127.0.0.1', port=0):
        self.host = host
        self.offline = os.getenv('FINGERTIP_OFFLINE', '0') != '0'
        if self.offline:
            log.warn('Offline mode')
        global instances
        if (host, port, self.offline) in instances:
            self.port = instances[(host, port, self.offline)].port
        else:
            http_cache = self

            class Handler(http.server.SimpleHTTPRequestHandler):
                def translate_path(self, _path):
                    return http_cache.path_by_url(self.path)

                def do_HEAD(self):
                    log.debug(f'HEAD {self.path}')
                    http_cache.fetch(self.path, 'http')  # intended limitation
                    super().do_HEAD()
                    log.debug(f'HEAD served: {self.path}')

                def do_GET(self):
                    log.debug(f'GET {self.path}')
                    http_cache.fetch(self.path, 'http')  # intended limitation
                    try:
                        super().do_GET()
                    except ConnectionResetError:
                        log.warn(f'Connection reset for GET {self.path}')
                    log.debug(f'GET served: {self.path}')

            httpd = ThreadingHTTPServer((host, port), Handler)
            _, self.port = httpd.socket.getsockname()
            threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def proxied_url(self, cached_path):
        internal_path = cached_path.replace(path.DOWNLOADS, '', 1).lstrip('/')
        return f'http://cache/{internal_path}'

    # def direct_url(self, cached_path, baseurl=None):
    #     baseurl = baseurl or f'http://{self.host}:{self.port}'
    #     internal_path = cached_path.replace(path.CACHE, '', 1).lstrip('/')
    #     return f'{baseurl}/{internal_path}'
