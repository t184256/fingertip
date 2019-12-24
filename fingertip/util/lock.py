"""
Helper functions for fingertep: inter-process locking.
"""

import fasteners


class MaybeLock:
    def __init__(self, path, lock=True):
        self._lock = lock and fasteners.process_lock.InterProcessLock(path)

    def __enter__(self):
        if self._lock:
            self._lock.acquire()
        return self

    def __exit__(self, *_):
        if self._lock:
            self._lock.release()
