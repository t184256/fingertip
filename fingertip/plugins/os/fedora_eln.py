# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2022 Red Hat, Inc., see CONTRIBUTORS.

import os
import re

import fingertip.machine
from fingertip.util import path
from fingertip.plugins.os.common import red_hat_based

URL = ('http://odcs.fedoraproject.org/composes/production/latest-Fedora-ELN/'
       'compose')
BUILDROOT = 'https://kojipkgs.fedoraproject.org/repos/eln-build/latest'
HOSTNAME = 'Fedora-ELN'
NEXT_RHEL = 10


def _url(reponame='BaseOS', kind='', arch='x86_64'):
    if reponame == 'buildroot':
        return f'{BUILDROOT}/{arch}'
    kinds = {'': f'{arch}/os',
             'debuginfo': f'{arch}/debug/tree',
             'source': 'source/tree'}
    return (f'{URL}/{reponame}/{kinds[kind]}')


def main(m=None, extra_cmdline=''):
    m = m or fingertip.build('backend.qemu')
    if hasattr(m, 'qemu'):
        return m.apply(install_in_qemu, extra_cmdline=extra_cmdline)
    raise NotImplementedError()


def install_in_qemu(m=None, extra_cmdline=''):
    repos = ''
    for reponame in 'BaseOS', 'AppStream', 'CRB', 'Extras':
        for kind in ('', 'debuginfo', 'source'):
            name = f'ELN-{reponame}' + (f'-{kind}' if kind else '')
            url = _url(reponame, kind)
            repos += f'repo --name={name} --baseurl={url} --install\n'

    m = m or fingertip.machine.build('backend.qemu')
    base_url = _url()

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
        m.log.info(f'fetching kernel: {base_url}/images/pxeboot/vmlinuz')
        kernel = os.path.join(m.path, 'kernel')
        m.http_cache.fetch(f'{base_url}/images/pxeboot/vmlinuz', kernel)
        m.log.info(f'fetching initrd: {base_url}/images/pxeboot/initrd.img')
        initrd = os.path.join(m.path, 'initrd')
        m.http_cache.fetch(f'{base_url}/images/pxeboot/initrd.img', initrd)
        append = ('inst.ks=http://mock/ks inst.ksstrict console=ttyS0 '
                  'inst.zram=off '
                  'inst.text inst.notmux inst.cmdline '
                  f'proxy={m.http_cache.internal_url} ' +
                  f'inst.proxy={m.http_cache.internal_url} ' +
                  f'inst.repo={base_url} ' +
                  extra_cmdline)
        extra_args = ['-kernel', kernel, '-initrd', initrd, '-append', append]

        m.ram.safeguard = m.ram.max  # fix to 1536M when virtio-ballon is fixed
        with m.ram('>=4G'):
            m.expiration.cap('2d')  # non-immutable repositories
            m.qemu.run(load=None, extra_args=extra_args)
            m.console.expect('Complete!')
            m.qemu.wait()

        # first boot
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
        m.hooks.unseal += red_hat_based.unseal_networkmanager(m) + [hack_resolv_conf]
        m.hooks.timesync += red_hat_based.timesync(m)
        m.hooks.wait_for_running += red_hat_based.wait_for_running_systemd(m)

        m = m.apply('ansible', 'yum_repository', enabled=False, gpgcheck=False,
                    name='eln-koji-buildroot',
                    description='eln-koji-buildroot',
                    baseurl=_url(reponame='buildroot'))

        for reponame in 'BaseOS', 'AppStream', 'CRB', 'Extras':
            for kind in ('', 'debuginfo', 'source'):
                _kind = f'-{kind}' if kind else ''
                m("echo 'gpgkey = file:///etc/pki/rpm-gpg/"
                  "RPM-GPG-KEY-fedora-eln-$basearch'"
                  f" >> /etc/yum.repos.d/ELN-{reponame}{_kind}.repo")

        m.rhel = NEXT_RHEL
        m.fedora_eln = True
        m.dist_git_branch = 'eln'

        red_hat_based.proxy_dnf(m)

    return m
