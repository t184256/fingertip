# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import requests

import os

import fingertip.machine
from fingertip.util import path


FEDORA_GEOREDIRECTOR = 'http://download.fedoraproject.org/pub/fedora/linux'


def determine_mirror():
    h = requests.head(FEDORA_GEOREDIRECTOR, allow_redirects=False)
    if h.status_code == 302 and 'Location' in h.headers:
        return h.headers['Location'].rstrip('/')
    return FEDORA_GEOREDIRECTOR


def main(m=None, version=31):
    m = m or fingertip.build('backend.qemu')
    if hasattr(m, 'qemu'):
        return m.apply(install_in_qemu, version=version)
    elif hasattr(m, 'container'):
        return m.apply(m.container.from_image, f'fedora:{version}')
    else:
        raise NotImplementedError()


def install_in_qemu(m, version):
    mirror = determine_mirror()
    m.log.info(f'selected mirror: {mirror}')

    fedora_url = mirror + f'/releases/{version}/Server/x86_64/os'
    original_ram_size = m.qemu.ram_size

    with m:
        m.qemu.ram_size = '2G'

        ssh_key_fname = path.fingertip('ssh_key', 'fingertip.pub')
        with open(ssh_key_fname) as f:
            ssh_pubkey = f.read().strip()
        m.expiration.depend_on_a_file(ssh_key_fname)

        ks_fname = path.fingertip('kickstart_templates', f'fedora{version}')
        with open(ks_fname) as f:
            ks_text = f.read().format(HOSTNAME=f'fedora{version}',
                                      SSH_PUBKEY=ssh_pubkey,
                                      PROXY=m.http_cache.internal_url)
        m.expiration.depend_on_a_file(ks_fname)

        m.http_cache.mock('http://ks', text=ks_text)
        m.log.info(f'fetching kernel: {fedora_url}/isolinux/vmlinuz')
        kernel = os.path.join(m.path, 'kernel')
        m.http_cache.fetch(f'{fedora_url}/isolinux/vmlinuz', kernel)
        m.log.info(f'fetching initrd: {fedora_url}/isolinux/initrd.img')
        initrd = os.path.join(m.path, 'initrd')
        m.http_cache.fetch(f'{fedora_url}/isolinux/initrd.img', initrd)
        append = ('ks=http://ks inst.ksstrict console=ttyS0 inst.notmux '
                  f'proxy={m.http_cache.internal_url} ' +
                  f'inst.proxy={m.http_cache.internal_url} ' +
                  f'inst.repo={fedora_url}')
        extra_args = ['-kernel', kernel, '-initrd', initrd, '-append', append]

        m.qemu.run(load=None, extra_args=extra_args)
        m.console.expect('Storing configuration files and kickstarts')
        m.qemu.wait()
        m.qemu.compress_image()
        m.qemu.ram_size = original_ram_size
        m.qemu.run(load=None)  # cold boot
        HOSTNAME = 'fedora31'
        ROOT_PASSWORD = 'fingertip'
        m.prompt = f'[root@{HOSTNAME} ~]# '
        m.console.expect(f'{HOSTNAME} login: ')
        m.console.sendline('root')
        m.console.expect('Password: ')
        m.console.sendline(ROOT_PASSWORD)
        m.console.expect_exact(m.prompt)
        m.log.info('Fedora installation finished')

        def disable_proxy():
            return m.apply('ansible', 'ini_file', path='/etc/dnf/dnf.conf',
                           section='main', option='proxy', state='absent')
        m.hooks.disable_proxy.append(disable_proxy)

        m.hooks.unseal += [lambda: m('systemctl restart NetworkManager'),
                           lambda: m('nm-online')]

        m.fedora = version

        return m
