# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2020 Red Hat, Inc., see CONTRIBUTORS.

"""
`fingertip saviour <saviour-config.yml>` takes a config file and
locally mirrors whatever is specified there
(usually package repositories and GIT repositories)
in a way that's usable with local and non-local fingertip instances.
Example config is available as saviour.example.yml

The result is going to be available at
~/.cache/fingertip/saviour/http://whatever/you/have/mirrored.
Exporting it with an HTTP server as, e.g., http://cachy/saviour
(i.e., as http://cachy/saviour/http://whatever/you/have/mirrored)
will make it possible to make some other fingertip use this cache
with http_cache and git_cache
by setting FINGERTIP_SAVIOUR='cachy/saviour'.

For real applications you probably want
'local,cachy/saviour,direct' or 'local,cachy/saviour,cached+direct'.

Requires reflinking support (btrfs or XFS CoW).
Requires git and rsync, also dnf for dnf method.
"""

import collections
import logging
import os
import shutil
import subprocess
import textwrap
import traceback

import git
import ruamel.yaml

import fingertip.expiration
import fingertip.machine
from fingertip.util import lock, log, path, reflink, temp


def _remove(tgt):
    assert tgt.startswith(path.SAVIOUR)
    if not os.path.exists(tgt):
        pass
    elif os.path.isdir(tgt):
        shutil.rmtree(tgt)
    else:
        os.unlink(tgt)


def method_rsync(log, src, base, dst, options=[], excludes=[]):
    if os.path.exists(base):
        reflink.always(base, dst)
    run = log.pipe_powered(subprocess.run,
                           stdout=logging.INFO, stderr=logging.WARNING)
    run(['rsync', '-rv', '--partial', '--del', '--delete-excluded'] +
        sum([['--exclude', e] for e in excludes], []) + options +
        [src, dst], check=True)


def method_git(log, src, base, dst):
    r = git.Repo.clone_from(src, dst, mirror=True,
                            dissociate=True, reference_if_able=base)
    r.git.update_server_info()
    with open(os.path.join(dst, 'hooks/post-update.sample'), 'w') as f:
        f.write('#!/usr/bin/sh\nexec git update-server-info')


def method_reposync(log, src, base, dst,
                    arches=['noarch', 'x86_64'], source=True, options=[]):
    if os.path.exists(base):
        reflink.always(base, dst)
    repo_id, parent_dir = os.path.basename(dst), os.path.dirname(dst)
    repo_desc_for_mirroring = textwrap.dedent(f'''
        [{repo_id}]
        baseurl = {src}
        enabled = 1
        gpgcheck = 0
        name = {repo_id}
    ''')
    repodir = temp.disappearing_dir()
    with open(os.path.join(repodir, f'whatever.repo'), 'w') as f:
        f.write(repo_desc_for_mirroring)
    run = log.pipe_powered(subprocess.run,
                           stdout=logging.INFO, stderr=logging.WARNING)
    run(['dnf', f'--setopt=reposdir={repodir}', 'reposync', '--newest-only',
         '--download-metadata', '--delete', '--repoid', f'{repo_id}'] +
        [f'--arch={arch}' for arch in arches] + options +
        (['--source'] if source else []),
        cwd=parent_dir, check=True)


def method_command(log, src, base, dst, command='false', reuse=True):
    if reuse and os.path.exists(base):
        reflink.always(base, dst)
    env = os.environ.copy()
    env['SRC'], env['BASE'], env['DST'] = src, base, dst
    run = log.pipe_powered(subprocess.run,
                           stdout=logging.INFO, stderr=logging.WARNING)
    log.info(command.replace('$SRC', src).replace('$BASE', base)
                    .replace('$DST', dst))
    run(command, shell=True, cwd=os.path.dirname(dst), env=env,
        check=True)


@fingertip.transient
def main(*args):
    if len(args) >= 2:
        subcmd, config, *what_to_mirror = args
        if subcmd == 'mirror' and os.path.exists(config):
            return mirror(config, args)
    log.error('usage: ')
    log.error('    fingertip saviour mirror <config-file> [<what-to-mirror>]')
    raise SystemExit()


def mirror(config, *what_to_mirror):
    total_failures = []
    failures = collections.defaultdict(list)
    with open(config) as f:
        config = ruamel.yaml.YAML(typ='safe').load(f)
    hows, whats = config['how'], config['what']
    for resource_name, s in whats.items():
        log.debug(f'processing {resource_name}...')

        if s is None:
            how, suffix = resource_name, ''
        elif '/' in s:
            how, suffix = s.split('/', 1)
            suffix = '/' + suffix
        else:
            how, suffix = s, ''

        try:
            how = hows[how]
        except KeyError:
            log.error(f'missing how section on {how}')
            raise SystemExit()

        url = how['url'] + suffix
        method = how['method']
        sources = (how['sources'] if 'sources' in how else [url])
        sources = [s + suffix for s in sources]
        extra_args = {k: v for k, v in how.items()
                      if k not in ('url', 'sources', 'method')}

        if f'method_{method}' not in globals():
            log.error(f'unsupported method {method}')
            raise SystemExit()

        meth = globals()[f'method_{method}']
        symlink = path.saviour(url.rstrip('/'))
        front_symlink = path.saviour('_', resource_name) + '-FRONT'
        back_symlink = path.saviour('_', resource_name) + '-BACK'
        front = path.saviour('_', resource_name, 'front')
        back = path.saviour('_', resource_name, 'back')
        lockfile = path.saviour('_', resource_name) + '-lock'
        assert front.startswith(path.SAVIOUR)
        assert back.startswith(path.SAVIOUR)

        log.info(f'locking {resource_name}...')
        with lock.Lock(lockfile):
            _remove(back)
            os.makedirs(os.path.dirname(back), exist_ok=True)

            fingertip.util.log.info(f'mirroring {resource_name}...')
            sublog = log.Sublogger(f'{method} {resource_name}')

            for source in sources:
                fingertip.util.log.info(f'trying {source}...')
                try:
                    meth(sublog, source, front, back, **extra_args)
                    assert os.path.exists(back)
                    break
                except Exception as _:
                    traceback.print_exc()
                    failures[resource_name].append(source)
                    fingertip.util.log.warning(f'failed to mirror {source}')

            if len(failures[resource_name]) == len(sources):
                fingertip.util.log.error(f'failed to mirror {resource_name} '
                                         f'from all {len(sources)} sources')
                total_failures.append(resource_name)
                continue

            os.makedirs(os.path.dirname(symlink), exist_ok=True)
            os.makedirs(os.path.dirname(front), exist_ok=True)
            if not os.path.lexists(back_symlink):
                os.symlink(back, back_symlink)
            os.replace(back_symlink, symlink)
            if os.path.exists(front):
                assert front.startswith(path.SAVIOUR)
                _remove(front)

            reflink.always(back, front)

            if not os.path.lexists(front_symlink):
                os.symlink(front, front_symlink)
            os.replace(front_symlink, symlink)
            if os.path.exists(back):
                assert back.startswith(path.SAVIOUR)
                _remove(back)

    if total_failures:
        fingertip.util.log.error(f'failed: {", ".join(total_failures)}')
        raise SystemExit()

    os.system('fdupes -r . | duperemove --fdupes')
