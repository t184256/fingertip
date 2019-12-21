"""
Helper functions for fingertep: weak hashing.
"""

import hashlib


def weak_hash(s):
    return hashlib.sha224(s.encode()).hexdigest()[:8]


def of_file(s):
    with open(s) as f:
        return weak_hash(f.read())
