# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import importlib
import importlib.util
import os

from fingertip.util import log, weak_hash
from fingertip.util import path as util_path


def _resolve_user_plugin(name):
    """Resolve a plugin name to a file in the user plugins directory."""
    user_dir = util_path.USER_PLUGINS
    parts = name.split('.')
    path = os.path.join(user_dir, *parts) + '.py'
    if os.path.isfile(path):
        return path


def _load_user_plugin(name):
    """Load a user plugin module by name, or raise ModuleNotFoundError."""
    user_plugin_path = _resolve_user_plugin(name)
    if not user_plugin_path:
        raise ModuleNotFoundError(name)
    mod_name = 'fingertip.user_plugins.' + name
    spec = importlib.util.spec_from_file_location(mod_name, user_plugin_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_builtin_plugin(name):
    """Load a built-in fingertip plugin module by name."""
    return importlib.import_module('fingertip.plugins.' + name)


def _load_plugin(name):
    """Import a plugin, trying user plugins first, then built-in."""
    for load in _load_user_plugin, _load_builtin_plugin:
        try:
            module = load(name)
            return module.main
        except (ModuleNotFoundError, AttributeError):
            if '.' in name:
                modname, funcname = name.rsplit('.', 1)
                try:
                    module = load(modname)
                    return getattr(module, funcname)
                except (ModuleNotFoundError, AttributeError):
                    pass
    raise ModuleNotFoundError(name)


def func_and_autotag(smth, *args, **kwargs):
    return load_step(smth), autotag(smth, *args, **kwargs)


def load_step(smth):
    if isinstance(smth, str):
        smth = smth.replace('-', '_')  # python module name restrictions
        if smth.startswith('.') and '=' in smth:
            # this is for changing values on objects, e.g., ... + .ram.size=2G
            chain, value = smth.split('=', 1)
            chain = chain.split('.')[1:]
            return make_assigner(chain, value)
        elif smth.startswith('.'):  #
            # this is for calling methods on objects, e.g. ... + .hooks.smth
            return make_method_caller(smth.split('.')[1:])
        smth = _load_plugin(smth)
    return smth


def autotag(something, *args, **kwargs):
    log.info(f'autotag in: {something} {args} {kwargs}')
    if isinstance(something, str):
        name = something if not something.startswith('.') else '_' + something
    else:
        name = something.__module__ + '.' + something.__qualname__
        if name.endswith('.__main__'):
            name = name[:len('__main__')]
    args_str = ':'.join([f'{a}' for a in args] +
                        [f'{k}={v}' for k, v in sorted(kwargs.items())])
    if args_str and (' ' in args_str or '/' in args_str or len(args_str) > 20):
        args_str = '::' + weak_hash.of_string(args_str)
    tag = f'{name}:{args_str}' if args_str else name
    return tag


def make_method_caller(name_chain):
    def method_caller(m, *args, **kwargs):
        with m:
            x = m
            for name in name_chain:
                x = getattr(x, name)
            x(*args, **kwargs)
        return m
    return method_caller


def make_assigner(name_chain, value):
    def assigner(m):
        with m:
            x = m
            for name in name_chain[:-1]:
                x = getattr(x, name)
            setattr(x, name_chain[-1], value)
        return m
    return assigner
