# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.


def make_greeting(m, greeting='Hello!'):
    with m:
        m.console.sendline(f"echo '{greeting}' > .greeting")
        m.console.expect_exact(m.prompt)
        return m


def main(m, greeting='Hello!'):
    with m.apply(make_greeting, greeting=greeting) as m:
        m.console.sendline(f"cat .greeting")
        m.console.expect_exact(greeting)
        m.console.expect_exact(m.prompt)
