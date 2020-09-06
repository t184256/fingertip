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
import fnmatch
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


# base is a previous consistent snapshot, dst is a WIP directory
def method_rsync(log, src, base, dst, options=[], excludes=[]):
    run = log.pipe_powered(subprocess.run,
                           stdout=logging.INFO, stderr=logging.WARNING)
    run(['rsync', '-rvt', '--partial', '--del', '--delete-excluded'] +
        (['--copy-dest', base] if os.path.isdir(base) else []) +
        sum([['--exclude', e] for e in excludes], []) + options +
        [src, dst], check=True)


def method_git(log, src, base, dst):
    fingertip.util.log.info(f'removing {dst}...')
    _remove(dst)
    fingertip.util.log.info(f'cloning {src}...')
    r = git.Repo.clone_from(src, dst, mirror=True,
                            dissociate=True, reference_if_able=base)
    r.git.update_server_info()
    with open(os.path.join(dst, 'hooks/post-update.sample'), 'w') as f:
        f.write('#!/usr/bin/sh\nexec git update-server-info')


def method_reposync(log, src, base, dst,
                    arches=['noarch', 'x86_64'], source='auto',
                    metadata='download', options=[]):
    if source == 'auto':
        source = '/source' in src or '/SRPM' in src
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
    run(['dnf', f'--setopt=reposdir={repodir}', 'reposync', '--norepopath',
         f'--download-path={dst}', '--repoid=repo',
         '--delete', '--remote-time'] +
        [f'--arch={arch}' for arch in arches] +
        (['--download-metadata'] if not metadata != 'generate' else []) +
        (['--source'] if source else []) +
        options,
        check=True)
    run = log.pipe_powered(subprocess.run,  # either too silent or too noisy =/
                           stdout=logging.INFO, stderr=logging.INFO)
    createrepo_c_options = ['-v', '--error-exit-val', '--ignore-lock']
    if metadata == 'regenerate':
        log.info('regenerating metadata...')
        run(['createrepo_c'] + createrepo_c_options + ['--update', dst],
            check=True)
    elif metadata == 'generate':
        log.info('generating metadata from scratch...')
        run(['createrepo_c'] + createrepo_c_options + [dst], check=True)


def method_command(log, src, base, dst, command='false', reuse=True):
    if not reuse:
        fingertip.util.log.info(f'removing {dst}...')
        _remove(dst)
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
            return deduplicate(log.Sublogger('deduplicate'))
    log.error('usage: ')
    log.error('    fingertip saviour mirror <config-file> [<what-to-mirror>]')
    log.error('    fingertip saviour deduplicate')
    raise SystemExit()


def _symlink(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + '-tmp'
    if not os.path.lexists(tmp):
        os.symlink(src, tmp)
    os.replace(tmp, dst)


def mirror(config, *what_to_mirror, deduplicate=True):
    total_failures = []
    failures = collections.defaultdict(list)

    with open(config) as f:
        config = ruamel.yaml.YAML(typ='safe').load(f)
    hows, whats = config['how'], config['what']
    if not what_to_mirror:
        what_to_mirror = whats.keys()
    else:
        what_to_mirror = [k for k in whats.keys()
                          if any((fnmatch.fnmatch(k, req)
                                  for req in what_to_mirror))]

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
        # usually symlink points to data, but while we're working on it,
        # it temporarily points to a consistent snapshot of it named `snap`
        data = path.saviour('_', resource_name, 'data')
        snap = path.saviour('_', resource_name, 'snap')
        temp = path.saviour('_', resource_name, 'temp')
        lockfile = path.saviour('_', resource_name) + '-lock'
        assert data.startswith(path.SAVIOUR)
        assert snap.startswith(path.SAVIOUR)
        assert temp.startswith(path.SAVIOUR)

        sublog = log.Sublogger(f'{method} {resource_name}')
        sublog.info('locking...')
        with lock.Lock(lockfile):
            os.makedirs(os.path.dirname(snap), exist_ok=True)

            if os.path.exists(temp):
                sublog.info('removing stale temp...')
                _remove(temp)
            if os.path.exists(symlink):  # it's already published
                if os.path.exists(data) and not os.path.exists(snap):
                    # `data` is present and is the best we have to publish
                    sublog.info('snapshotting...')
                    reflink.always(data, temp, preserve=True)
                    os.rename(temp, snap)
                if os.path.exists(snap):
                    # link to a consistent snapshot while we work on `data`
                    _symlink(snap, symlink)

            for source in sources:
                sublog.info(f'trying {source}...')
                try:
                    meth(sublog, source, snap, data, **extra_args)
                    assert os.path.exists(data)
                    break
                except Exception as _:
                    traceback.print_exc()
                    failures[resource_name].append(source)
                    fingertip.util.log.warning(f'failed to mirror {source}')

            if len(failures[resource_name]) == len(sources):
                sublog.error(f'failed to mirror '
                             f'from all {len(sources)} sources')
                total_failures.append(resource_name)
                continue

            _symlink(data, symlink)
            if os.path.exists(snap):
                os.rename(snap, temp)  # move it out the way asap
                sublog.info('removing now obsolete snapshot...')
                _remove(temp)

            if deduplicate:
                try:
                    _deduplicate(sublog, resource_name, timeout=1)
                except lock.LockTimeout:
                    log.warning('skipped deduplication, db was locked')
    if total_failures:
        fingertip.util.log.error(f'failed: {", ".join(total_failures)}')
        raise SystemExit()
    log.info('saviour has completed mirroring')


def deduplicate(log, *subpath, timeout=None):
    log.info('locking the deduplication db...')
    with lock.Lock(path.saviour('.duperemove.hashfile-lock'), timeout=timeout):
        log.info('deduplicating...')
        run = log.pipe_powered(subprocess.run,
                               stdout=logging.INFO, stderr=logging.WARNING)
        r = run(['duperemove',
                 '--hashfile', path.saviour('.duperemove.hashfile'),
                 '-hdr', path.saviour('_', *subpath)])
        assert r.returncode in (0, 22)  # nothing to deduplicate


_deduplicate = deduplicate
