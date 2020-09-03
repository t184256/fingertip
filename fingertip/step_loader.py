# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import importlib

from fingertip.util import log, weak_hash


def func_and_autotag(smth, *args, **kwargs):
    return load_step(smth), autotag(smth, *args, **kwargs)


def load_step(smth):
    if isinstance(smth, str):
        if smth.startswith('.') and '=' in smth:
            # this is for changing values on objects, e.g., ... + .ram.size=2G
            chain, value = smth.split('=', 1)
            chain = chain.split('.')[1:]
            return make_assigner(chain, value)
        elif smth.startswith('.'):  #
            # this is for calling methods on objects, e.g. ... + .hooks.smth
            return make_method_caller(smth.split('.')[1:])
        try:
            # try to import abc.xyz as (import fingertip.plugins.abc.xyz).main
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
    if isinstance(something, str):
        name = something if not something.startswith('.') else '_' + something
    else:
        name = something.__module__ + '.' + something.__qualname__
        assert name.startswith('fingertip.plugins.')
        name = name[len('fingertip.plugins.'):]
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
