"""
Helper functions for fingertep: inter-process locking.
"""

import collections
import threading

import fasteners


threadlocks = collections.defaultdict(threading.Lock)
threadlocks_lock = threading.Lock()


class LockTimeout(RuntimeError):
    pass


class Lock:
    def __init__(self, path, timeout=None):
        self.timeout = timeout
        # InterProcessLock does not work across threads
        self._plock = fasteners.process_lock.InterProcessLock(path)
        with threadlocks_lock:
            self._tlock = threadlocks[path]
        self._plock_acquired, self._tlock_acquired = False, False

    def __enter__(self):
        if not self._plock.acquire(timeout=self.timeout):
            raise LockTimeout('timeout acquiring interprocess lock')
        self._plock_acquired = True
        if not self._tlock.acquire(timeout=(self.timeout or -1)):
            raise LockTimeout('timeout acquiring interthread lock')
        self._tlock_acquired = True
        return self

    def __exit__(self, *_):
        if self._tlock_acquired:
            self._tlock.release()
            self._tlock_acquired = False
        if self._plock_acquired:
            self._plock.release()
            self._plock_acquired = False


# Python 3.7 has `contextlib.nullcontext`
class NoLock:
    def __enter__(self):
        pass

    def __exit__(self, *_):
        pass
