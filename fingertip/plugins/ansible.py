# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

"""
A plugin to execute Ansible playbooks and ad-hoc commands.

Ansible's Python API is not stable yet, so... CLI.
"""

import logging
import os
import subprocess

from fingertip.util import temp


def prepare(m):
    if not hasattr(m, '_ansible_prepare') and m.hooks.ansible_prepare:
        with m:
            m.hooks.ansible_prepare()
            m._ansible_prepare = True
    return m


def _ansible(m, *args, check=True, cmd=('ansible', 'fingertip')):
    env = {**os.environ, 'ANSIBLE_HOST_KEY_CHECKING': 'False'}
    if hasattr(m, 'ssh'):
        m.ssh.connect()  # to ensure correct spin-up, it has smarter timeouts
        m.log.info(f'ansible {args}')
        connection = 'ssh'  # TODO: compare with paramiko
        prefix = ()
        host = ['fingertip',
                'ansible_connection=ssh',
                'ansible_user=root',
                'ansible_host=localhost',
                f'ansible_port={m.ssh.port}',
                f'ansible_ssh_private_key_file={m.ssh.key_file}']
    elif m.backend == 'podman-criu':
        connection = 'podman'
        prefix = ('sudo', '-H')
        host = ['fingertip', f'ansible_host={m.container.container_id}']
    else:
        raise NotImplementedError()
    inventory = temp.disappearing_file(hint='ansible-inventory')
    with open(inventory, 'w') as f:
        f.write(' '.join(host))
    more_opts = ('-T', '120', '-i', inventory, '-c', connection)
    cmd = prefix + cmd + more_opts + args
    m.log.debug(' '.join(cmd))
    run = m.log.pipe_powered(subprocess.run,
                             stdout=logging.INFO, stderr=logging.INFO)
    return run(cmd, env=env, check=check)


def main(m, module, *args, **kwargs):
    def to_str(v):
        if isinstance(v, list) or isinstance(v, tuple):
            return ','.join(v)
        if isinstance(v, bool):
            return 'yes' if v else 'no'
        return str(v)
    module_args = args + tuple(f'{k}="{to_str(v)}"' for k, v in kwargs.items())
    with m.apply(prepare) as m:
        _ansible(m, '-m', module, '-a', ' '.join(module_args))
    return m


def playbook(m, playbook_path):
    with m.apply(prepare) as m:
        _ansible(m, playbook_path, cmd=('ansible-playbook',))
        # not comprehensive; best-effort
        m.expiration.depend_on_a_file(playbook_path)
    return m
