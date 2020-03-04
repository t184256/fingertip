# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import requests

import os

import fingertip.machine
from fingertip.util import path


FEDORA_GEOREDIRECTOR = 'http://download.fedoraproject.org/pub/fedora/linux'


def determine_mirror(mirror):
    h = requests.head(mirror, allow_redirects=False)
    if h.status_code in (301, 302, 303, 307, 308) and 'Location' in h.headers:
        return h.headers['Location'].rstrip('/')
    return mirror


def main(m=None, version=31, updates=True,
         mirror=None, resolve_redirect=False):
    m = m or fingertip.build('backend.qemu')
    if hasattr(m, 'qemu'):
        m = m.apply(install_in_qemu, version=version, updates=updates,
                    mirror=mirror, resolve_redirect=resolve_redirect)
    elif hasattr(m, 'container'):
        m = m.apply(m.container.from_image, f'fedora:{version}')
        if updates:
            with m:
                m('dnf -y update')
    else:
        raise NotImplementedError()
    return m


def install_in_qemu(m, version, updates=True,
                    mirror=None, resolve_redirect=False):
    ml_norm = ('http://mirrors.fedoraproject.org/metalink' +
               f'?repo=fedora-{version}&arch=x86_64&protocol=http')
    ml_upd = ('http://mirrors.fedoraproject.org/metalink' +
              f'?repo=updates-released-f{version}&arch=x86_64&protocol=http')
    releases_development = 'development' if version == '32' else 'releases'
    if mirror:
        if resolve_redirect:
            m.log.info(f'autoselecting mirror by redirect from {mirror}...')
            mirror = determine_mirror(mirror)
        url = f'{mirror}/{releases_development}/{version}/Everything/x86_64/os'
        upd = f'{mirror}/updates/{version}/Everything/x86_64'
        repos = (f'url --url {url}\n' +
                 f'repo --name fedora --baseurl {url}\n' +
                 (f'repo --name updates --baseurl {upd}'
                  if updates else ''))
    else:
        m.log.info('autoselecting mirror...')
        mirror = determine_mirror(FEDORA_GEOREDIRECTOR)
        url = f'{mirror}/{releases_development}/{version}/Everything/x86_64/os'
        repos = (f'url --url {url}\n' +
                 f'repo --name fedora --metalink {ml_norm}\n' +
                 (f'repo --name updates --metalink {ml_upd}'
                  if updates else ''))
    m.log.info(f'selected mirror: {mirror}')

    m.expiration.cap('2d')  # non-immutable repositories

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
                                      PROXY=m.http_cache.internal_url,
                                      REPOS=repos)
        m.expiration.depend_on_a_file(ks_fname)

        m.http_cache.mock('http://ks', text=ks_text)
        m.log.info(f'fetching kernel: {url}/isolinux/vmlinuz')
        kernel = os.path.join(m.path, 'kernel')
        m.http_cache.fetch(f'{url}/isolinux/vmlinuz', kernel)
        m.log.info(f'fetching initrd: {url}/isolinux/initrd.img')
        initrd = os.path.join(m.path, 'initrd')
        m.http_cache.fetch(f'{url}/isolinux/initrd.img', initrd)
        append = ('ks=http://ks inst.ksstrict console=ttyS0 inst.notmux '
                  f'proxy={m.http_cache.internal_url} ' +
                  f'inst.proxy={m.http_cache.internal_url} ' +
                  f'inst.repo={url}')
        extra_args = ['-kernel', kernel, '-initrd', initrd, '-append', append]

        m.qemu.run(load=None, extra_args=extra_args)
        i = m.console.expect(['Storing configuration files and kickstarts',
                              'installation failed',
                              'installation was stopped',
                              'installer will now terminate'])
        assert i == 0, 'Installation failed'
        m.qemu.wait()
        m.qemu.compress_image()
        m.qemu.ram_size = original_ram_size
        m.qemu.run(load=None)  # cold boot
        HOSTNAME = f'fedora{version}'
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
