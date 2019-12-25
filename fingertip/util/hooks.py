"""
Helper functions for fingertep: attacking and calling hooks.
"""

import collections


class HookSet(list):
    def __call__(self, *args, **kwargs):
        return [hook(*args, **kwargs) for hook in self]

    def in_reverse(self, *args, **kwargs):
        return [hook(*args, **kwargs) for hook in self[::-1]]


class _HookManager(collections.defaultdict):
    def __call__(self, **kwargs):
        for hook_type, hook in kwargs.items():
            self[hook_type].append(hook)

    def __getattr__(self, hook_type):
        return self[hook_type]


def HookManager():
    return _HookManager(HookSet)  # pickling shenanigans workaround


# hooks = HookManager()

# hooks(smth=func)
# hooks['smth'] += func
# hooks.smth += func

# hooks['smth']()
# hooks.smth()

# hooks['smth'].in_reverse()
# hooks.smth.in_reverse()
