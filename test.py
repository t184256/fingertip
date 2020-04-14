#!/usr/bin/python3
import fingertip

BASES = dict(
    podman_centos=lambda: fingertip.build('backend.podman-criu', 'centos'),
    podman_fedora=lambda: fingertip.build('backend.podman-criu', 'fedora'),
    podman_fedorO=lambda: (
        fingertip
        .build('backend.podman-criu')
        .apply('os.fedora', updates=False)
    ),
    podman_alpine=lambda: (
        fingertip.build('backend.podman-criu').apply('os.alpine')
    ),
    podman_ubuntu=lambda: (
        fingertip
        .build('backend.podman-criu', 'ubuntu')
        .apply('exec', 'apt update && apt install -y python')
    ),
    qemu_alpine=lambda: (
        fingertip
        .build('os.alpine')
        .apply('unseal')
        .apply('.hooks.disable_cache')
    ),
    qemu_fedora=lambda: fingertip.build('os.fedora'),
    qemu_fedorO=lambda: fingertip.build('os.fedora', updates=False),
)

TESTS = dict(
    xtrue=lambda m: m.apply('exec', 'true'),
    again=lambda m: m.apply('exec', 'true'),
    nsave=lambda m: m.apply('exec', 'true', transient=True),
    xtend=lambda m: m.apply('exec', 'true').apply('exec', 'true'),
    false=lambda m: m.apply('exec', 'false', no_check=True),
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
    ('podman_alpine', 'wait4'),  # shell poorly snapshottable with CRIU (ash)
    ('podman_alpine', 'subsh'),  # shell poorly snapshottable with CRIU (ash)
    ('podman_ubuntu', 'wait4'),  # shell poorly snapshottable with CRIU (dash)
    ('podman_ubuntu', 'subsh'),  # shell poorly snapshottable with CRIU (dash)
    ('podman_centos', 'scrpt'),  # needs ssh.upload
    ('podman_fedora', 'scrpt'),  # needs ssh.upload
    ('podman_fedorO', 'scrpt'),  # needs ssh.upload
    ('podman_alpine', 'scrpt'),  # needs ssh.upload
    ('podman_ubuntu', 'scrpt'),  # needs ssh.upload
    ('podman_centos', 'hostn'),  # nothing sets it
    ('podman_fedora', 'hostn'),  # nothing sets it
    ('podman_fedorO', 'hostn'),  # nothing sets it
    ('podman_alpine', 'hostn'),  # nothing sets it
    ('podman_ubuntu', 'hostn'),  # nothing sets it
)

for base_name, base in BASES.items():
    for test_name, test in TESTS.items():
        if (base_name, test_name) in SKIP:
            continue

        print(f'{base_name}: {test_name}...')

        test(base())
