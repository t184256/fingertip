# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import itertools
import os
import sys

import fingertip
import fingertip.util.cleanup_job
import fingertip.util.log
import fingertip.util.path
import fingertip.util.reflink

from fingertip.util import optional_pretty_backtraces  # noqa: F401


def parse_kwarg(kwarg):
    if '=' in kwarg:
        key, val = kwarg.split('=', 1)
    else:
        if kwarg.startswith('no-'):
            key, val = kwarg[3:], False
        else:
            key, val = kwarg, True
    return key.replace('-', '_'), val


def parse_subcmd(subcmd, *all_args):
    args = [a for a in all_args if not a.startswith('--')]
    kwargs = [parse_kwarg(a[2:])
              for a in sorted(all_args) if a.startswith('--')]
    return subcmd, args, dict(kwargs)


def main():
    # Start with plain to get output from setup wizard
    fingertip.util.log.plain()

    if (sys.argv[1:] not in (['cleanup', 'periodic'],
                             ['filesystem', 'cleanup'])):
        fingertip.util.reflink.storage_setup_wizard()
        fingertip.util.cleanup_job.schedule()

    fingertip.util.log.nicer()

    args = sys.argv[1:]

    if not args:
        fingertip.util.log.error('no plugin specified')
        sys.exit(1)

    subcmds = [list(ws)
               for x, ws in itertools.groupby(args, lambda w: w != '+') if x]
    first_step, *rest_of_the_steps = [parse_subcmd(*sc) for sc in subcmds]

    first_step_cmd, first_step_args, first_step_kwargs = first_step
    m = fingertip.build(first_step_cmd, *first_step_args, **first_step_kwargs,
                        fingertip_last_step=(not rest_of_the_steps))

    for i, (step_cmd, step_args, step_kwargs) in enumerate(rest_of_the_steps):
        last_step = i == len(rest_of_the_steps) - 1
        m = m.apply(step_cmd, *step_args, **step_kwargs,
                    fingertip_last_step=last_step)

    if m:
        success_log = m
        fingertip.util.log.plain()
        DEBUG = os.getenv('FINGERTIP_DEBUG') == '1'
        msg = (f'For more details, check {success_log} '
               'or set FINGERTIP_DEBUG=1.'
               if not DEBUG else f'Logfile: {success_log}')
        fingertip.util.log.info(f'Success. {msg}')
    else:
        msg = f'Success, no log. Rerun with FINGERTIP_DEBUG=1 for more info.'


if __name__ == '__main__':
    main()
