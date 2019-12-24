# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import os

import fingertip
from fingertip.util import path


MIRROR = 'http://dl-cdn.alpinelinux.org/alpine'
ISO = MIRROR + '/v3.10/releases/x86_64/alpine-virt-3.10.3-x86_64.iso'
REPO = MIRROR + '/v3.10/main/'


def install(m):
    iso_file = os.path.join(m.path, os.path.basename(ISO))
    m.http_cache.fetch(ISO, iso_file)

    with m:
        m.qemu.run(load=None, extra_args=['-cdrom', iso_file])
        m.console.expect_exact('localhost login: ')
        m.console.sendline('root')
        m.console.expect_exact('localhost:~# ')

        m.hostname = 'alpine'
        m.prompt = f'{m.hostname}:~# '

        m.console.sendline('setup-alpine -q')
        m.console.expect_exact('Select keyboard layout [none]:')
        m.console.sendline('us')
        m.console.expect_exact('Select variant []:')
        m.console.sendline('us')
        m.console.expect_exact(m.prompt)

        m.console.sendline(f'setup-proxy {m.http_cache.internal_url}')
        m.console.expect_exact(m.prompt)
        m.console.sendline('. /etc/profile.d/proxy.sh')
        m.console.expect_exact(m.prompt)

        m.console.sendline(f'setup-apkrepos {REPO}')
        m.console.expect_exact(m.prompt)

        m.console.sendline('setup-sshd -c openssh')
        m.console.expect_exact(m.prompt)

        m.console.sendline('setup-disk -m sys /dev/vda')
        m.console.expect_exact('Erase the above disk(s) and continue? [y/N]:')
        m.console.sendline('y')
        m.console.expect_exact(m.prompt)

        m.console.sendline('fstrim -v /')
        m.console.expect_exact(m.prompt)

        m.console.sendline('poweroff')

        m.qemu.wait()
        m.qemu.compress_image()
        return m


def first_boot(m):
    with open(path.fingertip('ssh_key', 'fingertip.pub')) as f:
        ssh_pubkey = f.read().strip()

    with m:
        m.qemu.run(load=None)
        m.console.expect_exact(f'{m.hostname} login: ')
        m.console.sendline('root')
        m.console.expect_exact(m.prompt)

        m.console.sendline(f'install -m 700 -d .ssh')
        m.console.expect_exact(m.prompt)
        m.console.sendline(f'echo "{ssh_pubkey}" >> .ssh/authorized_keys')
        m.console.expect_exact(m.prompt)

        m.hook(unseal=unseal)

        return m


def main(m=None):
    m = m or fingertip.build('backend.qemu', ram_size='128M')
    if hasattr(m, 'qemu'):
        return m.apply(install).apply(first_boot)
    else:
        # podman-criu: https://github.com/checkpoint-restore/criu/issues/596
        raise NotImplementedError()


def unseal(m):
    with m:
        m.console.sendline(f'unset http_proxy https_proxy ftp_proxy')
        m.console.expect_exact(m.prompt)
        m.ssh('rm /etc/profile.d/proxy.sh')
        m.ssh('/sbin/setup-proxy none || true')
        m.ssh('/etc/init.d/networking restart')
        return m
