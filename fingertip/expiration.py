# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import datetime
import time


def _parse(interval):
    _SUFFIXES = {'s': 1, 'm': 60, 'h': 60 * 60, 'd': 24 * 60 * 60}
    if isinstance(interval, str) and interval[-1] in _SUFFIXES:
        return float(interval[:-1]) * _SUFFIXES[interval[-1]]
    return float(interval)


class Expiration:
    def __init__(self, expire_in):
        self.time = time.time() + _parse(expire_in)

    def pretty(self):
        return datetime.datetime.fromtimestamp(self.time).isoformat()

    def cap(self, interval):
        self.time = min(self.time, time.time() + _parse(interval))

    def is_expired(self):
        return self.time < time.time()
