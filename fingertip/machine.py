# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import collections
import importlib
import os
import pickle
import time

from fingertip.util import log, temp, path


class Machine:
    def __init__(self, save_to=None, sealed=True, expire_in=7*24*3600):
        self._hooks = collections.defaultdict(list)
        os.makedirs(path.MACHINES, exist_ok=True)
        self.path = temp.disappearing_dir(path.MACHINES)
        self._save_to = save_to
        # States: loaded -> spun_up -> spun_down -> saved/dropped
        self._state = 'spun_down'
        self._up_counter = 0
        self.sealed = sealed
        self.expiration = Expiration(expire_in)

    def __enter__(self):
        log.debug(f'state={self._state}')
        assert (self._state == 'loaded' or
                self._state == 'spun_up' and self._up_counter)
        self._exec_hooks('up')
        self._state = 'spun_up'
        self._up_counter += 1
        return self

    def __exit__(self, exc_type, *_):
        assert self._state == 'spun_up'
        self._up_counter -= 1
        if not self._up_counter:
            self._exec_hooks('down')
            self._state = 'spun_down'
            if not exc_type and self._save_to:
                self.save()

    def hook(self, **kwargs):
        for hook_type, hook in kwargs.items():
            self._hooks[hook_type].append(hook)

    def _exec_hooks(self, hook_type, *args, in_reverse=False, **kwargs):
        log.debug(f'firing {hook_type} hooks')
        for hook in self._hooks[hook_type]:
            log.debug(f'hook {hook_type} {hook}')
            hook(self, *args, **kwargs)

    def save(self, to=None):
        log.debug(f'save to={to}')
        self._save_to = self._save_to or to
        if self._save_to:
            assert self._state == 'spun_down'
            self._exec_hooks('save', in_reverse=True)
            log.debug(f'saving to temp {self.path}')
            prev_path = self.path
            self.path = to
            self._state = 'saving'
            with open(os.path.join(prev_path, 'machine.pickle'), 'wb') as f:
                pickle.dump(self, f)
            log.debug(f'moving to {to}')
            os.rename(prev_path, self.path)
            self._state == 'saved'
            return to
        else:
            assert self._state in ('spun_down', 'loaded')
            log.warn(f'forget it, discarding {self.path}')
            temp.remove(self.path)
            self._state = 'dropped'

    def apply(self, step, *args, **kwargs):
        func = load_step(step, *args, **kwargs)
        log.debug(f'apply {self.path} {step} {func} {args} {kwargs}')
        if self._state == 'spun_up':
            log.debug(f'applying to unclean')
            return func(self, *args, **kwargs)
        elif self._state == 'loaded':
            log.debug(f'applying to clean')
            return self._cache_aware_apply(step, func, *args, **kwargs)
        else:
            log.abort(f'apply to state={self._state}')

    def _cache_aware_apply(self, step, func, *args, **kwargs):
        assert self._state == 'loaded'
        tag = autotag(step, *args, **kwargs)

        # Could there already be a cached result?
        log.debug(f'PATH {self.path} {tag}')
        new_mpath = os.path.join(os.path.dirname(self.path), tag)
        end_goal = self._save_to
        if os.path.exists(new_mpath):
            # Sweet, scratch this instance and use a cached result
            log.info(f'reusing {step}')
            self.save(to=None)
            return clone_and_load(new_mpath, save_to=end_goal)
        # Loaded instance not spun up, step not cached: perform step and cache
        log.info(f'building (and, possibly, caching) {step}')
        m = func(self, *args, **kwargs)
        if m:
            m.save(to=new_mpath)
            return clone_and_load(new_mpath, save_to=end_goal)
        else:  # transient step
            return clone_and_load(os.path.dirname(self.path))

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
    m._exec_hooks('load')
    m._state = 'loaded'
    return m


def clone_and_load(from_path, save_to=None):
    log.debug(f'clone {from_path} {save_to}')
    if from_path is None:  # TODO: remove later
        log.abort(f'from_path == None')
    temp_path = temp.disappearing_dir(from_path)
    log.debug(f'temp = {temp_path}')
    os.makedirs(temp_path, exist_ok=True)
    with open(os.path.join(from_path, 'machine.pickle'), 'rb') as f:
        m = pickle.load(f)
    m._exec_hooks('clone', m, temp_path)
    m._save_to, m.path = save_to, temp_path
    with open(os.path.join(m.path, 'machine.pickle'), 'wb') as f:
        pickle.dump(m, f)
    return _load_from_path(temp_path)


def load_step(smth, *args, **kwargs):
    if isinstance(smth, str):
        # try to import abc.xyz as (import fingertip.plugins.abc.xyz).main
        try:
            module = importlib.import_module('fingertip.plugins.' + smth)
            return module.main
        except (ModuleNotFoundError, AttributeError):
            # try to import abc.xyz as (import fingertip.plugins.abc).xyz
            modname, funcname = smth.rsplit('.', 1)
            module = importlib.import_module('fingertip.plugins.' + modname)
            smth = getattr(module, funcname)
    return smth


def autotag(something, *args, **kwargs):
    log.info(f'autotag in: {something} {args} {kwargs}')
    # take args into account later
    if isinstance(something, str):
        name = something
    else:
        name = something.__module__ + '.' + something.__qualname__
        assert name.startswith('fingertip.plugins.')
        name = name[len('fingertip.plugins.'):]
        if name.endswith('.__main__'):
            name = name[:len('__main__')]
    args_str = ':'.join([f'{a}' for a in args] +
                        [f'{k}={v}' for k, v in sorted(kwargs.items())])
    log.info(f'{name}:{args_str}' if args_str else name)
    return f'{name}:{args_str}' if args_str else name


def build(first_step, *args, **kwargs):
    first_tag = autotag(first_step, *args, **kwargs)

    # Could there already be a cached result?
    first_mpath = path.machines(first_tag)
    if not os.path.exists(first_mpath):
        first_func = load_step(first_step, *args, **kwargs)
        first = first_func(*args, **kwargs)
        if not first:
            return
        first.save(to=first_mpath)
    return clone_and_load(first_mpath)


class Expiration:
    def __init__(self, expire_in):
        self.time = time.time() + expire_in

    def limit(self, interval):
        self.time = min(self.time, time.time() + interval)
