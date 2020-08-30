# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

"""
Helper functions for fingertip: working with self-destructing tempfiles.
"""

# TODO: replace with python3-tempdir

import atexit
import os
import random
import shutil
import signal
import string
import tempfile

from fingertip.util import log, units


AUTOREMOVE_PREFIX = 'tmp.fingertip'


# TODO: hacky and unclean
def terminate_child(num, frame):
    print('caught SIGTERM, cleaning up...')
    raise SystemExit('SIGTERM')  # fire atexit hooks


assert signal.signal(signal.SIGTERM, terminate_child) == signal.SIG_DFL


def random_chars(k=6):
    return ''.join(random.choices(string.ascii_letters, k=k))


def suffix_non_existing(base, k=6):  # racy
    while True:
        suffix = random_chars(k)
        if not os.path.exists(base + suffix):
            return base + suffix


def unique_filename(dstdir=None, hint=''):
    dstdir = dstdir or tempfile.gettempdir()
    hint = hint if len(hint) <= 20 else hint[:20-2] + '..'
    return suffix_non_existing(os.path.join(dstdir, f'.{hint}.'))


def unique_file(dstdir=None, hint='', create=False):
    tgt = unique_filename(dstdir=dstdir, hint=hint)
    if create:
        open(tgt, 'w').close()
    return tgt


def unique_dir(dstdir=None, hint=''):
    tgt = unique_filename(dstdir=dstdir, hint=hint)
    os.mkdir(tgt)
    return tgt


def remove(*paths):
    for path in paths:
        assert os.path.basename(path).startswith('.' + AUTOREMOVE_PREFIX)
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


def disappearing(path):
    assert os.path.basename(path).startswith('.' + AUTOREMOVE_PREFIX)
    atexit.register(remove, path)
    return path


def disappearing_file(dstdir=None, hint='', create=False):
    hint = f'{AUTOREMOVE_PREFIX}.{hint}' if hint else AUTOREMOVE_PREFIX
    return disappearing(unique_file(dstdir=dstdir, hint=hint, create=create))


def disappearing_dir(dstdir=None, hint=''):
    hint = f'{AUTOREMOVE_PREFIX}.{hint}' if hint else AUTOREMOVE_PREFIX
    return disappearing(unique_dir(dstdir=dstdir, hint=hint))


def has_space(how_much='2G', reserve_fraction=.5, where=None):
    where = where or tempfile.gettempdir()
    how_much = units.parse_binary(how_much)
    total, _, free = shutil.disk_usage(where)
    if not free >= how_much:
        log.warning(f'{where} does not have {how_much} of free space')
    if not free >= total * reserve_fraction:
        log.warning(f'{where} is {int((1 - reserve_fraction) * 100)}% full')
    return free >= how_much and free >= total * reserve_fraction
