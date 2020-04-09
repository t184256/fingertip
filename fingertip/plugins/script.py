# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import fingertip


# fingertip ... + script.run script  -  no cache, never persist, always rerun
# fingertip ... + script.run script + ssh  -  cache just for this invocation
# fingertip ... + script.run script --cache=1h  -  try to cache for 1h at most
# fingertip ... + script.run script --cache=1h + ssh  -  try reusing the cache
# fingertip ... + transient script.run script --cache=1h + ssh  -  revert


def _should_run_be_transient(scriptpath, cache=0, no_unseal=False):
    return False if cache else 'last'


@fingertip.transient(when=_should_run_be_transient)
def run(m, scriptpath, cache=0, no_unseal=False):
    m.log.debug(f'Plugin: script, scriptpath: {scriptpath}, '
                f'cache: {cache}, no_unseal: {no_unseal}')
    m = m if no_unseal else m.apply('unseal')
    scriptname = 'uploaded_script'

    with m:
        if cache is not True:
            m.expiration.cap(cache)
        m.expiration.depend_on_a_file(scriptpath)

        m.log.debug(f'uploading {scriptpath} into {scriptname}')
        m.ssh.upload(scriptpath, dst=scriptname)
        m.log.debug(f'uploaded {scriptpath} into {scriptname}')
        m(f'chmod +x {scriptname}')
        m.log.plain()
        m(f'./{scriptname}')
        m.log.nicer()
    return m
