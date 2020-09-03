# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import datetime
import functools
import inspect
import os

import cloudpickle

import fingertip.exec
from fingertip import step_loader, expiration, time_desync
from fingertip.util import hooks, lock, log, path, reflink, temp


def transient(func=None, when='always'):
    if func is None:  # no parameters, just @transient
        return functools.partial(transient, when=when)

    func.transient = when  # can be a callable!
    return func


def supply_last_step_if_requested(func, fingertip_last_step):
    if 'fingertip_last_step' in inspect.signature(func).parameters:
        return functools.partial(func, fingertip_last_step=fingertip_last_step)
    return func


class Machine:
    def __init__(self, backend_name, sealed=True, expire_in='7d'):
        self.hooks = hooks.HookManager()
        os.makedirs(path.MACHINES, exist_ok=True)
        self.path = temp.disappearing_dir(path.MACHINES)
        self._parent_path = path.MACHINES
        # States: loaded -> spun_up -> spun_down -> saved/dropped
        self._state = 'spun_down'
        self._transient = False
        self._up_counter = 0
        self.sealed = sealed
        self.expiration = expiration.Expiration(expire_in)
        self.time_desync = time_desync.TimeDesync(self)
        self.backend = backend_name
        self.log = log.Sublogger(f'fingertip.plugins.backend.{backend_name}',
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
            if not self._transient or exc_type:
                # the machine needs to be spun down, will be finalized later
                self.hooks.down.in_reverse()
                self._state = 'spun_down'
            else:
                # the machine can be dropped and finalized, fast and dirty
                self.hooks.drop.in_reverse()
                self._state = 'dropped'
                self._finalize()

    def _finalize(self, link_as=None, name_hint=None):
        log.debug(f'finalize hint={name_hint} link_as={link_as} {self._state}')
        if link_as and self._state == 'spun_down':
            self.hooks.save.in_reverse()
            temp_path = self.path
            self.path = temp.unique_dir(self._parent_path, hint=name_hint)
            log.debug(f'saving to temp {temp_path}')
            self._state = 'saving'
            self.expiration.depend_on_loaded_python_modules()
            self.log.finalize()
            with open(os.path.join(temp_path, 'machine.clpickle'), 'wb') as f:
                cloudpickle.dump(self, f)
            log.debug(f'moving {temp_path} to {self.path}')
            os.rename(temp_path, self.path)
            self._state == 'saved'
            link_this = self.path
        else:
            self.log.finalize()
            assert self._state in ('spun_down', 'loaded', 'dropped')
            log.info(f'discarding {self.path}')
            # TODO: track whether the step will be transient-last and reflink?
            with open(os.path.join(self.path, 'log.txt')) as f:
                self.log_contents = f.read()
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

    def apply(self, step, *args, fingertip_last_step=False, **kwargs):
        func, tag = step_loader.func_and_autotag(step, *args, **kwargs)
        log.debug(f'apply {self.path} {step} {func} {args} {kwargs}')
        if self._state == 'spun_up':
            log.debug(f'applying to unclean')
            func = supply_last_step_if_requested(func, fingertip_last_step)
            return func(self, *args, **kwargs)
        elif self._state == 'loaded':
            log.debug(f'applying to clean')
            return self._cache_aware_apply(step, tag, func, args, kwargs,
                                           fingertip_last_step)
        else:
            log.critical(f'apply to state={self._state}')
            raise RuntimeError(f'State machine error, apply to {self._state}')

    def _cache_aware_apply(self, step, tag, func, args, kwargs, last_step):
        assert self._state == 'loaded'

        transient_hint = func.transient if hasattr(func, 'transient') else None
        if callable(transient_hint):
            transient_hint = supply_last_step_if_requested(transient_hint,
                                                           last_step)
            transient_hint = transient_hint(self, *args, **kwargs)

        return_as_transient = self._transient
        exec_as_transient = (
            transient_hint in ('always', True) or
            transient_hint == 'last' and last_step
        )
        log.debug(f'transient: {transient_hint}')
        log.debug(f'exec_as_transient: {exec_as_transient}')
        log.debug(f'return_as_transient: {return_as_transient}')
        self._transient = exec_as_transient

        # Could there already be a cached result?
        log.debug(f'PATH {self.path} {tag}')
        new_mpath = os.path.join(self._parent_path, tag)

        lock_path = os.path.join(self._parent_path, '.' + tag + '-lock')
        do_lock = not self._transient
        if do_lock:
            log.info(f'acquiring lock for {tag}...')
        prev_log_name = self.log.name
        self.log.finalize()
        with lock.Lock(lock_path) if do_lock else lock.NoLock():
            if (os.path.exists(new_mpath) and not needs_a_rebuild(new_mpath)
                    and not exec_as_transient):
                # sweet, scratch this instance, fast-forward to cached result
                log.info(f'reusing {step} @ {new_mpath}')
                self._finalize()
                clone_from_path = new_mpath
            else:
                # loaded, not spun up, step not cached: perform step, cache
                log.info(f'applying (and, possibly, caching) {tag}')
                self.log = log.Sublogger('fingertip.plugins.' +
                                         tag.split(':', 1)[0],
                                         os.path.join(self.path, 'log.txt'))
                func = supply_last_step_if_requested(func, last_step)
                m = func(self, *args, **kwargs)
                if m:
                    if m._transient and transient_hint == 'last' and last_step:
                        assert m._state == 'dropped'
                        # transient-when-last step returned m
                        # just in case it's not the last, but it was.
                        # m is dropped already, only log contents is preserved.
                        fname = f'{datetime.datetime.utcnow().isoformat()}.txt'
                        t = path.logs(fname, makedirs=True)
                        with open(t, 'w') as f:
                            f.write(m.log_contents)
                        return t
                    assert not m._transient, 'transient step returned a value'
                    m._finalize(link_as=new_mpath, name_hint=tag)
                    clone_from_path = new_mpath
                    log.info(f'successfully applied and saved {tag}')
                else:  # transient step, either had hints or just returned None
                    clone_from_path = self._parent_path
                    log.info(f'successfully applied and dropped {tag}')
        if last_step:
            return os.path.join(clone_from_path, 'log.txt')
        m = clone_and_load(clone_from_path)
        m.log = log.Sublogger(prev_log_name, os.path.join(m.path, 'log.txt'))
        m._transient = return_as_transient
        return m


def _load_from_path(data_dir_path):
    log.debug(f'load from {data_dir_path}')
    with open(os.path.join(data_dir_path, 'machine.clpickle'), 'rb') as f:
        m = cloudpickle.load(f)
    assert m._state == 'saving'
    m._state = 'loading'
    m.log = log.Sublogger('fingertip.<unknown>')
    assert m.path == data_dir_path
    assert m._parent_path == os.path.realpath(os.path.dirname(data_dir_path))
    m.hooks.load()
    m._state = 'loaded'
    return m


def clone_and_load(from_path, name_hint=None):
    log.debug(f'clone {from_path}')
    temp_path = temp.disappearing_dir(from_path, hint=name_hint)
    log.debug(f'temp = {temp_path}')
    os.makedirs(temp_path, exist_ok=True)
    with open(os.path.join(from_path, 'machine.clpickle'), 'rb') as f:
        m = cloudpickle.load(f)
    m.log = log.Sublogger('fingertip.<cloning>')
    m.hooks.clone(temp_path)
    m._parent_path = os.path.realpath(from_path)
    m.path = temp_path
    with open(os.path.join(m.path, 'machine.clpickle'), 'wb') as f:
        cloudpickle.dump(m, f)
    return _load_from_path(temp_path)


def build(first_step, *args, fingertip_last_step=False, **kwargs):
    func, tag = step_loader.func_and_autotag(first_step, *args, **kwargs)

    # Could there already be a cached result?
    mpath = path.machines(tag)
    lock_path = path.machines('.' + tag + '-lock')
    log.info(f'acquiring lock for {tag}...')

    transient_hint = func.transient if hasattr(func, 'transient') else None
    if callable(transient_hint):
        transient_hint = supply_last_step_if_requested(transient_hint,
                                                       fingertip_last_step)
        transient_hint = transient_hint(*args, **kwargs)
    transient = (
        transient_hint in ('always', True) or
        transient_hint == 'last' and fingertip_last_step
    )

    with lock.Lock(lock_path) if not transient else lock.NoLock():
        if not os.path.exists(mpath) or needs_a_rebuild(mpath):
            log.info(f'building {tag}...')
            func = supply_last_step_if_requested(func, fingertip_last_step)
            first = func(*args, **kwargs)

            if first is None:
                assert transient, 'first step returned None'
                return

            if transient:
                log.info(f'succesfully built and discarded {tag}')
                first._finalize()  # discard (not fast-dropped though)

                if transient_hint == 'last' and fingertip_last_step:
                    fname = f'{datetime.datetime.utcnow().isoformat()}.txt'
                    t = path.logs(fname, makedirs=True)
                    with open(t, 'w') as f:
                        f.write(first.log_contents)
                    return t
            else:
                log.info(f'succesfully built and saved {tag}')
                first._finalize(link_as=mpath, name_hint=tag)

    if fingertip_last_step:
        return os.path.join(mpath, 'log.txt')
    m = clone_and_load(mpath)
    m.log = log.Sublogger('fingertip.<just built>',
                          os.path.join(m.path, 'log.txt'))
    return m


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
    return expired
