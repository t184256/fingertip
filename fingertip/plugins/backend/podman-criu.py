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
import fingertip.exec
import fingertip.machine
import fingertip.util.http_cache
from fingertip.util import log, reflink, repeatedly, weak_hash


def _podman(*args, func='run', **kwargs):
    log.info('podman ' + ' '.join(args))
    if func not in ('check_output', 'Popen'):
        kwargs['check'] = True
    func = getattr(subprocess, func)
    return func(('sudo', 'podman',) + args, **kwargs)


def _copy_ownership(p1, p2):
    uid, gid = os.stat(p1).st_uid, os.stat(p1).st_gid
    subprocess.run(['sudo', 'chown', f'{uid}:{gid}', p2], check=True)


def _base():
    m = fingertip.machine.Machine(sealed=False)  # TODO: seal
    m.backend = 'podman-criu'
    m.container = ContainerNamespacedFeatures(m)
    m.container.from_image = from_image

    def up():
        log.debug('up')
        m.container.name = 'fingertip_' + weak_hash.weak_hash(m.path)
        if not hasattr(m.container, 'starting_image'):  # no starting image yet
            return
        # need to load the initial image, checkpoint and create image.tar
        m.container.container_id = _podman(
                'container', 'restore', '-n', m.container.name,
                '-i', os.path.join(m.path, 'snapshot.tar'),
                func='check_output'
        ).decode().strip()
        assert m.container.container_id
        log.debug(f'restore -> container_id = {m.container.container_id}')
        m.console = pexpect.spawn('sudo', ['podman', 'attach',
                                           m.container.container_id],
                                  echo=False, timeout=None, encoding='utf-8',
                                  logfile=sys.stdout)
    m.hooks.up.append(up)

    def detach(retries=10, timeout=1/32):
        def dtch():
            time.sleep(.1)  # HACK, FIXME
            m.console.sendcontrol('p')
            time.sleep(.001)  # HACK
            m.console.sendcontrol('q')
            m.console.expect(pexpect.EOF, timeout=.1)
        repeatedly.keep_trying(dtch, pexpect.exceptions.TIMEOUT,
                               retries=retries, timeout=timeout)
        m.console.wait()
    m.hooks.detach.append(detach)

    def down():
        if not hasattr(m.container, 'starting_image'):  # no starting image yet
            return
        if m.console:
            m.hooks.detach()
        m.console = None
        _podman('container', 'checkpoint', m.container.container_id,
               '-e', os.path.join(m.path, 'snapshot.tar'))
        _copy_ownership(m.path, os.path.join(m.path, 'snapshot.tar'))
        _podman('rm', m.container.container_id)
        del m.container.container_id
        del m.container.name
    m.hooks.down.append(down)

    def drop():
        if not hasattr(m.container, 'starting_image'):  # no starting image yet
            return
        if m.console:
            m.hooks.detach()
        m.console = None
        _podman('stop', '-t', '0', m.container.container_id)
        _podman('rm', m.container.container_id)
        del m.container.container_id
        del m.container.name
    m.hooks.drop.append(drop)

    def clone(to):
        if not hasattr(m.container, 'starting_image'):  # no starting image yet
            return
        log.debug(f'{m} {to}')
        reflink.auto(os.path.join(m.path, 'snapshot.tar'),
                     os.path.join(to, 'snapshot.tar'))
    m.hooks.clone.append(clone)

    m.exec = m.container.exec

    return m


def main(image=None):
    m = fingertip.build(_base)
    if image:
        with m:
            m = from_image(m, image=image)
    return m


class ContainerNamespacedFeatures:
    def __init__(self, m):
        self._args = []

    def exec(self, *cmd, shell=False):
        cmd = ('sh', '-c', *cmd) if shell else cmd
        p = _podman('exec', self.container_id, *cmd,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    func='Popen')
        out, err, outerr = fingertip.exec.stream_out_and_err(
            p.stdout, p.stderr, sys.stdout.buffer
        )
        p.wait()
        if p.returncode:  # HACK: ugly workaround for podman polluting stderr
            m = (f'Error: non zero exit code: {p.returncode}:'
                 ' OCI runtime error\n').encode()
            if err.endswith(m) and outerr.endswith(m):
                err = err[:-len(m)]
                outerr = outerr[:-len(m)]
        return fingertip.exec.ExecResult(p.returncode, out, err, outerr)


def from_image(m=None, image=None, cmd=[]):
    m = m or fingertip.build(main)
    with m:
        m.container.name = 'fingertip_' + weak_hash.weak_hash(m.path)
        m.container.starting_image = image
        m.container.container_id = _podman(
                'run', '-dit', '--name', m.container.name, *m.container._args,
                image, *cmd,
                func='check_output'
        ).decode().strip()
        log.debug(f'run -> container_id = {m.container.container_id}')
        _podman('container', 'checkpoint', m.container.container_id,
                '-e', os.path.join(m.path, 'snapshot.tar'))
        _copy_ownership(m.path, os.path.join(m.path, 'snapshot.tar'))
        _podman('container', 'rm', m.container.container_id)
        m.hooks.up()
        m.console.sendline('')
        m.console.expect_exact('\r\n')
        m.console.sendline('')
        m.console.expect_exact('\r\n')
        m.prompt = m.console.before
        log.info(f'm.prompt: "{m.prompt}"')
        return m
