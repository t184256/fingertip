# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import importlib

from fingertip.util import log, weak_hash


def func_and_autotag(smth, *args, **kwargs):
    return load_step(smth, *args, **kwargs), autotag(smth, *args, **kwargs)


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
    if args_str and (' ' in args_str or len(args_str) > 20):
        args_str = '::' + weak_hash.weak_hash(args_str)
    tag = f'{name}:{args_str}' if args_str else name
    return tag
