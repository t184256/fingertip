# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

"""
Helper functions and constants for fingertip: reflinking (CoW-powered copying).
"""
# TODO: clean up even more?

import subprocess

from fingertip.util import log, temp


def always(src, dst):
    subprocess.run(['cp', '--reflink=always', src, dst], check=True)


def auto(src, dst):
    subprocess.run(['cp', '--reflink=auto', src, dst], check=True)


def is_supported(dirpath):
    tmp = temp.disappearing_file(dstdir=dirpath)
    r = subprocess.Popen(['cp', '--reflink=always', tmp, tmp + '-reflink'],
                         stderr=subprocess.PIPE)
    _, err = r.communicate()
    r.wait()
    temp.remove(tmp, tmp + '-reflink')
    sure_not = b'failed to clone' in err and b'Operation not supported' in err
    if r.returncode and not sure_not:
        log.error('reflink support detection inconclusive, cache dir problems')
    return r.returncode == 0
