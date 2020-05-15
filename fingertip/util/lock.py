"""
Helper functions for fingertep: inter-process locking.
"""

import collections
import threading

import fasteners


threadlocks = collections.defaultdict(threading.Lock)
threadlocks_lock = threading.Lock()


class Lock:
    def __init__(self, path):
        # InterProcessLock does not work across threads
        self._plock = fasteners.process_lock.InterProcessLock(path)
        with threadlocks_lock:
            self._tlock = threadlocks[path]

    def __enter__(self):
        self._plock.acquire()
        self._tlock.acquire()
        return self

    def __exit__(self, *_):
        self._tlock.release()
        self._plock.release()


# Python 3.7 has `contextlib.nullcontext`
class NoLock:
    def __enter__(self):
        pass

    def __exit__(self, *_):
        pass
