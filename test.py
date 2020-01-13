#!/usr/bin/python3
import fingertip

BASES = (
    lambda: fingertip.build('backend.podman-criu', 'centos'),
    lambda: fingertip.build('backend.podman-criu', 'fedora'),
    lambda: fingertip.build('os.fedora'),
    lambda: (fingertip.build('os.alpine')
                      .apply('unseal')
                      .apply('os.alpine.disable_proxy')),
    lambda: (fingertip.build('backend.podman-criu', 'ubuntu')
                      .apply('backend.podman-criu.exec',
                             'apt update && apt install -y python')),
)

TESTS = (
    lambda m: m.apply('ansible', 'command', 'uname -a'),
    lambda m: m.apply('ansible', 'package', name='patch', state='present'),
    lambda m: m.apply('ssh.exec', 'true') if hasattr(m, 'ssh') else None,
    lambda m: m.apply('self_test.greeting'),
    lambda m: m.apply('self_test.prompts'),
    lambda m: m.apply('self_test.subshell'),
    lambda m: m.apply('self_test.wait_for_it'),
)

for base in BASES:
    for test in TESTS:
        test(base())
