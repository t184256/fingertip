# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

"""
Helper functions for fingertip: logging.
"""

import logging
import os
import sys

import coloredlogs

LEVELS = coloredlogs.parse_encoded_styles(
    'debug=grey,faint;info=blue;warning=yellow;error=red;critical=red,bold'
)
FIELDS = coloredlogs.parse_encoded_styles(
    'relativeCreated=grey,faint;funcName=grey,faint;pathname=grey,faint;'
    'module=grey,faint'
)

FMT_DEBUG = ('%(relativeCreated)dms %(pathname)s:%(lineno)d %(funcName)s\n'
             '%(module)s: %(message)s')
FMT_NORMAL = '%(module)s: %(message)s'

logger = logging.getLogger(sys.argv[0])
if os.getenv('FINGERTIP_DEBUG') == '1':
    coloredlogs.install(logger=logger, milliseconds=True, level_styles=LEVELS,
                        field_styles=FIELDS, level='DEBUG', fmt=FMT_DEBUG)
else:
    coloredlogs.install(logger=logger, milliseconds=True, level_styles=LEVELS,
                        field_styles=FIELDS, level='INFO', fmt=FMT_NORMAL)

fatal, error, warn = logger.critical, logger.error, logger.warning
info, debug = logger.info, logger.debug


def abort(*args, **kwargs):
    fatal(*args, **kwargs)
    sys.exit(1)
