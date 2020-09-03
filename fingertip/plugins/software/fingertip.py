# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2020 Red Hat, Inc., see CONTRIBUTORS.

import os
import tarfile

from fingertip.util import path, temp

PREBUILD_DEPS = ['tar', 'git-core', 'rpm-build']
PREINSTALL_DEPS = [
    'ansible',
    'checkpolicy',
    'duperemove',
    'git-core',
    'nmap-ncat',
    'openssh-clients',
    'policycoreutils-python-utils',
    'python3',
    'python3-CacheControl',
    'python3-GitPython',
    'python3-cloudpickle',
    'python3-colorama',
    'python3-devel',
    'python3-fasteners',
    'python3-inotify_simple',
    'python3-lockfile',
    'python3-paramiko',
    'python3-pexpect',
    'python3-pyxdg',
    'python3-rangehttpserver',
    'python3-requests',
    'python3-requests-mock',
    'python3-ruamel-yaml',
    'qemu-img',
    'qemu-system-x86',
    'rsync',
    'util-linux',
    'xfsprogs',
]


def prepare(m, preinstall=False):
    assert hasattr(m, 'fedora')
    with m:
        pkgs = PREBUILD_DEPS + PREINSTALL_DEPS if preinstall else PREBUILD_DEPS
        m = m.apply('ansible', 'dnf', state='present', name=pkgs,
                    install_weak_deps=False)
        m.fingertip_prepared = True
    return m


def build(m, from_=None, preinstall=False):
    tarbomb = from_ if from_ and from_.endswith('.tar') else None
    if tarbomb is None:
        if from_ is None:
            fingertip_sources = path.FINGERTIP
            assert os.path.exists(os.path.join(fingertip_sources, '.copr'))
        tarbomb = temp.disappearing_file()
        with tarfile.open(tarbomb, 'w') as tf:
            tf.add(fingertip_sources, arcname='/', filter=lambda ti:
                   ti if '/redhat/' not in ti.name else None)
    if not hasattr(m, 'fingertip_prepared'):
        m = m.apply(prepare, preinstall)
    with m:
        m.ssh.upload(tarbomb, '/tmp/fingertip.tar')
        m.expiration.depend_on_a_file(tarbomb)
        m(r'''
            set -uex
            mkdir -p /tmp/fingertip/builddir
            cd /tmp/fingertip/builddir
            tar xf /tmp/fingertip.tar
            ./.copr/build-local.sh
            dnf -y builddep --setopt=install_weak_deps=False \
                /tmp/fingertip/srpms/*.rpm
            mkdir /tmp/fingertip/rpms
            rpmbuild -rb /tmp/fingertip/srpms/*.src.rpm \
                --define "_rpmdir /tmp/fingertip/rpms"
        ''')
        m.fingertip_built = True
    return m


def main(m, from_=None, preinstall=False):
    if not hasattr(m, 'fingertip_built'):
        m = m.apply(build, from_, preinstall)
    with m:
        m.ram.min = '2G'
        m('dnf -y install /tmp/fingertip/rpms/noarch/*.rpm')
        m.fingertip_installed = True
        m('useradd user', check=False)
        m('usermod -aG fingertip user')
        m('systemctl enable --now fingertip-shared-cache')
        m('fingertip-shared-cache-use user')
    return m
