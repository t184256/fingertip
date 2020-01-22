# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import datetime
import time


class Expiration:
    def __init__(self, expire_in):
        self.time = time.time() + expire_in

    def pretty(self):
        return datetime.datetime.fromtimestamp(self.time).isoformat()

    def cap(self, interval):
        self.time = min(self.time, time.time() + interval)

    def is_expired(self):
        return self.time < time.time()
