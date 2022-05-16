import os

import fingertip.machine
from fingertip.util import path

URL = 'http://mirror.centos.org/centos/8-stream/BaseOS/x86_64/os'

def main(m=None):
    m = m or fingertip.build('backend.qemu')
    if hasattr(m, 'qemu'):
        return m.apply(install_in_qemu)
    raise NotImplementedError()

def install_in_qemu(m=None):

    m = m or fingertip.machine.build('backend.qemu')

    with m:
        ssh_key_path = path.fingertip('ssh_key', 'fingertip.pub')
        with open(ssh_key_path) as f:
            ssh_pubkey = f.read().strip()
        m.expiration.depend_on_a_file(ssh_key_path)

        ks = path.fingertip('fingertip', 'plugins', 'os', 'centos_stream.ks')
        HOSTNAME = 'centos'

        fqdn = HOSTNAME + '.fingertip.local'

        with open(ks) as f:
            ks_text = f.read().format(HOSTNAME=fqdn,
                                      SSH_PUBKEY=ssh_pubkey,
                                      PROXY=m.http_cache.internal_url)
        m.expiration.depend_on_a_file(ks)
        m.http_cache.mock('http://mock/ks', text=ks_text)
        m.log.info(f'fetching kernel: {URL}/isolinux/vmlinuz')
        kernel = os.path.join(m.path, 'kernel')
        m.http_cache.fetch(f'{URL}/isolinux/vmlinuz', kernel)
        m.log.info(f'fetching initrd: {URL}/isolinux/initrd.img')
        initrd = os.path.join(m.path, 'initrd')
        m.http_cache.fetch(f'{URL}/isolinux/initrd.img', initrd)
        append = ('inst.ks=http://mock/ks console=ttyS0 ' +
                  'inst.text inst.notmux inst.cmdline ' +
                  f'proxy={m.http_cache.internal_url} ' +
                  f'inst.repo={URL} ')
        extra_args = ['-kernel', kernel, '-initrd', initrd, '-append', append]

        m.ram.safeguard = '768M'
        with m.ram('>=4G'):
            m.qemu.run(load=None, extra_args=extra_args)
            m.console.expect('Installation complete.')
            m.console.expect('Power down.')
            m.qemu.wait()
        m.qemu.run(load=None)  # cold boot

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

        m.log.info('CentOS Stream installation finished')

        def disable_proxy():
            return m.apply('ansible',
                           'ini_file',
                           path='/etc/yum.conf',
                           section='main',
                           option='proxy',
                           state='absent')

        m.hooks.disable_proxy.append(disable_proxy)

        m.hooks.unseal += [lambda: m('systemctl restart NetworkManager'),
                           lambda: m('nm-online')]

        m.hooks.timesync.append(lambda: m('hwclock -s'))

        m.centos = 8
        m.dist_git_branch = 'c8s'

        return m
