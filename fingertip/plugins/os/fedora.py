# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import os
import tempfile

import fingertip.machine
from fingertip.util import log, path


FEDORA_MIRROR = 'http://download.fedoraproject.org/pub/fedora'


def main(m=None, version=31):
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
        with tempfile.NamedTemporaryFile() as ks_file:
            ks_file.write(ks_text.encode())
            ks_file.flush()
            cached_ks_file = m.http_cache.fetch('file://' + ks_file.name,
                                                always=True)
        ks_url = m.http_cache.proxied_url(cached_ks_file)
        kernel = m.http_cache.fetch(f'{FEDORA_URL}/isolinux/vmlinuz')
        initrd = m.http_cache.fetch(f'{FEDORA_URL}/isolinux/initrd.img')
        extra_args = ['-kernel', kernel, '-initrd', initrd, '-append',
                      f'ks={ks_url} inst.ksstrict console=ttyS0 inst.notmux '
                      f'proxy={m.http_cache.internal_url} '
                      f'inst.proxy={m.http_cache.internal_url} '
                      f'inst.repo={FEDORA_URL}']

        m.qemu.run(load=None, extra_args=extra_args)
        m.console.expect('Storing configuration files and kickstarts')
        m.qemu.wait()
        os.unlink(cached_ks_file)
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

        m.hook(unseal=unseal)

        return m


def unseal(m):
    with m:
        m.ssh('systemctl restart NetworkManager')
        return m
