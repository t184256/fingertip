# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import itertools
import sys

import fingertip
import fingertip.util.log

from fingertip.util import optional_pretty_backtraces  # noqa: F401


def parse_kwarg(kwarg):
    key, val = kwarg.split('=', 1) if '=' in kwarg else (kwarg, True)
    return key.replace('-', '_'), val


def parse_subcmd(subcmd, *all_args):
    args = [a for a in all_args if not a.startswith('--')]
    kwargs = [parse_kwarg(a[2:])
              for a in sorted(all_args) if a.startswith('--')]
    return subcmd, args, dict(kwargs)


def main():
    fingertip.util.log.nicer()

    args = sys.argv[1:]
    subcmds = [list(ws)
               for x, ws in itertools.groupby(args, lambda w: w != '+') if x]
    first_step, *rest_of_the_steps = [parse_subcmd(*sc) for sc in subcmds]

    first_step_cmd, first_step_args, first_step_kwargs = first_step
    m = fingertip.build(first_step_cmd, *first_step_args, **first_step_kwargs)

    for step_cmd, step_args, step_kwargs in rest_of_the_steps:
        m = m.apply(step_cmd, *step_args, **step_kwargs)

    fingertip.util.log.plain()


if __name__ == '__main__':
    main()
