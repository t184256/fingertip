# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import git

import os
import shutil
import tarfile


from fingertip.util import log, lock, path, reflink, temp


SAVIOUR_DEFAULTS = 'local,cached+direct'


def saviour_sources():
    return [(t[len('cached+'):] if t.startswith('cached+') else t,
             t.startswith('cached+'))
            for t in
            os.getenv('FINGERTIP_SAVIOUR', SAVIOUR_DEFAULTS).split(',')]


def _remove(p):
    assert p.startswith(path.DOWNLOADS)
    if os.path.exists(p):
        shutil.rmtree(p)


class Repo(git.Repo, lock.Lock):
    def __init__(self, url, *path_components, enough_to_have=None):
        assert path_components
        self.url = url
        cache_path = path.downloads('git', *path_components, makedirs=True)
        self.path = temp.disappearing_dir(os.path.dirname(cache_path),
                                          path_components[-1])
        lock_working_copy_path = self.path + '-lock'
        lock_cache_path = cache_path + '-lock'
        lock.Lock.__init__(self, lock_working_copy_path)
        update_not_needed = None
        sources = saviour_sources()
        self.self_destruct = False
        with lock.Lock(lock_cache_path), lock.Lock(lock_working_copy_path):
            cache_is_enough = False
            if os.path.exists(cache_path):
                try:
                    cr = git.Repo(cache_path)
                    cache_is_enough = enough_to_have and (
                        enough_to_have in (t.name for t in cr.tags) or
                        enough_to_have in (h.name for h in cr.heads) or
                        enough_to_have in (c.hexsha for c in cr.iter_commits())
                        # that's not all revspecs, but best-effort is fine
                    )
                except git.GitError as e:
                    log.error(f'something wrong with git cache {cache_path}')
                    log.error(str(e))
                _remove(self.path)

            for i, (source, cache) in enumerate(sources):
                last_source = i == len(sources) - 1

                if cache and cache_is_enough:
                    log.info(f'not re-fetching {url} from {source} '
                             f'because {enough_to_have} '
                             'is already present in cache')
                    git.Repo.clone_from(cache_path, self.path, mirror=True)
                    break

                if source == 'local':
                    surl = path.saviour(url).replace('//', '/')  # workaround
                    if not os.path.exists(surl) and not last_source:
                        continue
                    log.info(f'cloning {url} from local saviour mirror')
                    git.Repo.clone_from(surl, self.path, mirror=True)
                    break
                elif source == 'direct':
                    surl = url
                else:
                    surl = source + '/' + url
                    surl = 'http://' + surl if '://' not in source else surl

                log.info(f'cloning {url} from {source} '
                         f'cache_exists={os.path.exists(cache_path)}...')
                try:
                    # TODO: bare clone
                    # no harm in referencing cache, even w/o cached+
                    git.Repo.clone_from(surl, self.path, mirror=True,
                                        dissociate=True,
                                        reference_if_able=cache_path)
                except git.GitError:
                    log.warning(f'could not clone {url} from {source}')
                    if last_source:
                        raise
                    continue
                break

            _remove(cache_path)
            reflink.auto(self.path, cache_path)
            git.Repo.__init__(self, self.path)
            self.remotes[0].set_url(url)
        self.self_destruct = True

    def __enter__(self):
        lock.Lock.__enter__(self)
        git.Repo.__enter__(self)
        return self

    def __exit__(self, *args):
        if self.self_destruct:
            _remove(self.path)
        git.Repo.__exit__(self, *args)
        lock.Lock.__exit__(self, *args)


class Checkout(git.Repo, lock.Lock):
    def __init__(self, url, *path_components, enough_to_have=None):
        with Repo(url, *path_components, enough_to_have=enough_to_have) as r:
            cache_path = path.downloads('git', *path_components, makedirs=True)
            self.path = temp.disappearing_dir(os.path.dirname(cache_path),
                                              path_components[-1])
            self.self_destruct = False
            git.Repo.clone_from(r.path, self.path)
        git.Repo.__init__(self, self.path)

    def __enter__(self):
        git.Repo.__enter__(self)
        return self

    def __exit__(self, *args):
        git.Repo.__exit__(self, *args)
        _remove(self.path)


def upload_clone(m, url, path_in_m, rev=None, rev_is_enough=True):
    assert hasattr(m, 'ssh')
    with m:
        kwa = {} if not rev_is_enough else {'enough_to_have': rev}
        with Repo(url, url.replace('/', '::'), **kwa) as repo:
            tar = temp.disappearing_file()
            tar_in_m = f'/.tmp-{os.path.basename(tar)}'
            extracted_in_m = f'/.tmp-{os.path.basename(tar)}-extracted'
            log.info(f'packing {url} checkout...')
            with tarfile.open(tar, 'w') as tf:
                tf.add(repo.path, arcname=extracted_in_m)
            log.info(f'uploading {url} checkout...')
            m.ssh.upload(tar, tar_in_m)
        log.info(f'performing {url} checkout...')
        m(f'''
            set -uex
            tar xmf {tar_in_m} -C /
            mkdir -p {path_in_m}
            git clone -n {extracted_in_m} {path_in_m}
            cd {path_in_m}
            git remote set-url origin {url}
            git checkout {f'{rev}' if rev else 'origin/HEAD'}
            rm -rf {extracted_in_m}
            rm -f {tar_in_m}
        ''')
    return m


def upload_contents(m, url, path_in_m, rev=None, rev_is_enough=True):
    assert hasattr(m, 'ssh')
    with m:
        kwa = {} if not rev_is_enough else {'enough_to_have': rev}
        with Repo(url, url.replace('/', '::'), **kwa) as repo:
            tar = temp.disappearing_file()
            log.info(f'packing {url} contents at rev {rev}...')
            tar_in_m = f'/.tmp-{os.path.basename(tar)}'
            with open(tar, 'wb') as tf:
                repo.archive(tf, treeish=rev, prefix=path_in_m + '/')
            log.info(f'uploading {url} contents at rev {rev}...')
            m.ssh.upload(tar, tar_in_m)
        log.info(f'unpacking {url} contents at rev {rev}...')
        m(f'''
            set -uex
            tar xmf {tar_in_m} -C /
            rm -f {tar_in_m}
        ''')
    return m
