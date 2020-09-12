# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

"""
Helper functions for fingertip: scheduling a cleanup job with systemd
"""
# TODO: clean up even more?

import shutil
import subprocess

from fingertip.util import log


def schedule():
    # Do this only if fingertip is in PATH
    if not shutil.which("fingertip"):
        log.debug('No `fingertip` found in PATH. Not scheduling '
                  'automatic cleanup.')
        return

    # Skip if systemd is not available
    if not shutil.which('systemd-run') or not shutil.which('systemctl'):
        log.warning('It looks like systemd is not available. '
                    'No cleanup is scheduled! If you are running out of disk, '
                    'space, run `fingertip cleanup periodic` manually.')
        return

    # If the timer is already installed skip installation too
    p = subprocess.run(['systemctl', '--user', 'is-active', '--quiet',
                       'fingertip-cleanup.timer'])
    if p.returncode == 0:
        log.debug('The systemd timer handling cleanup is already installed '
                  'and running.')
        return

    # Run twice a day
    log.info('Scheduling cleanup to run every two hours')
    subprocess.run(['systemd-run', '--unit=fingertip-cleanup', '--user',
                    '--on-calendar=0/2:00:00',
                    'fingertip', 'cleanup', 'periodic'])
