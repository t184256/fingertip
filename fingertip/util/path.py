# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

"""
Helper functions and constants for fingertip and fingertip plugins: paths.
"""
# TODO: fold into caching_proxy, there should be very few users of this module

import contextlib
import os

import xdg.BaseDirectory

FINGERTIP = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CACHE = os.path.join(xdg.BaseDirectory.xdg_cache_home, 'fingertip')
DOWNLOADS = os.path.join(CACHE, 'downloads')
MACHINES = os.path.join(CACHE, 'machines')
LOGS = os.path.join(CACHE, 'logs')


def easy_accessor(root_path):
    def easy_access_func(*more_path_components, makedirs=False, mkdir=False):
        if makedirs:
            os.makedirs(os.path.join(root_path, *more_path_components[:-1]),
                        exist_ok=True)
        return os.path.join(root_path, *more_path_components)
    return easy_access_func


fingertip = easy_accessor(FINGERTIP)
downloads = easy_accessor(DOWNLOADS)
machines = easy_accessor(MACHINES)
logs = easy_accessor(LOGS)


@contextlib.contextmanager
def wip(normal_path, makedirs=False):
    # WARNING: prone to race conditions
    norm, wip = normal_path, normal_path + '-WIP'
    if os.path.exists(norm):
        assert not os.path.exists(wip), f'Both {norm} and {wip} exist'
        os.rename(norm, wip)
    if makedirs:
        os.makedirs(os.path.dirname(wip.rstrip(os.path.sep)), exist_ok=True)
    yield wip
    os.rename(wip, norm)
