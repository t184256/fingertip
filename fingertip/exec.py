# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import textwrap
import selectors

from fingertip.util import log


class ExecResult:
    def __init__(self, retcode, out=None, err=None, outerr=None):
        self.retcode = retcode
        self.out, self.err, self.outerr = out, err, outerr

    def __bool__(self):
        return not bool(self.retcode)

    def __iter__(self):
        yield from (self.retcode, self.out, self.err)


class CommandExecutionError(RuntimeError):
    def __init__(self, exec_result):
        super().__init__(f'Command returned {exec_result.retcode}')


def stream_out_and_err(outfile, errfile, stream_to=None):
    sel = selectors.DefaultSelector()
    sel.register(outfile, selectors.EVENT_READ)
    sel.register(errfile, selectors.EVENT_READ)
    out, err, outerr = b'', b'', b''
    while True:
        for key, _ in sel.select():
            c = key.fileobj.read1()
            if not c:
                return out, err, outerr
            if stream_to:
                stream_to.write(c)
                if b'\n' in c:
                    stream_to.flush()
            outerr += c
            if key.fileobj is outfile:
                out += c
            elif key.fileobj is errfile:
                err += c


def nice_exec(m, *args,
              shell=True, dedent=True, check=True, decode=True):
    log.info(f'{args}')
    if shell and dedent:
        args = (textwrap.dedent(args[0]),)
    if shell:
        assert len(args) == 1

    exec_result = m.exec(*args, shell=shell)

    if decode:
        exec_result.out = exec_result.out.decode()
        exec_result.err = exec_result.err.decode()
        exec_result.outerr = exec_result.outerr.decode()

    if check and not exec_result:
        log.error('stdout')
        log.error(str(exec_result.out))
        log.error('stderr')
        log.error(str(exec_result.err))
        raise CommandExecutionError(exec_result)

    return exec_result
