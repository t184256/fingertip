# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2020 Red Hat, Inc., see CONTRIBUTORS.

TIME = {'s': 1, 'm': 60, 'h': 3600, 'd': 24 * 3600, 'w': 24 * 3600 * 7}
BINARY = {'K': 2**10, 'M': 2**20, 'G': 2**30, 'T': 2**40, 'P': 2**50}


def parse_time_interval(interval):
    if isinstance(interval, str) and interval[-1] in TIME:
        return float(interval[:-1]) * TIME[interval[-1]]
    try:
        return float(interval)
    except ValueError:
        raise ValueError(f'Cannot parse time interval {interval}')


def parse_binary(value):
    if isinstance(value, str) and value[-1] in BINARY:
        return int(value[:-1]) * BINARY[value[-1]]
    try:
        return int(value)
    except ValueError:
        raise ValueError(f'Cannot parse binary-suffixed value {value}')


def binary(value):
    value = int(value)
    if not value:
        return '0'
    r = str(value)
    for suffix, suffix_value in BINARY.items():
        if value // suffix_value * suffix_value == value:
            r = f'{int(value / suffix_value)}{suffix}'
    return r
