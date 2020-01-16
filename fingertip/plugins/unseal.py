# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.


def main(m):
    if m.sealed and m.hooks.unseal:
        with m:
            m.hooks.unseal()
            m.sealed = False
    return m
