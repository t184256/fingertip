# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import os

import fingertip
from fingertip.util import path


MIRROR = 'http://dl-cdn.alpinelinux.org/alpine'
ISO = MIRROR + '/v3.12/releases/x86_64/alpine-virt-3.12.0-x86_64.iso'
REPO = MIRROR + '/v3.12/main/'


def main(m=None):
    m = m or fingertip.build('backend.qemu', ram_size='128M')
    if hasattr(m, 'qemu'):
        m = m.apply(install_in_qemu).apply(first_boot)
    elif hasattr(m, 'container'):
        # podman-criu: https://github.com/checkpoint-restore/criu/issues/596
        m = m.apply(m.container.from_image, 'alpine')
    else:
        raise NotImplementedError()

    with m:
        def prepare():
            m('apk add python3')
        m.hooks.ansible_prepare.append(prepare)
        #m.hooks.ansible_prepare.append(lambda: m('apk add python3'))
    return m


def install_in_qemu(m):
    iso_file = os.path.join(m.path, os.path.basename(ISO))
    m.http_cache.fetch(ISO, iso_file)

    with m, m.ram('512M'):
        m.ram.safeguard = '256M'  # alpine is quite a slim distro

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

        fqdn = m.hostname + '.fingertip.local'
        m.console.sendline(f'echo "{m.hostname}" > /etc/hostname')
        m.console.expect_exact(m.prompt)
        m.console.sendline('hostname -F /etc/hostname')
        m.console.expect_exact(m.prompt)
        m.console.sendline(f'echo "127.0.0.1 {fqdn} {m.hostname}" > /etc/hosts')
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

        def disable_proxy():
            m('setup-proxy none', check=False)
            m.console.sendline(f'unset http_proxy https_proxy ftp_proxy')
            m.console.expect_exact(m.prompt)
        m.hooks.disable_cache.append(disable_proxy)

        return m


def first_boot(m):
    ssh_key_fname = path.fingertip('ssh_key', 'fingertip.pub')
    with open(ssh_key_fname) as f:
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
        m.expiration.depend_on_a_file(ssh_key_fname)

        m.hooks.unseal.append(lambda: m('/etc/init.d/networking restart'))
        m.hooks.timesync.append(lambda: m('hwclock -s'))
    return m
