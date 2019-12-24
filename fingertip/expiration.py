# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import time


class Expiration:
    def __init__(self, expire_in):
        self.time = time.time() + expire_in

    def limit(self, interval):
        self.time = min(self.time, time.time() + interval)
