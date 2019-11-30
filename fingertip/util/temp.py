# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

"""
Helper functions for fingertip: working with self-destructing tempfiles.
"""

# TODO: check that is has enough usage to justify it, prettify if so

import atexit
import os
import shutil
import tempfile

from fingertip.util import log


AUTOREMOVE_PREFIX = 'tmp-fingertip.'


def unique_dir(dstdir=None, hint=''):  # defaults to /tmp
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
            log.warn(f'cleanup error for {path}: {e}')


def disappearing_file(dstdir=None, hint=''):  # defaults to /tmp
    prefix = AUTOREMOVE_PREFIX + hint + '.' if hint else AUTOREMOVE_PREFIX
    _, temp_file_path = tempfile.mkstemp(prefix=prefix, dir=dstdir)
    assert AUTOREMOVE_PREFIX in temp_file_path
    atexit.register(lambda: remove(temp_file_path))
    return temp_file_path


def disappearing_dir(dstdir=None, hint=''):  # defaults to /tmp
    prefix = AUTOREMOVE_PREFIX + hint + '.' if hint else AUTOREMOVE_PREFIX
    temp_dir_path = tempfile.mkdtemp(prefix=prefix, dir=dstdir)
    assert AUTOREMOVE_PREFIX in temp_dir_path
    atexit.register(lambda: remove(temp_dir_path))
    return temp_dir_path
