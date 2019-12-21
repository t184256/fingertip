# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.


def make_greeting(m, greeting='Hello!'):
    with m:
        if hasattr(m, 'console'):
            m.console.sendline(f"echo '{greeting}' > .greeting")
            m.console.expect_exact(m.prompt)
            m.console.sendline("ls -l .greeting")
            m.console.expect_exact(m.prompt)
        else:
            raise NotImplementedError()
        return m


def main(m, greeting='Hello!'):
    with m.apply(make_greeting, greeting=greeting).transient() as m:
        if hasattr(m, 'ssh'):
            assert m.ssh('cat .greeting').strip() == greeting
        elif hasattr(m, 'container'):
            assert m.container.exec('cat .greeting').strip() == greeting
        else:
            raise NotImplementedError()
