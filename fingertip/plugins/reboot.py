# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2024 Red Hat, Inc., see CONTRIBUTORS.

def main(m):

    with m:
        m.console.sendline(' reboot')
        m.login()
        if 'wait_for_running' not in m.hooks:
            m.log.warning(
                'fingertip does not know how to wait for the current OS to be'
                ' up and running. Please define the "wait_for_running" hook in'
                ' your OS plugin.'
            )
        else:
            m.hooks.wait_for_running()

    return m
