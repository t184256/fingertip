# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import os
import pickle

from fingertip import step_loader, expiration
from fingertip.util import hooks, lock, log, temp, path


def transient(func):  # the effect should be reverted later, if possible
    func.transient = True
    return func


def terminal(func):  # the effect must be reverted or it must be the final one
    func.terminal = True
    return func


class UnsaveableMachine:
    def __init__(self, sealed=True):
        self.hooks = hooks.HookManager()
        os.makedirs(path.MACHINES, exist_ok=True)
        self.path = temp.disappearing_dir(path.MACHINES)
        # States: initial -> (spun_down <-> spun_up) -> finalized|dropped
        #            ^--------------------------------------/
        self._state = 'initial'
        self._terminal = False  # if true, no graceful shutdown and no spin_up
        self._up_counter = 0
        self.sealed = sealed

    def advance(self):  # these machines cannot fork, only continue evolving
        assert not self._terminal
        if self._state == 'finalized':
            self._state = 'initial'
        return self

    def terminal(self):  # this must be the last stage, there'll be no cleanup
        r = self.advance()
        r._terminal = True
        return r

    def _should_be_dropped_ungracefully(self):
        return self._terminal

    def __enter__(self):
        log.debug(f'enter state={self._state}')
        assert (self._state == 'initial' and not self._up_counter or
                self._state == 'spun_down' and not self._up_counter or
                self._state == 'spun_up' and self._up_counter)
        if not self._up_counter:
            self.hooks.up(self)
            self._state = 'spun_up'
        self._up_counter += 1
        return self

    def __exit__(self, exc_type, *_):
        log.debug('exit')
        assert self._state == 'spun_up'
        self._up_counter -= 1
        if not self._up_counter:
            if not self._should_be_dropped_ungracefully():
                self.hooks.down.in_reverse(self)
                self._state = 'spun_down'
            else:
                self.hooks.drop.in_reverse(self)
                self._state = 'dropped'
            if not exc_type and self._link_to:
                self._finalize()

    def _finalize(self):
        os.shutil.rmtree(self.path)

    def apply(self):


    def _finalize(self, link_to=None, name_hint=None):
        log.debug(f'finalize hint={name_hint} link_to={link_to} {self._state}')
        if link_to and self._state == 'spun_down':
            self.hooks.save.in_reverse(self)
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


def _load_from_path(data_dir_path):
    log.debug(f'load from {data_dir_path}')
    with open(os.path.join(data_dir_path, 'machine.pickle'), 'rb') as f:
        m = pickle.load(f)
    assert m._state == 'saving'
    m._state == 'loading'
    assert m.path == data_dir_path
    assert m._parent_path == os.path.realpath(os.path.dirname(data_dir_path))
    m.hooks.load(m)
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
    m.hooks.clone(m, temp_path)
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
