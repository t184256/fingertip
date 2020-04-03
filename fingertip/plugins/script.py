# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.


def run(m, scriptpath, transient=False, no_unseal=False):
    m.log.debug(f'Plugin: script, scriptpath: {scriptpath}, '
                f'transient: {transient}, no_unseal: {no_unseal}')
    m = m if no_unseal else m.apply('unseal')
    scriptname = 'uploaded_script'
    m.expiration.depend_on_a_file(scriptpath)
    with m:
        m.log.debug(f'uploading {scriptpath} into {scriptname}')
        m.ssh.upload(scriptpath, dst=scriptname)
        m.log.debug(f'uploaded {scriptpath} into {scriptname}')
        m(f'chmod +x {scriptname}')
        m.log.plain()
        m(f'./{scriptname}')
        m.log.nicer()
    if not transient:
        return m
