# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import functools
import os
import cloudpickle

import fingertip.exec
from fingertip import step_loader, expiration
from fingertip.util import hooks, lock, log, temp, path


def transient(func):
    func.transient = True

    @functools.wraps(func)
    def wrapper(*a, **kwa):
        r = func(*a, **kwa)
        assert r is None

    return wrapper


class Machine:
    def __init__(self, sealed=True, expire_in=7*24*3600):
        self.hooks = hooks.HookManager()
        os.makedirs(path.MACHINES, exist_ok=True)
        self.path = temp.disappearing_dir(path.MACHINES)
        self._parent_path = path.MACHINES
        self._link_as = None  # what are we building?
        # States: loaded -> spun_up -> spun_down -> saved/dropped
        self._state = 'spun_down'
        self._transient = False
        self._up_counter = 0
        self.sealed = sealed
        self.expiration = expiration.Expiration(expire_in)

    def __call__(self, *args, **kwargs):  # a convenience method
        return fingertip.exec.nice_exec(self, *args, **kwargs)

    def transient(self):
        self._transient = True
        return self

    def __enter__(self):
        log.debug(f'state={self._state}')
        assert (self._state == 'loaded' and not self._up_counter or
                self._state == 'spun_up' and self._up_counter)
        if not self._up_counter:
            assert self._state == 'loaded'
            self.hooks.up()
            self._state = 'spun_up'
        self._up_counter += 1
        return self

    def __exit__(self, exc_type, *_):
        assert self._state == 'spun_up'
        self._up_counter -= 1
        if not self._up_counter:
            if not self._transient:
                self.hooks.down.in_reverse()
                self._state = 'spun_down'
            else:
                self.hooks.drop.in_reverse()
                self._state = 'dropped'
            if not exc_type and self._link_as:
                self._finalize()

    def _finalize(self, link_as=None, name_hint=None):
        log.debug(f'finalize hint={name_hint} link_as={link_as} {self._state}')
        if link_as and self._state == 'spun_down':
            self.hooks.save.in_reverse()
            temp_path = self.path
            self.path = temp.unique_dir(self._parent_path, hint=name_hint)
            log.debug(f'saving to temp {temp_path}')
            self._state = 'saving'
            with open(os.path.join(temp_path, 'machine.clpickle'), 'wb') as f:
                cloudpickle.dump(self, f)
            log.debug(f'moving {temp_path} to {self.path}')
            os.rename(temp_path, self.path)
            self._state == 'saved'
            link_this = self.path
        else:
            assert self._state in ('spun_down', 'loaded', 'dropped')
            log.info(f'discarding {self.path}')
            temp.remove(self.path)
            link_this = self._parent_path
            self._state = 'dropped'
        if (link_this and link_as and
                os.path.realpath(link_as) != os.path.realpath(link_this)):
            log.debug(f'linking {link_this} to {link_as}')
            if os.path.lexists(link_as):
                if os.path.exists(link_as) and not needs_a_rebuild(link_as):
                    log.abort(f'Refusing to overwrite fresh {link_as}')
                os.unlink(link_as)
            os.symlink(link_this, link_as)
            return link_as

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
        end_goal = self._link_as

        lock_path = os.path.join(self._parent_path, '.' + tag + '-lock')
        do_lock = not hasattr(func, 'transient')
        if do_lock:
            log.info(f'acquiring lock for {tag}...')
        with lock.MaybeLock(lock_path, lock=do_lock):
            if os.path.exists(new_mpath) and not needs_a_rebuild(new_mpath):
                # sweet, scratch this instance, fast-forward to cached result
                log.info(f'reusing {step} @ {new_mpath}')
                self._finalize()
                clone_from_path = new_mpath
            else:
                # loaded, not spun up, step not cached: perform step, cache
                log.info(f'building (and, possibly, caching) {tag}')
                m = func(self, *args, **kwargs)
                if m and not m._transient:  # normal step, rebase to its result
                    m._finalize(link_as=new_mpath, name_hint=tag)
                    clone_from_path = new_mpath
                else:  # transient step
                    clone_from_path = self._parent_path
        return clone_and_load(clone_from_path, link_as=end_goal)


def _load_from_path(data_dir_path):
    log.debug(f'load from {data_dir_path}')
    with open(os.path.join(data_dir_path, 'machine.clpickle'), 'rb') as f:
        m = cloudpickle.load(f)
    assert m._state == 'saving'
    m._state = 'loading'
    assert m.path == data_dir_path
    assert m._parent_path == os.path.realpath(os.path.dirname(data_dir_path))
    m.hooks.load()
    m._state = 'loaded'
    return m


def clone_and_load(from_path, link_as=None, name_hint=None):
    log.debug(f'clone {from_path} {link_as}')
    if from_path is None:  # TODO: remove later
        log.abort(f'from_path == None')
    temp_path = temp.disappearing_dir(from_path, hint=name_hint)
    log.debug(f'temp = {temp_path}')
    os.makedirs(temp_path, exist_ok=True)
    with open(os.path.join(from_path, 'machine.clpickle'), 'rb') as f:
        m = cloudpickle.load(f)
    m.hooks.clone(temp_path)
    m._parent_path = os.path.realpath(from_path)
    m.path = temp_path
    m._link_as = link_as
    with open(os.path.join(m.path, 'machine.clpickle'), 'wb') as f:
        cloudpickle.dump(m, f)
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
            first._finalize(link_as=mpath, name_hint=tag)
    return clone_and_load(mpath)


OFFLINE = os.getenv('FINGERTIP_OFFLINE', '0') != '0'


def needs_a_rebuild(mpath):
    with open(os.path.join(mpath, 'machine.clpickle'), 'rb') as f:
        m = cloudpickle.load(f)
    expired = m.expiration.is_expired()
    if expired:
        log.debug(f'{mpath} has expired at {m.expiration.pretty()}')
    else:
        log.debug(f'{mpath} is valid until {m.expiration.pretty()}')
    if OFFLINE and expired:
        log.warn(f'{mpath} expired at {m.expiration.pretty()}, '
                 'but offline mode is enabled, so, reusing it')
    return expired and not OFFLINE
