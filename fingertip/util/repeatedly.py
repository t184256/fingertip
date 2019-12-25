"""
Helper functions for fingertip: repeatedly keep trying.
"""

import time


def keep_trying(func, exception_types, retries=12, timeout=1/32):
    while retries:
        try:
            return func()
        except exception_types:
            retries -= 1
            if not retries:
                raise
            time.sleep(timeout)
            timeout *= 2
