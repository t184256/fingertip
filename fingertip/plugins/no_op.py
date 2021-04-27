# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2021 Red Hat, Inc., see CONTRIBUTORS.


def main(m, *args, **kwargs):
    m.log.debug(f'no-op plugin has been called with {args}, {kwargs}')
    return m
