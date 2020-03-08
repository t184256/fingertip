# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import os
import shutil
import time

import fasteners

import fingertip.expiration
import fingertip.machine
from fingertip.util import log, path


OFFLINE = os.getenv('FINGERTIP_OFFLINE', '0') != '0'


@fingertip.transient
def main(what=None, older_than=0):
    if OFFLINE:
        log.error('FINGERTIP_OFFLINE set to 1, no cleanup')
        return
    if what == 'everything':
        return everything()
    elif what in ('downloads', 'logs', 'machines'):
        return globals()[what](older_than)
    log.error('usage: ')
    log.error('    fingertip cleanup downloads [<older-than>]')
    log.error('    fingertip cleanup logs [<older-than>]')
    log.error('    fingertip cleanup machines [<expired-for>|all]')
    log.error('    fingertip cleanup everything')
    raise SystemExit()


def downloads(older_than=0):
    _cleanup_dir(path.DOWNLOADS, older_than, lambda f: os.stat(f).st_ctime)


def logs(older_than=0):
    _cleanup_dir(path.LOGS, older_than, lambda f: os.stat(f).st_ctime)


def _cleanup_dir(dirpath, older_than, time_func):
    cutoff_time = time.time() - fingertip.expiration._parse(older_than)
    for root, dirs, files in os.walk(dirpath, topdown=False):
        for f in (os.path.join(root, x) for x in files):
            assert os.path.realpath(f).startswith(dirpath)
            if time_func(f) <= cutoff_time:
                log.info(f'removing {os.path.realpath(f)}')
                os.unlink(f)
        for d in (os.path.join(root, x) for x in dirs):
            assert os.path.realpath(d).startswith(dirpath)
            try:
                log.info(f'removing {os.path.realpath(d)}')
                os.rmdir(d)
            except OSError:  # directory not empty => ignore
                pass


def machines(expired_for=0):
    if expired_for != 'all':
        adjusted_time = time.time() - fingertip.expiration._parse(expired_for)
    for root, dirs, files in os.walk(path.MACHINES, topdown=False):
        for d in (os.path.join(root, x) for x in dirs):
            lock_path = os.path.join(root, '.' + os.path.basename(d) + '-lock')
            lock = fasteners.process_lock.InterProcessLock(lock_path)
            lock.acquire()
            try:
                remove = fingertip.machine.needs_a_rebuild(d, by=adjusted_time)
            except (FileNotFoundError, EOFError):
                remove = True
            if (expired_for == 'all' or remove):
                assert os.path.realpath(d).startswith(path.MACHINES)
                log.info(f'removing {os.path.realpath(d)}')
                if not os.path.islink(d):
                    shutil.rmtree(d)
                else:
                    os.unlink(d)
            else:
                log.debug(f'keeping {os.path.realpath(d)}')
            os.unlink(lock_path)
            lock.release()


def everything():
    downloads()
    logs()
    machines('all')
