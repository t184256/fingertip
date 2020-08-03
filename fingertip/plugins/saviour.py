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
    if os.path.exists(base) and not os.path.exists(dst):
        reflink.always(base, dst, preserve=True)
    run = log.pipe_powered(subprocess.run,
                           stdout=logging.INFO, stderr=logging.WARNING)
    run(['rsync', '-rvt', '--partial', '--del', '--delete-excluded'] +
        (['--copy-dest', base] if os.path.isdir(base) else []) +
        sum([['--exclude', e] for e in excludes], []) + options +
        [src, dst], check=True)


def method_git(log, src, base, dst):
    fingertip.util.log.info(f'removing {dst}...')
    _remove(dst)
    r = git.Repo.clone_from(src, dst, mirror=True,
                            dissociate=True, reference_if_able=base)
    r.git.update_server_info()
    with open(os.path.join(dst, 'hooks/post-update.sample'), 'w') as f:
        f.write('#!/usr/bin/sh\nexec git update-server-info')


def method_reposync(log, src, base, dst,
                    arches=['noarch', 'x86_64'], source=True, options=[]):
    if os.path.exists(base) and not os.path.exists(dst):
        reflink.always(base, dst, preserve=True)
    repo_desc_for_mirroring = textwrap.dedent(f'''
        [repo]
        baseurl = {src}
        name = repo
        enabled = 1
        gpgcheck = 0
    ''')
    repodir = temp.disappearing_dir()
    with open(os.path.join(repodir, f'whatever.repo'), 'w') as f:
        f.write(repo_desc_for_mirroring)
    run = log.pipe_powered(subprocess.run,
                           stdout=logging.INFO, stderr=logging.WARNING)
    run(['dnf', f'--setopt=reposdir={repodir}', 'reposync', '--newest-only',
         f'--download-path={dst}', '--norepopath',
         '--download-metadata', '--delete', '--repoid=repo'] +
        [f'--arch={arch}' for arch in arches] + options +
        (['--source'] if source else []),
        check=True)


def method_command(log, src, base, dst, command='false', reuse=True):
    fingertip.util.log.info(f'removing {dst}...')
    _remove(dst)
    if reuse and os.path.exists(base):
        reflink.always(base, dst, preserve=True)
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
    if len(args) >= 1:
        subcmd, *args = args
        if subcmd == 'mirror':
            return mirror(*args)
        if subcmd == 'deduplicate' and not args:
            return deduplicate()
    log.error('usage: ')
    log.error('    fingertip saviour mirror <config-file> [<what-to-mirror>]')
    log.error('    fingertip saviour deduplicate')
    raise SystemExit()


def mirror(config, *what_to_mirror):
    total_failures = []
    failures = collections.defaultdict(list)
    with open(config) as f:
        config = ruamel.yaml.YAML(typ='safe').load(f)
    hows, whats = config['how'], config['what']
    for resource_name in what_to_mirror or whats.keys():
        s = whats[resource_name]
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
        sources = (how['sources'] if 'sources' in how else [how['url']])
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

            sublog.info('removing old front...')
            os.makedirs(os.path.dirname(symlink), exist_ok=True)
            os.makedirs(os.path.dirname(front), exist_ok=True)
            if not os.path.lexists(back_symlink):
                os.symlink(back, back_symlink)
            os.replace(back_symlink, symlink)
            if os.path.exists(front):
                assert front.startswith(path.SAVIOUR)
                _remove(front)

            sublog.info('setting up a new front...')
            reflink.always(back, front, preserve=True)
            if not os.path.lexists(front_symlink):
                os.symlink(front, front_symlink)
            os.replace(front_symlink, symlink)

            sublog.info('removing old back...')
            if os.path.exists(back):
                assert back.startswith(path.SAVIOUR)
                _remove(back)

    if total_failures:
        fingertip.util.log.error(f'failed: {", ".join(total_failures)}')
        raise SystemExit()


def deduplicate():
    os.system(f'fdupes -r "{path.SAVIOUR}" | duperemove --fdupes')
