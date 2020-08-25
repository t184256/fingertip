# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import requests

import os

import fingertip.machine
from fingertip.util import log, path


FEDORA_GEOREDIRECTOR = 'http://download.fedoraproject.org/pub/fedora/linux'
LATEST = 33


def main(m=None, version=32, mirror=None, specific_mirror=True, fips=False):
    m = m or fingertip.build('backend.qemu')
    if hasattr(m, 'qemu'):
        m = m.apply(install_in_qemu, version=version, mirror=mirror,
                    specific_mirror=specific_mirror, fips=fips)
    elif hasattr(m, 'container'):
        m = m.apply(m.container.from_image, f'fedora:{version}')
        with m:
            m('dnf -y update')
    else:
        raise NotImplementedError()
    return m


def determine_mirror(mirror, version, releases_development):
    # we can query a georedirector for a local Fedora mirror and use just
    # that one, consistently. problem is, it also yields really broken ones.
    # let's check that a mirror has at least a repomd.xml and a kernel:
    updates_repomd = f'updates/{version}/Everything/x86_64/repodata/repomd.xml'
    kernel = (f'{releases_development}/{version}'
              '/Everything/x86_64/os/isolinux/vmlinuz')

    h = requests.head(mirror + '/' + updates_repomd, allow_redirects=False)
    if h.status_code in (301, 302, 303, 307, 308) and 'Location' in h.headers:
        r = h.headers['Location'].rstrip('/').replace('https://', 'http://')
        assert r.endswith('/' + updates_repomd)
        base = r[:-len('/' + updates_repomd)]
        # good, now now ensure it also has a kernel
        h = requests.head(base + '/' + kernel)
        if h.status_code != 200:
            log.warning(f'{base + "/" + kernel} -> {h.status_code}')
            log.warning(f'mirror {base} is broken, trying another one')
            return determine_mirror(mirror, version, releases_development)
        else:
            return base
    return mirror


def install_in_qemu(m, version, mirror=None, specific_mirror=True, fips=False):
    version = int(version) if version != 'rawhide' else 'rawhide'
    releases_development = ('development' if version in (LATEST, 'rawhide')
                            else 'releases')
    if mirror is None:
        if not specific_mirror:
            mirror = FEDORA_GEOREDIRECTOR  # not consistent, not recommended!
        else:
            mirror = determine_mirror(FEDORA_GEOREDIRECTOR, version,
                                      releases_development)
            m.log.info(f'autoselected mirror {mirror}')
    url = f'{mirror}/{releases_development}/{version}/Everything/x86_64/os'
    upd = f'{mirror}/updates/{version}/Everything/x86_64'
    repos = (f'url --url {url}\n' +
             f'repo --name fedora --baseurl {url}\n' +
             f'repo --name updates --baseurl {upd}')

    m.expiration.cap('2d')  # non-immutable repositories

    original_ram_size = m.qemu.ram_size

    with m:
        m.qemu.ram_size = '2G'
        hostname = f'fedora{version}' + ('-fips' if fips else '')
        fqdn = hostname + '.fingertip.local'
        ssh_key_fname = path.fingertip('ssh_key', 'fingertip.pub')
        with open(ssh_key_fname) as f:
            ssh_pubkey = f.read().strip()
        m.expiration.depend_on_a_file(ssh_key_fname)

        ks_fname = path.fingertip('kickstart_templates',
                                  f'fedora{version}' if version != 'rawhide'
                                  else f'fedora{LATEST}')
        with open(ks_fname) as f:
            ks_text = f.read().format(HOSTNAME=fqdn,
                                      SSH_PUBKEY=ssh_pubkey,
                                      PROXY=m.http_cache.internal_url,
                                      MIRROR=mirror,
                                      REPOS=repos)
        m.expiration.depend_on_a_file(ks_fname)

        m.http_cache.mock('http://mock/ks', text=ks_text)
        m.log.info(f'fetching kernel: {url}/isolinux/vmlinuz')
        kernel = os.path.join(m.path, 'kernel')
        m.http_cache.fetch(f'{url}/isolinux/vmlinuz', kernel)
        m.log.info(f'fetching initrd: {url}/isolinux/initrd.img')
        initrd = os.path.join(m.path, 'initrd')
        m.http_cache.fetch(f'{url}/isolinux/initrd.img', initrd)
        append = ('ks=http://mock/ks inst.ksstrict console=ttyS0 inst.notmux '
                  f'proxy={m.http_cache.internal_url} ' +
                  f'inst.proxy={m.http_cache.internal_url} ' +
                  f'inst.repo={url} ' +
                  ('fips=1' if fips else ''))
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
        ROOT_PASSWORD = 'fingertip'
        m.prompt = f'[root@{hostname} ~]# '
        m.console.expect(f'{hostname} login: ')
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

        m.hooks.timesync.append(lambda: m('hwclock -s'))

        m.fedora = version

        return m
