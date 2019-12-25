# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.


def main(m):
    if m.sealed:
        m.sealed = False
        with m:
            m.hooks.unseal(m)
            return m
