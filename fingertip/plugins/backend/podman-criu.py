# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

# setsebool -P container_manage_cgroup true

import os
import sys
import time
import subprocess

import pexpect

# TODO: sealing
# TODO: http proxy for podman
# TODO: http proxy for container itself
import fingertip.machine
import fingertip.util.http_cache
from fingertip.util import log, reflink, repeatedly, weak_hash


def podman(*args, check=True, **kwargs):
    log.info('podman ' + ' '.join(args))
    return subprocess.run(('sudo', 'podman',) + args,
                          check=check, **kwargs)


def copy_ownership(p1, p2):
    uid, gid = os.stat(p1).st_uid, os.stat(p1).st_gid
    subprocess.run(['sudo', 'chown', f'{uid}:{gid}', p2], check=True)


def base():
    m = fingertip.machine.Machine(sealed=False)  # TODO: seal
    m.backend = 'podman-criu'
    m.container = ContainerNamespacedFeatures(m)
    m.container.from_image = from_image
    m.hooks(load=_load, up=_up, down=_down, drop=_drop, save=_save,
            clone=_clone)
    return m


def main(image=None):
    m = fingertip.build(base)
    if image:
        with m:
            m = from_image(m, image=image)
    return m


class ContainerNamespacedFeatures:
    def __init__(self, m):
        self._args = []

    def exec(self, cmd, nocheck=False, shell=True):
        if shell:
            cmd = ['sh', '-c', cmd]
        # TODO: stream
        p = podman('exec', '-it', self.container_id, *cmd,
                   stderr=subprocess.STDOUT, stdout=subprocess.PIPE,
                   check=False)
        ret, out = p.returncode, p.stdout.decode()
        log.info(f'podman exec output: {out.rstrip()}')
        log.info(f'podman exec retcode: {ret}')
        if nocheck:
            return ret, out
        assert ret == 0
        return out


def from_image(m=None, image=None, cmd=[]):
    m = m or fingertip.build(main)
    with m:
        m.container.name = 'fingertip_' + weak_hash.weak_hash(m.path)
        m.container.starting_image = image
        cmd = (['sudo', 'podman', 'run', '-dit'] +
               ['--name', m.container.name] +
               m.container._args + [image] + cmd)
        log.info(str(cmd))
        time.sleep(10)
        m.container.container_id = subprocess.check_output(cmd).decode().strip()
        log.debug(f'run -> container_id = {m.container.container_id}')
        podman('container', 'checkpoint', m.container.container_id,
               '-e', os.path.join(m.path, 'snapshot.tar'))
        copy_ownership(m.path, os.path.join(m.path, 'snapshot.tar'))
        podman('container', 'rm', m.container.container_id)
        _up(m)
        m.console.sendline('')
        m.console.expect_exact('\r\n')
        m.console.sendline('')
        m.console.expect_exact('\r\n')
        m.prompt = m.console.before
        log.info(f'm.prompt: "{m.prompt}"')
        return m


def _load(m):
    pass


def _up(m):
    log.debug('up')
    m.container.name = 'fingertip_' + weak_hash.weak_hash(m.path)
    if not hasattr(m.container, 'starting_image'):  # no starting image yet
        return
    # need to load the initial image, checkpoint and create image.tar
    cmd = (['sudo', 'podman', 'container', 'restore', '-n', m.container.name,
           '-i', os.path.join(m.path, 'snapshot.tar')])
    m.container.container_id = subprocess.check_output(cmd).decode().strip()
    assert m.container.container_id
    log.debug(f'restore -> container_id = {m.container.container_id}')
    m.console = pexpect.spawn('sudo',
                              ['podman', 'attach', m.container.container_id],
                              echo=False, timeout=None, encoding='utf-8',
                              logfile=sys.stdout)


def _detach(m, retries=10, timeout=1/32):
    def dtch():
        time.sleep(.1)  # HACK, FIXME
        m.console.sendcontrol('p')
        time.sleep(.001)  # HACK
        m.console.sendcontrol('q')
        m.console.expect(pexpect.EOF, timeout=.1)
    repeatedly.keep_trying(dtch, pexpect.exceptions.TIMEOUT,
                           retries=retries, timeout=timeout)
    m.console.wait()


def _down(m):
    if not hasattr(m.container, 'starting_image'):  # no starting image yet
        return
    if m.console:
        _detach(m)
    m.console = None
    podman('container', 'checkpoint', m.container.container_id,
           '-e', os.path.join(m.path, 'snapshot.tar'))
    copy_ownership(m.path, os.path.join(m.path, 'snapshot.tar'))
    podman('rm', m.container.container_id)
    del m.container.container_id
    del m.container.name


def _drop(m):
    if not hasattr(m.container, 'starting_image'):  # no starting image yet
        return
    if m.console:
        _detach(m)
    m.console = None
    podman('stop', '-t', '0', m.container.container_id)
    podman('rm', m.container.container_id)
    del m.container.container_id
    del m.container.name


def _save(m):
    pass


def _clone(m, to):
    if not hasattr(m.container, 'starting_image'):  # no starting image yet
        return
    log.debug(f'{m} {to}')
    reflink.auto(os.path.join(m.path, 'snapshot.tar'),
                 os.path.join(to, 'snapshot.tar'))

def exec(m, cmd):
    with m:
        m.container.exec(cmd)
    return m
