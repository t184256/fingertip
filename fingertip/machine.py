# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import collections
import functools
import os
import pickle

from fingertip import step_loader, expiration
from fingertip.util import lock, log, temp, path


def transient(func):
    func.transient = True

    @functools.wraps(func)
    def wrapper(*a, **kwa):
        r = func(*a, **kwa)
        assert r is None

    return wrapper


class Machine:
    def __init__(self, sealed=True, expire_in=7*24*3600):
        self._hooks = collections.defaultdict(list)
        os.makedirs(path.MACHINES, exist_ok=True)
        self.path = temp.disappearing_dir(path.MACHINES)
        self._parent_path = path.MACHINES
        self._link_to = None  # what are we building?
        # States: loaded -> spun_up -> spun_down -> saved/dropped
        self._state = 'spun_down'
        self._transient = False
        self._up_counter = 0
        self.sealed = sealed
        self.expiration = expiration.Expiration(expire_in)

    def transient(self):
        self._transient = True
        return self

    def __enter__(self):
        log.debug(f'state={self._state}')
        assert (self._state == 'loaded' and not self._up_counter or
                self._state == 'spun_up' and self._up_counter)
        if not self._up_counter:
            assert self._state == 'loaded'
            self._exec_hooks('up')
            self._state = 'spun_up'
        self._up_counter += 1
        return self

    def __exit__(self, exc_type, *_):
        assert self._state == 'spun_up'
        self._up_counter -= 1
        if not self._up_counter:
            if not self._transient:
                self._exec_hooks('down', in_reverse=True)
                self._state = 'spun_down'
            else:
                self._exec_hooks('drop', in_reverse=True)
                self._state = 'dropped'
            if not exc_type and self._link_to:
                self._finalize()

    def hook(self, **kwargs):
        for hook_type, hook in kwargs.items():
            self._hooks[hook_type].append(hook)

    def _exec_hooks(self, hook_type, *args, in_reverse=False, **kwargs):
        log.debug(f'firing {hook_type} hooks')
        hooks = self._hooks[hook_type]
        for hook in hooks if not in_reverse else hooks[::-1]:
            log.debug(f'hook {hook_type} {hook}')
            hook(self, *args, **kwargs)

    def _finalize(self, link_to=None, name_hint=None):
        log.debug(f'finalize hint={name_hint} link_to={link_to} {self._state}')
        if link_to and self._state == 'spun_down':
            self._exec_hooks('save', in_reverse=True)
            temp_path = self.path
            self.path = temp.unique_dir(self._parent_path, hint=name_hint)
            log.debug(f'saving to temp {temp_path}')
            self._state = 'saving'
            with open(os.path.join(temp_path, 'machine.pickle'), 'wb') as f:
                pickle.dump(self, f)
            log.debug(f'moving {temp_path} to {self.path}')
            os.rename(temp_path, self.path)
            self._state == 'saved'
            link_from = self.path
        else:
            assert self._state in ('spun_down', 'loaded', 'dropped')
            log.info(f'discarding {self.path}')
            temp.remove(self.path)
            link_from = self._parent_path
            self._state = 'dropped'
        if (link_from and link_to and
                os.path.realpath(link_to) != os.path.realpath(link_from)):
            log.debug(f'linking {link_from} to {link_to}')
            if os.path.lexists(link_to):
                if os.path.exists(link_to):
                    log.abort(f'Refusing to overwrite {link_to}, try again?')
                os.unlink(link_to)
            os.symlink(link_from, link_to)
            return link_to

    def apply(self, step, *args, **kwargs):
        func, tag = step_loader.func_and_autotag(step, *args, **kwargs)
        log.debug(f'apply {self.path} {step} {func} {args} {kwargs}')
        if self._state == 'spun_up':
            log.debug(f'applying to unclean')
            return func(self, *args, **kwargs)
        elif self._state == 'loaded':
            log.debug(f'applying to clean')
            return self._cache_aware_apply(step, tag, func, *args, **kwargs)
        else:
            log.abort(f'apply to state={self._state}')

    def _cache_aware_apply(self, step, tag, func, *args, **kwargs):
        assert self._state == 'loaded'

        # Could there already be a cached result?
        log.debug(f'PATH {self.path} {tag}')
        new_mpath = os.path.join(self._parent_path, tag)
        end_goal = self._link_to

        lock_path = os.path.join(self._parent_path, '.' + tag + '-lock')
        do_lock = not hasattr(func, 'transient')
        if do_lock:
            log.info(f'acquiring lock for {tag}...')
        with lock.MaybeLock(lock_path, lock=do_lock):
            if os.path.exists(new_mpath):
                # sweet, scratch this instance, fast-forward to cached result
                log.info(f'reusing {step} @ {new_mpath}')
                self._finalize()
                clone_from_path = new_mpath
            else:
                # loaded, not spun up, step not cached: perform step, cache
                log.info(f'building (and, possibly, caching) {tag}')
                m = func(self, *args, **kwargs)
                if m and not m._transient:  # normal step, rebase to its result
                    m._finalize(link_to=new_mpath, name_hint=tag)
                    clone_from_path = new_mpath
                else:  # transient step
                    clone_from_path = self._parent_path
        return clone_and_load(clone_from_path, link_to=end_goal)

    def unseal(self):
        if self.sealed:
            self.sealed = False
            self._exec_hooks('unseal')


def _load_from_path(data_dir_path):
    log.debug(f'load from {data_dir_path}')
    with open(os.path.join(data_dir_path, 'machine.pickle'), 'rb') as f:
        m = pickle.load(f)
    assert m._state == 'saving'
    m._state == 'loading'
    assert m.path == data_dir_path
    assert m._parent_path == os.path.realpath(os.path.dirname(data_dir_path))
    m._exec_hooks('load')
    m._state = 'loaded'
    return m


def clone_and_load(from_path, link_to=None, name_hint=None):
    log.debug(f'clone {from_path} {link_to}')
    if from_path is None:  # TODO: remove later
        log.abort(f'from_path == None')
    temp_path = temp.disappearing_dir(from_path, hint=name_hint)
    log.debug(f'temp = {temp_path}')
    os.makedirs(temp_path, exist_ok=True)
    with open(os.path.join(from_path, 'machine.pickle'), 'rb') as f:
        m = pickle.load(f)
    m._exec_hooks('clone', m, temp_path)
    m._parent_path = os.path.realpath(from_path)
    m.path = temp_path
    m._link_to = link_to
    with open(os.path.join(m.path, 'machine.pickle'), 'wb') as f:
        pickle.dump(m, f)
    return _load_from_path(temp_path)


def build(first_step, *args, **kwargs):
    func, tag = step_loader.func_and_autotag(first_step, *args, **kwargs)

    # Could there already be a cached result?
    mpath = path.machines(tag)
    lock_path = path.machines('.' + tag + '-lock')
    log.info(f'acquiring lock for {tag}...')
    do_lock = not hasattr(func, 'transient')
    with lock.MaybeLock(lock_path, lock=do_lock):
        if not os.path.exists(mpath):
            first = func(*args, **kwargs)
            if first is None:
                return
            first._finalize(link_to=mpath, name_hint=tag)
    return clone_and_load(mpath)
