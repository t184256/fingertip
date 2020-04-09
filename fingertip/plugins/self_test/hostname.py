# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2020 Red Hat, Inc., see CONTRIBUTORS.

import fingertip


@fingertip.transient
def main(m):
    with m:
        r = m('hostname -f')
        assert r.out.endswith('.fingertip.local\n')
        assert m('hostname -d').out == 'fingertip.local\n'
