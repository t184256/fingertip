# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import collections
import importlib
import os
import pickle
import time

from fingertip.util import log, temp, path


class Machine:
    def __init__(self, sealed=True, expire_in=7*24*3600):
        self._hooks = collections.defaultdict(list)
        os.makedirs(path.MACHINES, exist_ok=True)
        self.path = temp.disappearing_dir(path.MACHINES)
        self._parent_path = path.MACHINES
        self._link_to = None
        # States: loaded -> spun_up -> spun_down -> saved/dropped
        self._state = 'spun_down'
        self._transient = False
        self._up_counter = 0
        self.sealed = sealed
        self.expiration = Expiration(expire_in)

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
            log.warn(f'forget it, discarding {self.path}')
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
        new_mpath = os.path.join(self._parent_path, tag)
        end_goal = self._link_to
        if os.path.exists(new_mpath):
            # sweet, scratch this instance and fast-forward to a cached result
            log.info(f'reusing {step} @ {new_mpath}')
            self._finalize()
            return clone_and_load(new_mpath, link_to=end_goal)
        # loaded instance not spun up, step not cached: perform step and cache
        log.info(f'building (and, possibly, caching) {tag}')
        m = func(self, *args, **kwargs)
        if m:  # normal step, rebase to its result
            m._finalize(link_to=new_mpath, name_hint=tag)
            return clone_and_load(new_mpath, link_to=end_goal)
        else:  # transient step
            return clone_and_load(self._parent_path, link_to=end_goal)

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
    assert m._parent_path == os.path.dirname(data_dir_path)
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
    m._parent_path = from_path
    m.path = temp_path
    m._link_to = link_to
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
        first._finalize(link_to=first_mpath, name_hint=first_tag)
    return clone_and_load(first_mpath)


class Expiration:
    def __init__(self, expire_in):
        self.time = time.time() + expire_in

    def limit(self, interval):
        self.time = min(self.time, time.time() + interval)
