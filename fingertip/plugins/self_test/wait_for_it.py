# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import fingertip


def setup(m):
    with m:
        # yes, 5 sec is a lot, but ballooning down VM RAM might be slow
        m.console.sendline("echo '>'pre-sleep; sleep 5; echo '>'post-sleep")
        m.console.expect_exact('>pre-sleep')
        return m


@fingertip.transient
def main(m):
    with m.apply(setup) as m:
        m.console.expect_exact('>post-sleep')
        m.console.expect_exact(m.prompt)
