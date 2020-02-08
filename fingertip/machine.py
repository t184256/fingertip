# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import functools
import os
import cloudpickle

import fingertip.exec
from fingertip import step_loader, expiration
from fingertip.util import hooks, lock, log, path, reflink, temp


def transient(func):
    func.transient = True

    @functools.wraps(func)
    def wrapper(*a, **kwa):
        r = func(*a, **kwa)
        assert r is None

    return wrapper


class Machine:
    def __init__(self, backend_name, sealed=True, expire_in='7d'):
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
        self.backend = backend_name
        self.log = log.sublogger(f'plugins.backend.{backend_name}',
                                 os.path.join(self.path, 'log.txt'))
        self.log.debug(f'created {backend_name}')
        self.hooks.clone.append(
            lambda to: reflink.auto(os.path.join(self.path, 'log.txt'),
                                    os.path.join(to, 'log.txt')))

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
        self.log.disable_hint()
        if link_as and self._state == 'spun_down':
            self.hooks.save.in_reverse()
            temp_path = self.path
            self.path = temp.unique_dir(self._parent_path, hint=name_hint)
            log.debug(f'saving to temp {temp_path}')
            self._state = 'saving'
            self.expiration.depend_on_loaded_python_modules()
            del self.log
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
                    log.critical(f'Refusing to overwrite fresh {link_as}')
                    raise RuntimeError(f'Not overriding fresh {link_as}')
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
            return self._cache_aware_apply(step, tag, func, args, kwargs)
        else:
            log.critical(f'apply to state={self._state}')
            raise RuntimeError(f'State machine error, apply to {self._state}')

    def _cache_aware_apply(self, step, tag, func, args, kwargs):
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
            prev_log = self.log
            if os.path.exists(new_mpath) and not needs_a_rebuild(new_mpath):
                # sweet, scratch this instance, fast-forward to cached result
                log.info(f'reusing {step} @ {new_mpath}')
                self._finalize()
                clone_from_path = new_mpath
            else:
                # loaded, not spun up, step not cached: perform step, cache
                log.info(f'applying (and, possibly, caching) {tag}')
                prev_log.disable_hint()
                self.log = log.sublogger('plugins.' + tag.split(':', 1)[0],
                                         os.path.join(self.path, 'log.txt'))
                m = func(self, *args, **kwargs)
                prev_log.enable_hint()
                if m:
                    assert not m._transient
                    m._finalize(link_as=new_mpath, name_hint=tag)
                    clone_from_path = new_mpath
                    log.info(f'successfully applied and saved {tag}')
                else:  # transient step
                    clone_from_path = self._parent_path
                    log.info(f'successfully applied and dropped {tag}')
        m = clone_and_load(clone_from_path, link_as=end_goal)
        m.log = prev_log
        return m


def _load_from_path(data_dir_path):
    log.debug(f'load from {data_dir_path}')
    with open(os.path.join(data_dir_path, 'machine.clpickle'), 'rb') as f:
        m = cloudpickle.load(f)
    assert m._state == 'saving'
    m._state = 'loading'
    m.log = log.sublogger('<unknown>')
    assert m.path == data_dir_path
    assert m._parent_path == os.path.realpath(os.path.dirname(data_dir_path))
    m.hooks.load()
    m._state = 'loaded'
    return m


def clone_and_load(from_path, link_as=None, name_hint=None):
    log.debug(f'clone {from_path} {link_as}')
    temp_path = temp.disappearing_dir(from_path, hint=name_hint)
    log.debug(f'temp = {temp_path}')
    os.makedirs(temp_path, exist_ok=True)
    with open(os.path.join(from_path, 'machine.clpickle'), 'rb') as f:
        m = cloudpickle.load(f)
    m.log = log.sublogger('<cloning>')
    m.hooks.clone(temp_path)
    del m.log
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
        if not os.path.exists(mpath) or needs_a_rebuild(mpath):
            log.info(f'building {tag}...')
            first = func(*args, **kwargs)
            if first is None:
                return
            first._finalize(link_as=mpath, name_hint=tag)
            log.info(f'succesfully built {tag}')
    m = clone_and_load(mpath)
    return m


OFFLINE = os.getenv('FINGERTIP_OFFLINE', '0') != '0'


def needs_a_rebuild(mpath, by=None):
    with open(os.path.join(mpath, 'machine.clpickle'), 'rb') as f:
        m = cloudpickle.load(f)
    if not m.expiration.files_have_not_changed():
        return True
    expired = m.expiration.is_expired(by)
    if expired:
        log.debug(f'{mpath} has expired at {m.expiration.pretty()}')
    else:
        log.debug(f'{mpath} is valid until {m.expiration.pretty()}')
    if OFFLINE and expired:
        log.warning(f'{mpath} expired at {m.expiration.pretty()}, '
                    'but offline mode is enabled, so, reusing it')
    return expired and not OFFLINE
