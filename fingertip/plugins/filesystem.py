# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2020 Red Hat, Inc., see CONTRIBUTORS.


import fingertip.machine
from fingertip.util import log, reflink


@fingertip.transient
def main(what=None):
    if what in ('setup', 'unmount', 'cleanup'):
        return globals()[what]()
    log.error('usage: ')
    log.error('    fingertip filesystem setup')
    log.error('    fingertip filesystem unmount')
    log.error('    fingertip filesystem cleanup')
    raise SystemExit()


def setup():
    reflink.storage_setup_wizard()


def unmount():
    reflink.storage_unmount()


def cleanup():
    reflink.storage_destroy()
