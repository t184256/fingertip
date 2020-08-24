# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import fingertip.exec


def main(m, *args, unseal=True, transient=False,
         shell=True, check=True):
    m = m.apply('unseal') if unseal else m
    with m:
        r = m.exec(*(args if not shell else ('sh', '-c', ' '.join(args))))
    if not r and check:
        raise fingertip.exec.CommandExecutionError(r)
    if not transient:
        return m
