# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

# The real code is in fingertip.util.vendored.http_cache,
# this is just a thin instantiator.

import os
from pathlib import Path

import fingertip.util.log
import fingertip.util.path
from fingertip.util.vendored.http_cache import (
    FetchSourceLocal,
    FetchSourceSaviour,
    FetchSourceDirect,
    HTTPCache as _HTTPCache,
)


def _saviour_sources():
    SAVIOUR_DEFAULTS = 'local,cached+direct'
    s = os.getenv('FINGERTIP_SAVIOUR', SAVIOUR_DEFAULTS) or SAVIOUR_DEFAULTS
    sources = []
    for t in s.split(','):
        cached = t.startswith('cached+')
        source_name = t[len('cached+'):] if cached else t

        if source_name == 'local':
            sources.append(FetchSourceLocal(Path(fingertip.util.path.SAVIOUR)))
        elif source_name == 'direct':
            sources.append(FetchSourceDirect(cached=cached))
        else:  # saviour URL
            sources.append(FetchSourceSaviour(source_name, cached=cached))

    assert sources, 'FINGERTIP_SAVIOUR must define at least one source'
    return sources


def HTTPCache():
    WARN_ON_DIRECT = os.getenv('FINGERTIP_SAVIOUR_WARN_ON_DIRECT', None) == '1'
    return _HTTPCache(log=fingertip.util.log,
                      cache_dir=fingertip.util.path.downloads('cache'),
                      fixups_dir=fingertip.util.path.downloads('fixups'),
                      sources=_saviour_sources(),
                      warn_on_direct=WARN_ON_DIRECT)
