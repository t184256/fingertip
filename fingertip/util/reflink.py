# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

"""
Helper functions and constants for fingertip: reflinking (CoW-powered copying).
"""
# TODO: clean up even more?

import os
import sys
import subprocess

from fingertip.util import log, temp, path


SETUP = os.getenv('FINGERTIP_SETUP', 'suggest')
SIZE = os.getenv('FINGERTIP_SETUP_SIZE', '25G')


def _cp_reflink(src, dst, *args):
    subprocess.run(['cp', '-rT', *args, src, dst], check=True)


def always(src, dst, preserve=False):
    args = ['--preserve=all'] if preserve else []
    _cp_reflink(src, dst, '--reflink=always', *args)


def auto(src, dst, preserve=False):
    args = ['--preserve=all'] if preserve else []
    _cp_reflink(src, dst, '--reflink=auto', *args)


def is_supported(dirpath):
    tmp = temp.disappearing_file(dstdir=dirpath, create=True)
    r = subprocess.Popen(['cp', '--reflink=always', tmp, tmp + '-reflink'],
                         stderr=subprocess.PIPE)
    _, err = r.communicate()
    r.wait()
    temp.remove(tmp, tmp + '-reflink')
    sure_not = b'failed to clone' in err and b'Operation not supported' in err
    if r.returncode and not sure_not:
        log.error('reflink support detection inconclusive, cache dir problems')
        log.error(f'({err})')
    return r.returncode == 0


def create_supported_fs(backing_file, size):
    subprocess.run(['fallocate', '-l', size, backing_file], check=True)
    subprocess.run(['mkfs.xfs', '-m', 'reflink=1', backing_file],
                   check=True)


def mount_supported_fs(backing_file, tgt):
    log.info('mounting a reflink-supported filesystem for image storage...')
    tgt_uid, tgt_gid = os.stat(tgt).st_uid, os.stat(tgt).st_gid
    subprocess.run(['sudo', 'mount', '-o', 'loop', backing_file, tgt],
                   check=True)
    mount_uid, mount_gid = os.stat(tgt).st_uid, os.stat(tgt).st_gid
    if (tgt_uid, tgt_gid) != (mount_uid, mount_gid):
        log.debug(f'fixing owner:group ({tgt_uid}:{tgt_gid})')
        subprocess.run(['sudo', 'chown', f'{tgt_uid}:{tgt_gid}', tgt],
                       check=True)
        if tgt.startswith('/home'):
            subprocess.run(['sudo', 'semanage', 'fcontext', '-a', '-t',
                            'user_home_dir_t', tgt + '(/.*)?'], check=False)
            subprocess.run(['sudo', 'restorecon', '-v', tgt], check=False)


def storage_setup_wizard():
    assert SETUP in ('auto', 'suggest', 'never')
    if SETUP == 'never':
        return
    size = SIZE
    os.makedirs(path.MACHINES, exist_ok=True)
    if not is_supported(path.MACHINES):
        log.warning(f'images directory {path.MACHINES} lacks reflink support')
        log.warning('without it, fingertip will thrash and fill up your SSD '
                    'in no time')
        if not os.path.exists(path.COW_IMAGE):
            if SETUP == 'suggest':
                log.info(f'would you like to allow fingertip '
                         f'to allocate {size} at {path.COW_IMAGE} '
                         'for a reflink-enabled XFS loop mount?')
                log.info('(set FINGERTIP_SETUP="auto" environment variable'
                         ' to do it automatically)')
                i = input(f'[{size}]/different size/cancel/ignore> ').strip()
                if i == 'cancel':
                    log.error('cancelled')
                    sys.exit(1)
                elif i == 'ignore':
                    return
                size = i or size
            tmp = temp.disappearing_file(os.path.dirname(path.COW_IMAGE))
            create_supported_fs(tmp, size)
            os.rename(tmp, path.COW_IMAGE)

        log.info(f'fingertip will now mount the XFS image at {path.COW_IMAGE}')
        if SETUP == 'suggest':
            i = input(f'[ok]/skip/cancel> ').strip()
            if i == 'skip':
                log.warning('skipping; '
                            'fingertip will have no reflink superpowers')
                log.warning('tell your SSD I\'m sorry')
                return
            elif i and i != 'ok':
                log.error('cancelled')
                sys.exit(1)

        mount_supported_fs(path.COW_IMAGE, path.CACHE)


def storage_unmount():
    log.plain()
    log.info(f'unmounting {path.CACHE} ...')
    subprocess.run(['sudo', 'umount', '-l', path.CACHE])
    log.nicer()


def storage_destroy():
    mount = subprocess.run(['mount'], capture_output=True)
    if path.COW_IMAGE in mount.stdout.decode():
        log.warning('Filesystem is still mounted. Trying to unmount.')
        storage_unmount()

    if os.path.exists(path.COW_IMAGE):
        os.unlink(path.COW_IMAGE)
