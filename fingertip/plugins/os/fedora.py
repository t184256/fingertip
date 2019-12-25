# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import os

import fingertip.machine
from fingertip.util import log, path


FEDORA_MIRROR = 'http://download.fedoraproject.org/pub/fedora'


def install_in_qemu(m=None, version=31):
    m = m or fingertip.machine.build('backend.qemu')
    FEDORA_URL = FEDORA_MIRROR + f'/linux/releases/{version}/Server/x86_64/os'
    original_ram_size = m.qemu.ram_size

    with m:
        m.qemu.ram_size = '2G'
        with open(path.fingertip('ssh_key', 'fingertip.pub')) as f:
            ssh_pubkey = f.read().strip()

        with open(path.fingertip('kickstart_templates', 'fedora31')) as f:
            ks_text = f.read().format(HOSTNAME=f'fedora{version}',
                                      SSH_PUBKEY=ssh_pubkey,
                                      PROXY=m.http_cache.internal_url)
        m.http_cache.mock('http://ks', text=ks_text)
        log.info(f'fetching kernel: {FEDORA_URL}/isolinux/vmlinuz')
        kernel = os.path.join(m.path, 'kernel')
        m.http_cache.fetch(f'{FEDORA_URL}/isolinux/vmlinuz', kernel)
        log.info(f'fetching initrd: {FEDORA_URL}/isolinux/initrd.img')
        initrd = os.path.join(m.path, 'initrd')
        m.http_cache.fetch(f'{FEDORA_URL}/isolinux/initrd.img', initrd)
        append = ('ks=http://ks inst.ksstrict console=ttyS0 inst.notmux '
                  f'proxy={m.http_cache.internal_url} ' +
                  f'inst.proxy={m.http_cache.internal_url} ' +
                  f'inst.repo={FEDORA_URL}')
        extra_args = ['-kernel', kernel, '-initrd', initrd, '-append', append]

        m.qemu.run(load=None, extra_args=extra_args)
        m.console.expect('Storing configuration files and kickstarts')
        m.qemu.wait()
        m.qemu.compress_image()
        m.qemu.ram = original_ram_size
        m.qemu.run(load=None)  # cold boot
        HOSTNAME = 'fedora31'
        ROOT_PASSWORD = 'fingertip'
        m.prompt = f'[root@{HOSTNAME} ~]# '
        m.console.expect(f'{HOSTNAME} login: ')
        m.console.sendline('root')
        m.console.expect('Password: ')
        m.console.sendline(ROOT_PASSWORD)
        m.console.expect_exact(m.prompt)
        log.info('Fedora installation finished')

        m.hooks(unseal=unseal)

        return m


def main(m=None):
    m = m or fingertip.build('backend.qemu')
    if hasattr(m, 'qemu'):
        return m.apply(install_in_qemu)
    elif hasattr(m, 'container'):
        return m.apply(m.container.from_image, 'fedora')
    raise NotImplementedError()


def unseal(m):
    with m:
        m.ssh('systemctl restart NetworkManager')
        return m


def enable_repo(m, name, url, disabled=False):
    import textwrap
    with m:
        m.ssh(textwrap.dedent(f'''
            set -uex
            cat > /etc/yum.repos.d/{name}.repo <<EOF
            [{name}]
            baseurl = {url}
            enabled = {1 if not disabled else 0}
            gpgcheck = 0
            name = {name}
            EOF'''))
    return m
