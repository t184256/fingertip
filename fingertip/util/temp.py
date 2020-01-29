# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

"""
Helper functions for fingertip: working with self-destructing tempfiles.
"""

# TODO: replace with python3-tempdir

import atexit
import os
import signal
import shutil
import sys
import tempfile

from fingertip.util import log


AUTOREMOVE_PREFIX = 'tmp-fingertip.'


# TODO: hacky and unclean
def terminate_child(num, frame):
    print('caught SIGTERM, cleaning up...')
    sys.exit(1)  # fire atexit hooks


signal.signal(signal.SIGTERM, terminate_child)


def unique_dir(dstdir=None, hint=''):  # defaults to /tmp
    hint = hint if len(hint) <= 20 else hint[:20-2] + '..'
    return tempfile.mkdtemp(prefix=('.' + hint + '.'), dir=dstdir)


def remove(*paths):
    for path in paths:
        assert AUTOREMOVE_PREFIX in path
        try:
            if not os.path.isdir(path) or os.path.islink(path):
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
            else:
                shutil.rmtree(path, ignore_errors=True)
        except Exception as e:
            log.warning(f'cleanup error for {path}: {e}')


def disappearing_file(dstdir=None, hint=''):
    prefix = AUTOREMOVE_PREFIX + hint + '.' if hint else AUTOREMOVE_PREFIX
    _, temp_file_path = tempfile.mkstemp(prefix=prefix, dir=dstdir)
    assert AUTOREMOVE_PREFIX in temp_file_path
    atexit.register(lambda: remove(temp_file_path))
    return temp_file_path


def disappearing_dir(dstdir=None, hint=''):
    prefix = AUTOREMOVE_PREFIX + hint + '.' if hint else AUTOREMOVE_PREFIX
    temp_dir_path = tempfile.mkdtemp(prefix=prefix, dir=dstdir)
    assert AUTOREMOVE_PREFIX in temp_dir_path
    atexit.register(lambda: remove(temp_dir_path))
    return temp_dir_path


def has_space(how_much='2G', reserve_fraction=.3, where='/tmp'):
    for suffix, power in {'G': 30, 'M': 20, 'K': 10}.items():
        if isinstance(how_much, str) and how_much.endswith(suffix):
            how_much = float(how_much[:-1]) * 2 ** power
            break
    total, _, free = shutil.disk_usage(where)
    if not free >= how_much:
        log.warning(f'{where} does not have {how_much} of free space')
    if not free >= total * reserve_fraction:
        log.warning(f'{where} is {int((1 - reserve_fraction) * 100)}% full')
    return free >= how_much and free >= total * reserve_fraction
