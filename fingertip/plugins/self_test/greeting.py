# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import fingertip


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


@fingertip.transient
def main(m, greeting='Hello!'):
    with m.apply(make_greeting, greeting=greeting) as m:
        assert m('cat .greeting').out.strip() == greeting
