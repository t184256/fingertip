"""
Helper functions for fingertep: weak hashing.
"""

import hashlib


def of_string(s):
    return hashlib.sha256(s.encode()).hexdigest()[:8]


def of_file(fname):
    h = hashlib.sha256()
    with open(fname, 'rb') as f:
        while True:
            d = f.read(65563)
            if not d:
                return h.hexdigest()[:8]
            h.update(d)
