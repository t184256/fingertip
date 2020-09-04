# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2020 Red Hat, Inc., see CONTRIBUTORS.

import logging
import os
import re
import subprocess

import fingertip
from fingertip.util import reflink


META_TEMPLATE = '''
instance-id: fingertip
local-hostname: {HOSTNAME}
'''

USER_TEMPLATE = '''
#cloud-config
fqdn: {FQDN}
final_message: cloud-config final message
ssh_authorized_keys:
  - {SSH_PUBKEY}
disable_root: False
chpasswd:
  list: |
     root:fingertip
  expire: False
'''


def main(m=None, url=None):
    m = m or fingertip.build('backend.qemu')
    assert url
    assert hasattr(m, 'qemu')

    # because we have no idea how to unseal it later
    m.sealed = False
    m.expiration.cap('4h')

    image_file = os.path.join(m.path, os.path.basename(url))
    if '://' in url:
        m.log.info(f'fetching {url}...')
        m.http_cache.fetch(url, image_file)
    else:
        m.log.info(f'copying {url}...')
        reflink.auto(url, image_file)
        m.expiration.depend_on_a_file(url)

    m.log.info('resizing image...')
    run = m.log.pipe_powered(subprocess.run,
                             stdout=logging.INFO, stderr=logging.ERROR)
    run(['qemu-img', 'resize', image_file, m.qemu.disk_size], check=True)
    m.qemu._image_to_clone = image_file
    m.qemu.virtio_scsi = True  # in case it's Linux <5

    with m:
        hostname = url.rsplit('/', 1)[-1].rsplit('.', 1)[0].replace('.', '_')
        hostname = hostname.replace('x86_64', '')
        fqdn = hostname + '.fingertip.local'

        meta_data = META_TEMPLATE.format(FQDN=fqdn,
                                         HOSTNAME=hostname,
                                         SSH_PUBKEY=m.ssh.pubkey)
        meta_file = os.path.join(m.path, 'meta-data')
        with open(meta_file, 'w') as f:
            f.write(meta_data)
        m.http_cache.serve_local_file('/cloud-init/meta-data', meta_file)

        user_data = USER_TEMPLATE.format(FQDN=fqdn,
                                         HOSTNAME=hostname,
                                         SSH_PUBKEY=m.ssh.pubkey)
        user_file = os.path.join(m.path, 'user-data')
        with open(user_file, 'w') as f:
            f.write(user_data)
        m.http_cache.serve_local_file('/cloud-init/user-data', user_file)

        init_url = m.http_cache.internal_url + '/cloud-init/'
        seed = ['-smbios', f'type=1,serial=ds=nocloud-net;s={init_url}']

        m.qemu.run(load=None, extra_args=seed)

        m.console.expect_exact('cloud-config final message')
        m.console.sendline('')
        m.console.sendline('')
        m.console.expect(f'login:')
        m.console.sendline('root')
        m.console.expect('Password: ')
        m.console.sendline('fingertip')
        m.console.sendline(' echo prompt" "detection\n')
        m.console.expect_exact('prompt detection')
        m.prompt = re.search(r'\n(.+?) echo prompt', m.console.before).group(1)
        m.log.debug(f'm.prompt = {repr(m.prompt)}')
        m.console.sendline('')
        m.console.expect_exact(m.prompt)

        m.ram.safeguard = '512M'  # sane for 2020, and it's overrideable anyway

        def login(username='root', password='fingertip'):
            m.console.expect(f'login: ')
            m.console.sendline(username)
            m.console.expect('Password: ')
            m.console.sendline(password)
            m.console.expect_exact(m.prompt)
        m.login = login

        m.hooks.timesync.append(lambda: m('hwclock -s'))

        m.log.info('cloud-init finished')
    return m
