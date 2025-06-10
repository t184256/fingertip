# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import os
import shutil
import tempfile
import time
import glob

import fasteners

import fingertip.expiration
import fingertip.machine
from fingertip.util import log, path, temp, units


def _time(path):
    s = os.stat(path)
    return max(s.st_mtime, s.st_atime, s.st_ctime)


@fingertip.transient
def main(what=None, *args, **kwargs):
    if what == 'everything':
        return everything()
    if what == 'periodic':
        return periodic()
    elif what in ('downloads', 'logs', 'machines', 'tempfiles', 'saviour'):
        return globals()[what](*args, **kwargs)
    log.error('usage: ')
    log.error('    fingertip cleanup downloads [<older-than>]')
    log.error('    fingertip cleanup logs [<older-than>]')
    log.error('    fingertip cleanup machines [<expired-for>|all]')
    log.error('    fingertip cleanup tempfiles [<older-than> [<location]]')
    log.error('    fingertip cleanup saviour [<expired-for>|all]')
    log.error('    fingertip cleanup everything')
    log.error('    fingertip cleanup periodic')
    raise SystemExit()


def downloads(older_than=0):
    cutoff_time = time.time() - units.parse_time_interval(older_than)
    _cleanup_dir(path.DOWNLOADS, lambda f: _time(f) >= cutoff_time)


def logs(older_than=0):
    cutoff_time = time.time() - units.parse_time_interval(older_than)
    _cleanup_dir(path.LOGS, lambda f: _time(f) >= cutoff_time)


def _cleanup_dir(dirpath, preserve_func):
    for root, dirs, files in os.walk(dirpath, topdown=False):
        for f in (os.path.join(root, x) for x in files):
            if preserve_func(f):
                continue
            assert os.path.realpath(f).startswith(os.path.realpath(dirpath))
            os.unlink(f)
        for d in (os.path.join(root, x) for x in dirs):
            if preserve_func(d):
                continue
            assert os.path.realpath(d).startswith(os.path.realpath(dirpath))
            try:
                os.rmdir(d)
            except OSError:  # directory not empty => ignore
                pass


def machines(expired_for=0):
    if expired_for != 'all':
        adjusted_time = time.time() - units.parse_time_interval(expired_for)
    else:
        adjusted_time = None
    for root, dirs, files in os.walk(path.MACHINES, topdown=False):
        for d in (os.path.join(root, x) for x in dirs):
            lock_path = os.path.join(root, '.' + os.path.basename(d) + '-lock')
            lock = fasteners.process_lock.InterProcessLock(lock_path)
            lock.acquire()
            try:
                remove = fingertip.machine.needs_a_rebuild(d, by=adjusted_time)
            except Exception as ex:
                log.warning(f'while processing {d}: {ex}')
                remove = True
            if (expired_for == 'all' or remove):
                assert os.path.realpath(d).startswith(
                    os.path.realpath(path.MACHINES)
                )
                log.info(f'removing {os.path.realpath(d)}')
                if not os.path.islink(d):
                    shutil.rmtree(d)
                else:
                    os.unlink(d)
            else:
                log.debug(f'keeping {os.path.realpath(d)}')
            os.unlink(lock_path)
            lock.release()


def tempfiles(older_than='4h', location=None):
    location = location or tempfile.gettempdir()
    cutoff_time = time.time() - units.parse_time_interval(older_than)
    _cleanup_dir(location, lambda f: (_time(f) >= cutoff_time or
                                      temp.AUTOREMOVE_PREFIX not in f))


def saviour(expired_for='10d'):
    if expired_for != 'all':
        adjusted_time = time.time() - units.parse_time_interval(expired_for)
    else:
        adjusted_time = 0
    for time_file_path in glob.glob(
        path.saviour('**/.last_mirrored'), recursive=True, include_hidden=True
    ):
        d = os.path.dirname(time_file_path)
        lock_path = os.path.join(
            os.path.dirname(d), '.' + os.path.basename(d) + '-lock')
        lock = fasteners.process_lock.InterProcessLock(lock_path)
        lock.acquire()
        remove = _time(time_file_path) <= adjusted_time
        if (expired_for == 'all' or remove):
            assert os.path.realpath(d).startswith(
                os.path.realpath(path.SAVIOUR)
            )
            log.info(f'removing {os.path.realpath(d)}')
            if not os.path.islink(d):
                shutil.rmtree(d)
            else:
                os.unlink(d)
        else:
            log.debug(f'keeping {os.path.realpath(d)}')
        os.unlink(lock_path)
        lock.release()
    _, top_dirs, __ = next(os.walk(path.SAVIOUR))
    for top_dir in top_dirs:
        if top_dir == "_" or top_dir.startswith('.'):
            continue
        for root, dirs, files in os.walk(os.path.join(path.SAVIOUR, top_dir)):
            for f in (os.path.join(root, x) for x in files):
                if not os.path.exists(os.readlink(f)):
                    os.unlink(f)
                    log.info(f'removing {f}')
                else:
                    log.debug(f'keeping {f}')

def periodic():
    machines('4h')
    downloads('30d')
    logs('30d')
    tempfiles()
    tempfiles(location='/tmp')  # backend.qemu uses /tmp specifically
    saviour()


def everything():
    downloads()
    logs()
    machines('all')
    tempfiles(0)
    tempfiles(0, '/tmp')  # backend.qemu uses /tmp specifically
    saviour('all')
