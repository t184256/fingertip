# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import fingertip


@fingertip.transient
def main(m, next_plugin, *args, **kwargs):
    with m:
        m.apply(next_plugin, *args, **kwargs)
