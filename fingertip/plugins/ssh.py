import re
import subprocess

import fingertip
from fingertip.util import path


@fingertip.transient(when='last')
def main(m=None, unseal=True):
    if m is None:
        return existing()
    m = m.apply('unseal') if unseal else m
    with m:
        m.expiration.cap(0)  # non-deterministic user input, never reuse
        m.log.info(f'waiting for the SSH server to be up...')
        m.ssh.exec('true')
        # terminate the ssh session not to leave any traces in vm
        m.ssh.invalidate()
        m.log.info(f'starting interactive SSH session, {m.ssh.port}')
        m.log.plain()
        _connect(m.ssh.port, m.ssh.key_file)
    return m


def _connect(port, key_file):
        subprocess.run(['ssh',
                        '-o', 'StrictHostKeyChecking=no',
                        '-o', 'UserKnownHostsFile=/dev/null',
                        '-o', 'GSSAPIAuthentication=no',
                        '-o', 'GSSAPIKeyExchange=no',
                        '-i', key_file,
                        '-p', str(port),
                        '-t',
                        'root@127.0.0.1'], check=True)


def existing():
    # HACK HACK HACK
    key_file = path.fingertip('ssh_key', 'fingertip')
    process = subprocess.Popen(['ps', '-uf'], stdout=subprocess.PIPE)
    stdout, _ = process.communicate()
    ports = [int(p) for p in
             re.findall(r'hostfwd=tcp:127.0.0.1:(\d+)-:22', stdout.decode())]
    if len(ports) == 1:
        return _connect(ports[0], key_file)
    elif len(ports) > 1:
        print('several fingertip VMs found, which port?')
        for i, p in enumerate(ports):
            print(f'[{i}] {p}')
        c = int(input('> '))
        return _connect(ports[c] if c < len(ports) else c, key_file)
    print('no running fingertip VMs found')
