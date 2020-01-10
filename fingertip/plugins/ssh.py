import subprocess

import fingertip
from fingertip.util import log


@fingertip.transient
def main(m, no_unseal=False):
    m = m if no_unseal else m.apply('unseal')
    with m.transient() as m:
        log.info(f'waiting for the SSH server to be up...')
        m.ssh('true')
        log.info(f'starting interactive SSH session, {m.ssh.port}')
        subprocess.run(['ssh',
                        '-o', 'StrictHostKeyChecking=no',
                        '-o', 'GSSAPIAuthentication=no',
                        '-o', 'GSSAPIKeyExchange=no',
                        '-i', m.ssh.key_file,
                        '-p', str(m.ssh.port),
                        '-t',
                        'root@127.0.0.1'])


def exec(m, cmd, no_unseal=False, transient=False):
    m = m if no_unseal else m.apply('unseal')
    with m:
        log.info(f'waiting for the SSH server to be up...')
        m.ssh(cmd)
    if not transient:
        return m
