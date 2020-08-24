# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import sys
import fingertip


# script.X (X in [run, setup]) without --cache=Y defaults to:
#  * not caching if it is the last plugin in the pipeline
#  * caching for just one invocation
#    if there are further plugins in the pipeline
# Examples:
# fingertip ... + script.X script  -  no cache, never persist, always rerun
# fingertip ... + script.X script + ssh  -  cache just for this invocation
# fingertip ... + script.X script --cache=1h  -  cache for 1h at most
# fingertip ... + script.X script --cache=1h + ssh  -  cache or reuse if fresh
# fingertip ... + transient script.X script + ssh  -  revert
def _should_be_transient(m, scriptpath, cache=0, unseal=True):
    return False if cache else 'last'


def _exec(m, scriptpath, mode=None, last_step=None, cache=0, unseal=True):
    m.log.debug(f'Plugin: script, mode: {mode}, scriptpath: {scriptpath}, '
                f'last_step: {last_step}, cache: {cache}, '
                f'unseal: {unseal}')
    assert mode in ['run', 'test']
    m = m.apply('unseal') if unseal else m
    scriptname = 'uploaded_script'

    with m:
        if cache is not True:
            m.expiration.cap(cache)
        m.expiration.depend_on_a_file(scriptpath)

        m.log.debug(f'uploading {scriptpath} into {scriptname}')
        m.ssh.upload(scriptpath, dst=scriptname)
        m.log.debug(f'uploaded {scriptpath} into {scriptname}')
        m(f'chmod +x {scriptname}')
        if mode == 'test':
            m.hooks.disable_proxy()
            check = False
        elif mode == 'run':
            check = True
        else:
            assert False
        m.log.plain()
        exit_code = m(f'./{scriptname}', check=check).retcode
        m.log.nicer()
        if mode == 'test' and exit_code != 0:
            m.log.warning(f'failed with exit code: {exit_code}')
            if last_step:
                sys.exit(exit_code)
    return m

# execute setup-like scenario, meaning mostly
#  * do not touch proxy settings
#  * traceback on non-zero exit code
@fingertip.transient(when=_should_be_transient)
def run(m, scriptpath, fingertip_last_step=None, cache=0, unseal=True):
    return _exec(m, scriptpath, mode='run',
                 last_step=fingertip_last_step,
                 cache=cache, unseal=unseal)

# run test like scenario, meaning mostly:
#  * disable proxy
#  * do not traceback on non-zero exit code
@fingertip.transient(when=_should_be_transient)
def test(m, scriptpath, fingertip_last_step=None, cache=0, unseal=True):
    return _exec(m, scriptpath, mode='test',
                 last_step=fingertip_last_step,
                 cache=cache, unseal=unseal)
