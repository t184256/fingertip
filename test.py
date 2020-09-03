#!/usr/bin/python3
import fingertip

DEBIAN = ('http://cdimage.debian.org/cdimage/openstack/10.5.1-20200830/'
          'debian-10.5.1-20200830-openstack-amd64.qcow2')

BASES = dict(
    podman_centos=lambda: fingertip.build('backend.podman-criu', 'centos'),
    podman_alpine=lambda: (
        fingertip.build('backend.podman-criu').apply('os.alpine')
    ),
    podman_ubuntu=lambda: (
        fingertip.build('backend.podman-criu', 'ubuntu')
                 .apply('exec', 'apt update && apt install -y python')
    ),
    qemu_alpine=lambda: (
        fingertip.build('os.alpine')
                 .apply('unseal')
                 .apply('.hooks.disable_cache')
    ),
    qemu_fedora=lambda: fingertip.build('os.fedora'),
    qemu_debian=lambda: (
        fingertip.build('backend.qemu', ram_min='512M')
                 .apply('os.cloud-init', url=DEBIAN)),
)

TESTS = dict(
    xtrue=lambda m: m.apply('exec', 'true'),
    again=lambda m: m.apply('exec', 'true'),
    nsave=lambda m: m.apply('exec', 'true', transient=True),
    xtend=lambda m: m.apply('exec', 'true').apply('exec', 'true'),
    false=lambda m: m.apply('exec', 'false', check=False),
    uname=lambda m: m.apply('ansible', 'command', 'uname -a'),
    patch=lambda m: m.apply('ansible', 'package',
                            name='patch', state='present'),
    execs=lambda m: m.apply('self_test.exec'),
    greet=lambda m: m.apply('self_test.greeting'),
    prmpt=lambda m: m.apply('self_test.prompts'),
    subsh=lambda m: m.apply('self_test.subshell'),
    wait4=lambda m: m.apply('self_test.wait_for_it'),
    scrpt=lambda m: m.apply('self_test.script'),
    hostn=lambda m: m.apply('self_test.hostname'),
)

SKIP = (
    ('qemu_fedora', 'prmpt'),  # takes too much time
    ('qemu_debian', 'hostn'),  # no idea
    ('podman_alpine', 'wait4'),  # shell poorly snapshottable with CRIU (ash)
    ('podman_alpine', 'subsh'),  # shell poorly snapshottable with CRIU (ash)
    ('podman_ubuntu', 'wait4'),  # shell poorly snapshottable with CRIU (dash)
    ('podman_ubuntu', 'subsh'),  # shell poorly snapshottable with CRIU (dash)
    ('podman_centos', 'scrpt'),  # needs ssh.upload
    ('podman_alpine', 'scrpt'),  # needs ssh.upload
    ('podman_ubuntu', 'scrpt'),  # needs ssh.upload
    ('podman_centos', 'hostn'),  # nothing sets it
    ('podman_alpine', 'hostn'),  # nothing sets it
    ('podman_ubuntu', 'hostn'),  # nothing sets it
)

for base_name, base in BASES.items():
    for test_name, test in TESTS.items():
        if (base_name, test_name) in SKIP:
            continue

        print(f'{base_name}: {test_name}...')

        test(base())
