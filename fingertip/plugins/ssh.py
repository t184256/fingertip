import subprocess

import fingertip


@fingertip.transient
def main(m, no_unseal=False):
    m = m if no_unseal else m.apply('unseal')
    with m.transient() as m:
        m.log.info(f'waiting for the SSH server to be up...')
        m.ssh.exec('true')
        m.log.info(f'starting interactive SSH session, {m.ssh.port}')
        m.log.plain()
        subprocess.run(['ssh',
                        '-o', 'StrictHostKeyChecking=no',
                        '-o', 'UserKnownHostsFile=/dev/null',
                        '-o', 'GSSAPIAuthentication=no',
                        '-o', 'GSSAPIKeyExchange=no',
                        '-i', m.ssh.key_file,
                        '-p', str(m.ssh.port),
                        '-t',
                        'root@127.0.0.1'])
