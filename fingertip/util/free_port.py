# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

"""
Helper functions for fingertip: finding a (hopefully free) TCP port.
"""

import socket


def find(address='127.0.0.1'):
    # race-condition-prone, but still better than just random
    # no, I don't want to pull in port-for or something
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((address, 0))
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    _, port = s.getsockname()
    s.close()
    return port
