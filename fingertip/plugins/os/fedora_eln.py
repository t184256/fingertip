# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2022 Red Hat, Inc., see CONTRIBUTORS.

import os
import re

import fingertip.machine
from fingertip.util import path
from fingertip.plugins.os.common import red_hat_based

URL = ('http://odcs.fedoraproject.org/composes/production/latest-Fedora-ELN/'
       'compose/Everything')
HOSTNAME = 'Fedora-ELN'
NEXT_RHEL = 10


def _url(kind='', arch='x86_64'):
    kinds = {'': f'{arch}/os',
             'debuginfo': f'{arch}/debug/tree',
             'source': 'source/tree'}
    return (f'{URL}/{kinds[kind]}')


def main(m=None, extra_cmdline=''):
    m = m or fingertip.build('backend.qemu')
    if hasattr(m, 'qemu'):
        return m.apply(install_in_qemu, extra_cmdline=extra_cmdline)
    raise NotImplementedError()


def install_in_qemu(m=None, extra_cmdline=''):
    repos = ''
    for kind in ('', 'debuginfo', 'source'):
        name = f'ELN-Everything' + (f'-{kind}' if kind else '')
        url = _url(kind)
        repos += f'repo --name={name} --baseurl={url} --install\n'
    # https://github.com/fedora-eln/eln/issues/87
    repos += repos.replace('Everything', 'BaseOS')

    m = m or fingertip.machine.build('backend.qemu')
    base_url = _url()
    # https://github.com/fedora-eln/eln/issues/87
    base_url = base_url.replace('Everything', 'BaseOS')

    with m:
        ssh_key_path = path.fingertip('ssh_key', 'fingertip.pub')
        with open(ssh_key_path) as f:
            ssh_pubkey = f.read().strip()
        m.expiration.depend_on_a_file(ssh_key_path)

        ks = path.fingertip('fingertip', 'plugins', 'os', 'fedora_eln.ks')
        fqdn = HOSTNAME + '.fingertip.local'

        with open(ks) as f:
            ks_text = f.read().format(HOSTNAME=fqdn,
                                      SSH_PUBKEY=ssh_pubkey,
                                      PROXY=m.http_cache.internal_url,
                                      REPOS=repos)
        m.expiration.depend_on_a_file(ks)

        m.http_cache.mock('http://mock/ks', text=ks_text)
        m.log.info(f'fetching kernel: {base_url}/isolinux/vmlinuz')
        kernel = os.path.join(m.path, 'kernel')
        m.http_cache.fetch(f'{base_url}/isolinux/vmlinuz', kernel)
        m.log.info(f'fetching initrd: {base_url}/isolinux/initrd.img')
        initrd = os.path.join(m.path, 'initrd')
        m.http_cache.fetch(f'{base_url}/isolinux/initrd.img', initrd)
        append = ('inst.ks=http://mock/ks inst.ksstrict console=ttyS0 '
                  'inst.zram=off '
                  'inst.text inst.notmux inst.cmdline '
                  f'proxy={m.http_cache.internal_url} ' +
                  f'inst.proxy={m.http_cache.internal_url} ' +
                  f'inst.repo={base_url} ' +
                  extra_cmdline)
        extra_args = ['-kernel', kernel, '-initrd', initrd, '-append', append]

        m.ram.safeguard = '1536M'
        with m.ram('>=4G'):
            m.expiration.cap('1d')  # non-immutable repositories
            m.qemu.run(load=None, extra_args=extra_args)
            m.console.expect('Installation complete')
            m.qemu.wait()

        # https://github.com/fedora-eln/eln/issues/88#issuecomment-1117015345
        # hacky fixup
        m.ram.min = '4G'
        m.qemu.run(load=None)
        m.console.expect('Give root password for maintenance')
        m.console.sendline('fingertip')
        m.console.expect(':/root# ')
        m.console.sendline('mkdir /mnt')
        m.console.expect(':/root# ')
        m.console.sendline('mount /dev/vda3 /mnt; mount /dev/vda1 /mnt/boot')
        m.console.expect(':/root# ')
        for d in 'proc', 'dev', 'sys':
            m.console.sendline(f'mount --bind /{d} /mnt/{d}')
            m.console.expect(':/root# ')
        m.console.sendline('chroot /mnt')
        m.console.expect(':/# ')
        m.console.sendline('grubby --info=ALL')
        m.console.expect(':/# ')
        m.console.sendline('. /etc/default/grub')
        m.console.expect(':/# ')
        for a in append.split():
            if a == 'console=ttyS0' or a in extra_cmdline:
                continue
            m.console.sendline(f'grubby --update-kernel=ALL --remove-args {a}')
            m.console.expect(':/# ')
        m.console.sendline('grubby --update-kernel=ALL '
                           '       --args $GRUB_CMDLINE_LINUX')
        m.console.sendline('grubby --update-kernel=ALL --args root=/dev/vda3')
        m.console.expect(':/# ')
        m.console.sendline('grubby --info=ALL')
        m.console.expect(':/# ')
        m.console.sendline('exit')
        m.console.expect(':/root# ')
        for d in 'proc', 'dev', 'sys':
            m.console.sendline(f'umount /mnt/{d}')
            m.console.expect(':/root# ')
        m.console.sendline('umount /mnt/boot; umount /mnt')
        m.console.expect(':/root# ')
        m.console.sendline('poweroff')
        m.qemu.wait()
        m.ram.min = '2G'

        # second boot, first proper boot
        m.qemu.run(load=None)

        def login(username='root', password='fingertip'):
            if username == 'root':
                m.prompt = f'[root@{HOSTNAME} ~]# '
            else:
                m.prompt = f'[{username}@{HOSTNAME} ~]$ '
            m.console.expect(f'{HOSTNAME} login: ')
            m.console.sendline(username)
            m.console.expect('Password: ')
            m.console.sendline(password)
            m.console.expect_exact(m.prompt)

        m.login = login

        m.login()

        m.log.info('Fedora ELN installation finished')

        # https://bugzilla.redhat.com/show_bug.cgi?id=1957294#c7
        def hack_resolv_conf():
            m(r'''
                DNS=$(nmcli device show | grep DNS | head -n1 | sed 's/.*\s//')
                rm -f /etc/resolv.conf
                echo "nameserver $DNS" > /etc/resolv.conf
                restorecon /etc/resolv.conf
                mv /etc/resolv.conf /etc/resolv.conf.hack
                ln -sf /etc/resolv.conf.hack /etc/resolv.conf
            ''')
        m.hooks.unseal += [lambda: m('systemctl restart NetworkManager'),
                           lambda: m('nm-online'),
                           hack_resolv_conf]
        m.hooks.timesync.append(lambda: m('hwclock -s'))

        m.rhel = NEXT_RHEL
        m.fedora_eln = True
        m.dist_git_branch = 'eln'

        red_hat_based.proxy_dnf(m)

    return m
