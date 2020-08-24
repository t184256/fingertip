import subprocess

import fingertip


@fingertip.transient(when='last')
def main(m, unseal=True):
    m = m.apply('unseal') if unseal else m
    with m:
        m.expiration.cap(0)  # non-deterministic user input, never reuse
        m.log.info(f'waiting for the SSH server to be up...')
        m.ssh.exec('true')
        # terminate the ssh session not to leave any traces in vm
        m.ssh.invalidate()

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
                        'root@127.0.0.1'], check=True)
    return m
