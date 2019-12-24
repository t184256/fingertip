# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import fingertip


PROMPTS = [f'{i}> ' for i in range(20)]


def apply_prompt(m, p):
    with m:
        m.console.sendline('')
        m.console.expect_exact(m.prompt)
        m.console.sendline(f'export PS1="{p}"')
        m.console.expect_exact(p)
        m.prompt = p
        return m


@fingertip.transient
def main(m, greeting='Hello!'):
    assert hasattr(m, 'console')
    for p in PROMPTS:
        m = m.apply(apply_prompt, p)
