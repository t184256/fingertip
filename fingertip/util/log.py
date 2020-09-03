# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

"""
Helper functions for fingertip: logging.
"""

import atexit
import datetime
import logging
import os
import random
import re
import sys
import threading

from fingertip.util import path, reflink


_DISABLE_LINE_WRAP = '\x1b[?7l'
_ENABLE_LINE_WRAP = '\x1b[?7h'
_ERASE = '\x1b[K'
_REWIND = '\x1b[1000D'  # hope nobody's term is wider than 1000 cols
_RESET = '\033[0m'
DEBUG = os.getenv('FINGERTIP_DEBUG') == '1'

_STRIP = re.compile(br'\x07|'  # BEL
                    br'\x1b[c7-8]|'
                    br'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def strip_control_sequences(s):
    d = _STRIP.sub(b'', s).decode()
    r = repr(d)
    return r if r'\x' in r else d


class ErasingFormatter(logging.Formatter):
    _COLORS = {logging.DEBUG: '\033[0;34m',
               logging.WARNING: '\033[1;33m',
               logging.ERROR: '\033[1;31m',
               logging.CRITICAL: '\033[0;31m\033[7m'}

    def __init__(self, erasing=False, shorten_name=False):
        # Adds its own newlines, handler's .terminator is supposed to be ''
        self.erasing, self.shorten_name = erasing, shorten_name
        orig_excepthook = sys.excepthook

        def excepthook(*a):
            sys.stderr.write('\n')
            orig_excepthook(*a)
        sys.excepthook = excepthook

    def format(self, record):
        msg = record.msg.strip()
        name = record.name

        if self.shorten_name and name.startswith('fingertip.plugins.'):
            name = record.name[len('fingertip.plugins.'):]

        result = f'{name}: {msg}'

        if record.levelno in self._COLORS:
            result = self._COLORS[record.levelno] + result + _RESET

        if self.erasing:
            if record.levelno < logging.WARNING:
                pre = _ERASE + _DISABLE_LINE_WRAP
                post = _ENABLE_LINE_WRAP + _REWIND
            else:
                pre, post = _ERASE, '\n'
            return pre + result + post
        return result


class ErasingStreamHandler(logging.StreamHandler):
    def __init__(self, stream=None, erasing=True, shorten_name=True):
        super().__init__(stream)
        stream = stream or sys.stderr
        self.erasing = stream.isatty() and not DEBUG and erasing
        self.setFormatter(ErasingFormatter(erasing=self.erasing,
                                           shorten_name=shorten_name))
        if self.erasing:
            self.terminator = ''

    def stop_erasing(self):
        if self.erasing:
            sys.stderr.write(_REWIND + _ERASE)
            sys.stderr.flush()
            self.terminator = '\n'
            self.erasing = False


logger = logging.getLogger('fingertip')
critical, error, warning = logger.critical, logger.error, logger.warning
debug, info = logger.debug, logger.info
current_handler = None


def nicer():
    # global logger
    # logger = logging.getLogger('fingertip')
    global current_handler
    logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
    if current_handler:
        logger.removeHandler(current_handler)
    current_handler = ErasingStreamHandler(shorten_name=True)
    logger.addHandler(current_handler)


def plain():
    global current_handler
    logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
    if current_handler:
        if isinstance(current_handler, ErasingStreamHandler):
            current_handler.stop_erasing()
        logger.removeHandler(current_handler)
    current_handler = logging.StreamHandler()
    logger.addHandler(current_handler)


class LogPipeThread(threading.Thread):
    def __init__(self, logger, level=logging.INFO):
        threading.Thread.__init__(self, daemon=True)
        self.logger, self.level = logger, level
        self.pipe_read, self.pipe_write = os.pipe()
        self.opened_write = os.fdopen(self.pipe_write, 'wb')
        self.opened_read = os.fdopen(self.pipe_read, 'rb')
        self.opened_write.data = b''
        self.opened_write.wait = self.join
        self.start()

    def run(self):
        for line in iter(self.opened_read.readline, b''):
            self.opened_write.data += line
            if line:
                line = strip_control_sequences(line).rstrip('\r\n')
                self.logger.log(self.level, line)
        self.opened_read.close()


class LogPseudoFile():
    def __init__(self, logger, level=logging.INFO):
        self.logger, self.level = logger, level
        self._buffer = ''

    def write(self, d):
        self._buffer += d
        lines = self._buffer.split('\n')
        for line in lines[:-1]:
            if line:
                line = strip_control_sequences(line.encode()).rstrip('\r\n')
                self.logger.log(self.level, line)
        self._buffer = lines[-1]

    def flush(self):
        pass


class Sublogger:
    def __init__(self, name, to_file=None):
        # If I don't do this, it assumes that subloggers with the same name
        # are reusable, and logs stuff *somewhere*. Ugh.
        self.sub = logger.getChild(name + '.' + str(random.random()))
        self.sub.name = self.name = name
        self.path = to_file
        self.used = False

    def hint(self):
        if not os.path.exists(self.path):
            self.warning(f'{self.path} missing!')

        fname = f'{datetime.datetime.utcnow().isoformat()}.txt'
        t = path.logs(fname, makedirs=True)
        reflink.auto(self.path, t)
        home = os.path.expanduser('~')
        t = t if not t.startswith(home) else t.replace(home, '~')
        m = (f'For an intermediate log, check {t} or set FINGERTIP_DEBUG=1.'
             if not DEBUG else f'Logfile: {t}')
        sys.stderr.write(m + '\n')

    def initialize(self):
        if not self.used:
            self.used = True

            if self.path:
                self.sub.addHandler(logging.FileHandler(self.path))

                debug(f'logging to {self.path}, enabling hint')
                atexit.register(self.hint)

    def finalize(self):
        if self.used and self.path:
            debug(f'no longer logging to {self.path}, disabling hint')
            atexit.unregister(self.hint)

    def make_pipe(self, **kwargs):
        return LogPipeThread(self.sub, **kwargs).opened_write

    def pipe_powered(self, func, **what_to_supply):
        def exec_func(*a, **kwa):
            extra_args = {name: self.make_pipe(level=level)
                          for name, level in what_to_supply.items()}
            try:
                return func(*a, **kwa, **extra_args)
            finally:
                for pipe in extra_args.values():
                    pipe.close()
        return exec_func

    def make_pseudofile(self, **kwargs):
        return LogPseudoFile(self.sub, **kwargs)

    def pseudofile_powered(self, func, **what_to_supply):
        def exec_func(*a, **kwa):
            extra_args = {name: self.make_pseudofile(level=level)
                          for name, level in what_to_supply.items()}
            return func(*a, **kwa, **extra_args)
        return exec_func

    plain, nicer = staticmethod(plain), staticmethod(nicer)

    def critical(self, *args, **kwargs):
        self.initialize()
        return self.sub.critical(*args, **kwargs)

    def error(self, *args, **kwargs):
        self.initialize()
        return self.sub.error(*args, **kwargs)

    def warning(self, *args, **kwargs):
        self.initialize()
        return self.sub.warning(*args, **kwargs)

    def info(self, *args, **kwargs):
        self.initialize()
        return self.sub.info(*args, **kwargs)

    def debug(self, *args, **kwargs):
        self.initialize()
        return self.sub.debug(*args, **kwargs)
