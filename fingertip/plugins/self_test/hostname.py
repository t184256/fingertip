# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2020 Red Hat, Inc., see CONTRIBUTORS.

import fingertip

@fingertip.transient
def main(m):
    with m.transient() as m:
        r = m('hostname')
        assert r.out.endswith('.fingertip.local\n')
        assert m('hostname -d').out == 'fingertip.local\n'

