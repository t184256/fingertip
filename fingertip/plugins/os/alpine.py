# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import os

import fingertip
from fingertip.util import path


MIRROR = 'http://dl-cdn.alpinelinux.org/alpine'
ISO = MIRROR + '/v3.14/releases/x86_64/alpine-virt-3.14.2-x86_64.iso'
REPO = MIRROR + '/v3.14/main/'


def main(m=None):
    m = m or fingertip.build('backend.qemu', ram_min='256M', ram_size='512M')
    if hasattr(m, 'qemu'):
        m = m.apply(install_in_qemu).apply(first_boot)
    elif hasattr(m, 'container'):
        # podman-criu: https://github.com/checkpoint-restore/criu/issues/596
        m = m.apply(m.container.from_image, 'alpine')
    else:
        raise NotImplementedError()

    with m:
        m.hooks.ansible_prepare.append(lambda: m('apk add python3'))

        m.hooks.wait_for_running.append(
            lambda: m('while [ "$(rc-status --runlevel)" != "default" ]; do sleep 1; done')
        )
    return m


def install_in_qemu(m):
    iso_file = os.path.join(m.path, os.path.basename(ISO))
    m.http_cache.fetch(ISO, iso_file)

    ssh_key_fname = path.fingertip('ssh_key', 'fingertip.pub')
    with open(ssh_key_fname) as f:
        ssh_pubkey = f.read().strip()

    with m, m.ram('512M'):
        m.ram.safeguard = '256M'  # alpine is quite a slim distro
        m.qemu.run(load=None, extra_args=['-cdrom', iso_file])
        m.console.expect_exact('localhost login: ')
        m.console.sendline('root')
        m.console.expect_exact('localhost:~# ')

        m.hostname = 'alpine'
        m.prompt = f'{m.hostname}:~# '

        m.console.sendline('setup-alpine -q')
        m.console.expect_exact('Select keyboard layout: [none]')
        m.console.sendline('us')
        m.console.expect_exact("Select variant (or 'abort'):")
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
        m.console.expect_exact('Erase the above disk(s) and continue? (y/n)')
        m.console.sendline('y')
        m.console.expect_exact(m.prompt)

        m.console.sendline('mount /dev/vda3 /mnt')
        m.console.expect_exact(m.prompt)

        m.console.sendline('chroot /mnt apk add openssh')
        m.console.expect_exact(m.prompt)
        m.console.sendline('echo SetEnv'
                           f' http_proxy={m.http_cache.internal_url} '
                           '>> /mnt/etc/ssh/sshd_config')
        m.console.expect_exact(m.prompt)
        m.console.sendline('install -m 700 -d /mnt/root/.ssh')
        m.console.expect_exact(m.prompt)
        m.console.sendline(f'echo "{ssh_pubkey}" >> '
                           '/mnt/root/.ssh/authorized_keys')
        m.console.expect_exact(m.prompt)
        m.expiration.depend_on_a_file(ssh_key_fname)

        m.console.sendline('fstrim -v /mnt')
        m.console.expect_exact(m.prompt)

        m.console.sendline('poweroff')

        m.qemu.wait()
        os.unlink(iso_file)

        def disable_proxy():
            m('''
              setup-proxy none || :
              sed -i '/^SetEnv http_proxy=.*/d' /etc/ssh/sshd_config || :
              service sshd restart
            ''')
            m.console.sendline(' unset http_proxy https_proxy ftp_proxy')
            m.console.expect_exact(m.prompt)
        m.hooks.disable_proxy.append(disable_proxy)

        def login():
            m.console.expect_exact(f'{m.hostname} login: ')
            m.console.sendline('root')
            m.console.expect_exact(m.prompt)

        m.login = login

        m.hooks.unseal.append(lambda: m('/etc/init.d/networking restart'))
        m.hooks.timesync.append(lambda: m('hwclock -s'))

        return m


def first_boot(m):
    with m:
        m.qemu.run(load=None)
        m.login()
    return m
