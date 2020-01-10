# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

"""
A plugin to execute Ansible playbooks and ad-hoc commands.

Ansible's Python API is not stable yet, so... CLI.
"""

import os
import subprocess

from fingertip.util import log, temp


def _ansible(m, *args, check=True, cmd=('ansible', 'fingertip')):
    env = {**os.environ, 'ANSIBLE_HOST_KEY_CHECKING': 'False'}
    if hasattr(m, 'ssh'):
        hostline = ' '.join(['fingertip',
                             'ansible_connection=ssh',
                             'ansible_user=root',
                             'ansible_host=localhost',
                             f'ansible_port={m.ssh.port}',
                             f'ansible_ssh_private_key_file={m.ssh.key_file}'])
    else:
        raise NotImplementedError()
    inventory = temp.disappearing_file(hint='ansible-inventory')
    with open(inventory, 'w') as f:
        f.write(hostline)
    cmd = cmd + ('-i', inventory) + args
    log.info(' '.join(cmd))
    return subprocess.run(cmd, env=env, check=check)


def main(m, module, *args, **kwargs):
    module_args = args + tuple(f'{k}={v}' for k, v in kwargs.items())
    with m:
        _ansible(m, '-m', module, '-a', ' '.join(module_args))
    return m


def playbook(m, playbook_path):
    with m:
        _ansible(m, playbook_path, cmd=('ansible-playbook',))
    return m
