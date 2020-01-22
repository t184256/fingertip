# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.


def main(m):
    if m.sealed:
        m.expiration.cap('4h')
        m.sealed = False
        with m:
            m.hooks.unseal()
    return m
