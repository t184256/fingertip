# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import datetime
import os
import sys
import time

from fingertip.util import log, weak_hash


def _parse(interval):
    _SUFFIXES = {'s': 1, 'm': 60, 'h': 60 * 60, 'd': 24 * 60 * 60}
    if isinstance(interval, str) and interval[-1] in _SUFFIXES:
        return float(interval[:-1]) * _SUFFIXES[interval[-1]]
    return float(interval)


class Expiration:
    def __init__(self, expire_in):
        self.time = time.time() + _parse(expire_in)
        self._deps = {}

    def pretty(self):
        return datetime.datetime.fromtimestamp(self.time).isoformat()

    def cap(self, interval):
        self.time = min(self.time, time.time() + _parse(interval))

    def is_expired(self, by=None):
        return self.time < (by or time.time())

    def depend_on_a_file(self, path):
        path = os.path.abspath(path)
        if path.startswith('/usr/lib') or '/site-packages/' in path:
            return
        self._deps[path] = (os.stat(path).st_mtime, weak_hash.of_file(path))

    def files_have_not_changed(self):
        if os.getenv('FINGERTIP_IGNORE_CODE_CHANGES', '0') != '0':
            return True
        for path, (mtime, hash_) in self._deps.items():
            log.debug(f'checking that {path} has not changed...')
            if mtime != (os.stat(path).st_mtime):
                if hash_ != weak_hash.of_file(path):
                    log.warning(f'{path} has changed, set '
                                'FINGERTIP_IGNORE_CODE_CHANGES=1 to ignore')
                    return False
        return True

    def depend_on_loaded_python_modules(self):
        """
        Make the machine depend on all python modules loaded to date
        in an imprecise best-effort overly-cautious attempt
        to get automatic rebuilds triggered on source code changes.
        """
        for module in sys.modules:
            try:
                module_file = sys.modules[module].__file__
                if module_file:
                    self.depend_on_a_file(module_file)
            except AttributeError:
                pass
