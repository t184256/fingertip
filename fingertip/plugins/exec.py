# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import fingertip.exec


def main(m, *args, no_unseal=False, transient=False,
         no_shell=False, no_check=False):
    m = m if no_unseal else m.apply('unseal')
    with m:
        r = m.exec(*(args if no_shell else ('sh', '-c', ' '.join(args))))
    if not r and not no_check:
        raise fingertip.exec.CommandExecutionError(r)
    if not transient:
        return m
