# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2020 Red Hat, Inc., see CONTRIBUTORS.

import fingertip


def check_processes(m):
    if hasattr(m, 'console'):
        with m:
            # There should not be any hanging process after installation
            m.console.sendline("ps ax | grep '[s]shd.*notty' > .sshd1")
            m.console.expect_exact(m.prompt)

            if hasattr(m, 'ssh'):
                # debug output of all sshd processes
                r = m("ps ax | grep -A1 '[s]shd' && echo $$")
                assert r.retcode == 0
                m.ssh.invalidate()

            # There should not be any hanging process even after the above command
            m.console.sendline("ps ax | grep '[s]shd.*notty' > .sshd2")
            m.console.expect_exact(m.prompt)
    else:
        raise NotImplementedError()

    return m


@fingertip.transient
def main(m):
    m = m.apply('unseal')
    with m.apply(check_processes) as m:
        m.log.info(m('cat .sshd1').out.strip())
        m.log.info(m('cat .sshd2').out.strip())
        assert m('cat .sshd1 | wc -l').out.strip() == "0"
        assert m('cat .sshd2 | wc -l').out.strip() == "0"
