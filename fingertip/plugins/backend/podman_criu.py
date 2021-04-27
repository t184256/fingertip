# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

# setsebool -P container_manage_cgroup true

import logging
import os
import subprocess
import sys
import time

import pexpect

# TODO: sealing
# TODO: http proxy for podman
# TODO: http proxy for container itself
import fingertip.exec
import fingertip.machine
import fingertip.util.http_cache
from fingertip.util import reflink, repeatedly, weak_hash


def _podman(log, *args, func='run', **kwargs):
    if func not in ('check_output', 'Popen'):
        kwargs['check'] = True
    redirects = {}
    if func != 'check_output' and 'stdout' not in kwargs:
        redirects['stdout'] = logging.INFO
    if 'stderr' not in kwargs:
        redirects['stderr'] = logging.ERROR
    func = getattr(subprocess, func)
    func = log.pipe_powered(func, **redirects)
    return func(('sudo', 'podman',) + args, **kwargs)


def _copy_ownership(log, p1, p2):
    uid, gid = os.stat(p1).st_uid, os.stat(p1).st_gid
    run = log.pipe_powered(subprocess.run,
                           stdout=logging.INFO, stderr=logging.ERROR)
    run(['sudo', 'chown', f'{uid}:{gid}', p2], check=True)


def _base():
    # TODO: seal
    m = fingertip.machine.Machine('podman-criu', sealed=False, expire_in='4h')
    m.container = ContainerNamespacedFeatures(m)
    m.container.from_image = from_image
    m._backend_mode = 'pexpect'

    def up():
        m.log.debug('up')
        m.container.name = 'fingertip_' + weak_hash.of_string(m.path)
        if not hasattr(m.container, 'starting_image'):  # no starting image yet
            return
        # need to load the initial image, checkpoint and create image.tar
        m.container.container_id = _podman(
                m.log,
                'container', 'restore', '-n', m.container.name,
                '-i', os.path.join(m.path, 'snapshot.tar'),
                func='check_output'
        ).decode().strip()
        assert m.container.container_id
        m.log.debug(f'restore -> container_id = {m.container.container_id}')
        if m._backend_mode == 'pexpect':
            pexp = m.log.pseudofile_powered(pexpect.spawn,
                                            logfile=logging.INFO)
            m.console = pexp('sudo', ['podman', 'attach',
                                      m.container.container_id],
                             echo=False, timeout=None,
                             encoding='utf-8', codec_errors='ignore')
        elif m._backend_mode == 'direct':
            subprocess.run(['sudo', 'podman', 'attach',
                           m.container.container_id])
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
        _podman(m.log, 'container', 'checkpoint', m.container.container_id,
                '-e', os.path.join(m.path, 'snapshot.tar'))
        _copy_ownership(m.log, m.path, os.path.join(m.path, 'snapshot.tar'))
        _podman(m.log, 'rm', m.container.container_id)
        del m.container.container_id
        del m.container.name
    m.hooks.down.append(down)

    def drop():
        if not hasattr(m.container, 'starting_image'):  # no starting image yet
            return
        if m.console:
            m.hooks.detach()
        m.console = None
        _podman(m.log, 'stop', '-t', '0', m.container.container_id)
        _podman(m.log, 'rm', m.container.container_id)
        del m.container.container_id
        del m.container.name
    m.hooks.drop.append(drop)

    def clone(to):
        if not hasattr(m.container, 'starting_image'):  # no starting image yet
            return
        m.log.debug(f'{m} {to}')
        reflink.auto(os.path.join(m.path, 'snapshot.tar'),
                     os.path.join(to, 'snapshot.tar'))
    m.hooks.clone.append(clone)

    def attach_exec():
        m.exec = m.container.exec
    m.hooks.up.append(attach_exec)  # FIXME self_test.exec, I blame cloudpickle

    return m


def main(image=None):
    m = fingertip.build(_base)
    if image:
        m = m.apply(from_image, image)
    return m


class ContainerNamespacedFeatures:
    def __init__(self, m):
        self._args = []
        self.m = m

    def exec(self, *cmd, shell=False):
        cmd = ('sh', '-c', *cmd) if shell else cmd
        stdout = self.m.log.make_pipe(level=logging.INFO)
        stderr = self.m.log.make_pipe(level=logging.INFO)
        try:
            p = _podman(self.m.log, 'exec', self.container_id, *cmd,
                        stdout=stdout, stderr=stderr, func='Popen')
            p.wait()
        finally:
            stdout.close()
            stderr.close()
            stdout.wait()
            stderr.wait()
        out, err = stdout.data, stderr.data
        if p.returncode:  # HACK: ugly workaround for podman polluting stderr
            m = ('Error: exec session exited with non-zero exit code '
                 f'{p.returncode}: OCI runtime error\n').encode()
            if err.endswith(m):
                err = err[:-len(m)]
        return fingertip.exec.ExecResult(p.returncode, out, err)


def from_image(m=None, image=None, cmd=[]):
    m = m or fingertip.build(main)
    with m:
        m.container.name = 'fingertip_' + weak_hash.of_string(m.path)
        m.container.starting_image = image
        m.container.container_id = _podman(
                m.log,
                'run', '-dit', '--name', m.container.name, *m.container._args,
                image, *cmd,
                func='check_output'
        ).decode().strip()
        m.log.debug(f'run -> container_id = {m.container.container_id}')
        _podman(m.log, 'container', 'checkpoint', m.container.container_id,
                '-e', os.path.join(m.path, 'snapshot.tar'))
        _copy_ownership(m.log, m.path, os.path.join(m.path, 'snapshot.tar'))
        _podman(m.log, 'container', 'rm', m.container.container_id)
        m.hooks.up()
        m.console.sendline(' echo TEST')
        m.console.expect_exact('echo TEST\r\n')
        m.console.expect_exact('TEST\r\n')
        m.console.sendline('')
        m.console.expect_exact('\r\n')
        m.console.sendline('')
        m.console.expect_exact('\r\n')
        m.prompt = m.console.before
        m.log.debug(f'm.prompt: "{m.prompt}"')
        m.console.sendline('')
        m.console.expect_exact(m.prompt)
        return m
