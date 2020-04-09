# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import fingertip
import fingertip.util.temp


@fingertip.transient
def main(m):
    scriptname = fingertip.util.temp.disappearing_file()
    m.log.debug(f'Scriptname: {scriptname}')
    filename = '/etc/TADAA'
    with open(scriptname, "w") as f:
        f.write(f'#!/bin/sh\n\ntouch {filename}')
    with m.apply('script.run', scriptname) as m:
        assert m(f'ls -1 {filename}').out.strip() == filename
