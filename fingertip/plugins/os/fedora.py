# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 - 2022 Red Hat, Inc., see CONTRIBUTORS.

import requests

import os

import fingertip.machine
from fingertip.util import http_cache, log, path, units
from fingertip.plugins.os.common import red_hat_based


FEDORA_GEOREDIRECTOR = 'http://download.fedoraproject.org/pub/fedora/linux'
RELEASED = 40

def prepare_upgrade(m, releasever=None):
    assert hasattr(m, 'fedora')
    assert hasattr(m, 'qemu')

    releasever = releasever or m.fedora + 1

    with m:
        m('dnf upgrade -y --refresh')
        pkgs = ['dnf-plugin-system-upgrade']
        if releasever == 'rawhide':
            # if we upgrade to unreleased version, we need to tweak repo files
            m('rm /etc/yum.repos.d/fedora-updates*.repo')
            # Fedora 40 -> Fedora 41 rawhide is now DNF4 -> DNF5, for proxy:
            pkgs += ['libdnf5-plugin-actions']
        m = m.apply('ansible', 'package', state='present', name=pkgs)
        if releasever in (RELEASED + 1, 'rawhide'):
            m(r'sed -i -e "s|\(baseurl=.*/\)releases/|\1development/|g" '
               '/etc/yum.repos.d/fedora.repo')
            m('cat /etc/yum.repos.d/fedora.repo')
        m(f'dnf -y system-upgrade download --releasever={releasever}')
        m.fedora_upgrade_prepared = True
    return m

def upgrade(m=None, releasever=None):
    """
    Upgrade Fedora to other release using dnf system upgrade plugin.
    If no releasever is specified, upgrade to the next major version.
    Useful to get pre-release versions.
    """
    m = m or fingertip.build('os.fedora')
    assert hasattr(m, 'fedora')
    assert hasattr(m, 'qemu')

    releasever = releasever or m.fedora + 1

    if not hasattr(m, 'fedora_upgrade_prepared'):
        m = m.apply(prepare_upgrade, releasever)

    with m, m.ram('>=2G'):
        m('sudo systemctl enable getty@ttyS0')  # IDK why
        m.console.sendline(' dnf system-upgrade reboot')
        m.login()
        m('systemctl is-system-running --wait || true')
        rel = m('cat /etc/fedora-release').out
        if releasever == 'rawhide':
            assert 'Rawhide' in rel
        else:
            assert str(releasever) in rel
        prev_release = m.fedora
        m.fedora = releasever
        m.dist_git_branch = (f'f{releasever}'
                             if releasever != 'rawhide' else 'rawhide')

        m._package_manager_proxied = False
        if releasever == 'rawhide':
            # Fedora 40 -> Fedora 41 rawhide is now DNF4 -> DNF5
            red_hat_based.proxy_dnf_action(m)
        else:
            red_hat_based.proxy_dnf(m)

        if releasever == 'rawhide':
            m('rm -f /etc/yum.repos.d/fedora-updates*.repo')

        m(f'''
          sed -i 's|^#baseurl=|baseurl=|' /etc/yum.repos.d/*
          sed -i 's|^baseurl=https://|baseurl=http://|' /etc/yum.repos.d/*
          sed -i 's|^metalink=|#metalink=|' /etc/yum.repos.d/*
          sed -i 's|download\\.example|download.fedoraproject.org|' \
                  /etc/yum.repos.d/*
          dnf -y autoremove
          dnf repoquery --installonly --latest-limit=-1 -q \
              | grep f{prev_release} | xargs dnf -y remove
          dnf -y clean all; dnf -y makecache; fstrim -va
        ''')

        hostname = f'fedora-{m.dist_git_branch}'
        m(f'hostnamectl set-hostname {hostname}')

        def login(username='root', password='fingertip'):
            if username == 'root':
                m.prompt = f'[root@{hostname} ~]# '
            else:
                m.prompt = f'[{username}@{hostname} ~]$ '
            m.console.expect(f'{hostname} login: ')
            m.console.sendline(username)
            m.console.expect('Password: ')
            m.console.sendline(password)
            m.console.expect_exact(m.prompt)

        m.login = login

        m.console.sendline(' reboot')
        m.login()
        m('systemctl is-system-running --wait || true')
    return m


def main(m=None, version=RELEASED, mirror=None, specific_mirror=True,
         fips=False):
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
    # if you have a saviour mirror, let's assume it's a good one
    for source, _ in http_cache.saviour_sources():
        if source != 'direct' and http_cache.is_fetcheable(source, mirror):
            return mirror
    # we can query a georedirector for a local Fedora mirror and use just
    # that one, consistently. problem is, it also yields really broken ones.
    # let's check that a mirror has at least a repomd.xml,
    # a kernel and an initrd:
    updates_repomd = f'updates/{version}/Everything/x86_64/repodata/repomd.xml'
    kernel = (f'{releases_development}/{version}'
              '/Everything/x86_64/os/images/pxeboot/vmlinuz')
    initrd = (f'{releases_development}/{version}'
              '/Everything/x86_64/os/images/pxeboot/initrd.img')

    h = requests.head(mirror + '/' + updates_repomd, allow_redirects=False)
    if h.status_code in (301, 302, 303, 307, 308) and 'Location' in h.headers:
        r = h.headers['Location'].rstrip('/').replace('https://', 'http://')
        assert r.endswith('/' + updates_repomd)
        base = r[:-len('/' + updates_repomd)]
        # good, now now ensure it also has a kernel
        heads = [requests.head(base + '/' + kernel),
                 requests.head(base + '/' + initrd)]
        if not all(h.status_code == 200 for h in heads):
            log.warning(f'{base}/{{kernel,initrd.img}} '
                        f'-> {[h.status_code for h in heads]}')
            log.warning(f'mirror {base} is broken, trying another one')
            return determine_mirror(mirror, version, releases_development)
        else:
            return base
    return mirror


def install_in_qemu(m, version, mirror=None, specific_mirror=True, fips=False):
    version = int(version) if version != 'rawhide' else 'rawhide'
    releases_development = ('development'
                            if version in (RELEASED + 1, 'rawhide')
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

    with m:
        m.ram.safeguard = '768M'
        # RAM size accommodates `dnf clean all && dnf install something`
        # Fedora 36, 2023-04-05
        if m.ram.size < units.parse_binary('1536M'):
            m.ram.size = '1536M'
        m.expiration.cap('2d')  # non-immutable repositories

        hostname = f'fedora{version}' + ('-fips' if fips else '')
        fqdn = hostname + '.fingertip.local'
        ssh_key_fname = path.fingertip('ssh_key', 'fingertip.pub')
        with open(ssh_key_fname) as f:
            ssh_pubkey = f.read().strip()
        m.expiration.depend_on_a_file(ssh_key_fname)

        ks_fname = path.fingertip('kickstart_templates',
                                  f'fedora{version}' if version != 'rawhide'
                                  else f'fedora{RELEASED+1}')
        with open(ks_fname) as f:
            ks_text = f.read().format(HOSTNAME=fqdn,
                                      SSH_PUBKEY=ssh_pubkey,
                                      PROXY=m.http_cache.internal_url,
                                      MIRROR=mirror,
                                      REPOS=repos)
        m.expiration.depend_on_a_file(ks_fname)

        m.http_cache.mock('http://mock/ks', text=ks_text)
        m.log.info(f'fetching kernel: {url}/images/pxeboot/vmlinuz')
        kernel = os.path.join(m.path, 'kernel')
        m.http_cache.fetch(f'{url}/images/pxeboot/vmlinuz', kernel)
        m.log.info(f'fetching initrd: {url}/images/pxeboot/initrd.img')
        initrd = os.path.join(m.path, 'initrd')
        m.http_cache.fetch(f'{url}/images/pxeboot/initrd.img', initrd)
        append = ('inst.ks=http://mock/ks inst.ksstrict '
                  'console=ttyS0 inst.notmux '
                  'inst.zram=off '
                  f'proxy={m.http_cache.internal_url} '
                  f'inst.proxy={m.http_cache.internal_url} '
                  f'inst.repo={url} '
                  'inst.wait_for_disks=0' +
                  ('fips=1' if fips else ''))
        extra_args = ['-kernel', kernel, '-initrd', initrd, '-append', append]

        with m.ram('3G'):
            m.qemu.run(load=None, extra_args=extra_args)
            i = m.console.expect(['Storing configuration files and kickstarts',
                                  'installation failed',
                                  'installation was stopped',
                                  'installer will now terminate'])
            assert i == 0, 'Installation failed'
            m.qemu.wait()

        m.qemu.run(load=None)  # cold boot

        def login(username='root', password='fingertip'):
            if username == 'root':
                m.prompt = f'[root@{hostname} ~]# '
            else:
                m.prompt = f'[{username}@{hostname} ~]$ '
            m.console.expect(f'{hostname} login: ')
            m.console.sendline(username)
            m.console.expect('Password: ')
            m.console.sendline(password)
            m.console.expect_exact(m.prompt)

        m.login = login

        m.login()
        m.log.info('Fedora installation finished')
        os.unlink(kernel)
        os.unlink(initrd)

        m.hooks.unseal += [lambda: m('systemctl restart NetworkManager'),
                           lambda: m('nm-online')]

        m.hooks.timesync.append(lambda: m('hwclock -s'))

        m.fedora = version
        m.dist_git_branch = (f'f{version}'
                             if version != 'rawhide' else 'rawhide')

        red_hat_based.proxy_dnf(m)

        return m
