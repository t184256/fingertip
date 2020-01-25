# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import textwrap
import selectors


class ExecResult:
    def __init__(self, retcode, out=None, err=None):
        self.retcode = retcode
        self.out, self.err = out, err

    def __bool__(self):
        return not bool(self.retcode)

    def __iter__(self):
        yield from (self.retcode, self.out, self.err)


class CommandExecutionError(RuntimeError):
    def __init__(self, exec_result):
        super().__init__(f'Command returned {exec_result.retcode}')


def nice_exec(m, *args,
              shell=True, dedent=True, check=True, decode=True):
    m.log.info(f'{args}')
    if shell and dedent:
        args = (textwrap.dedent(args[0]),)
    if shell:
        assert len(args) == 1

    exec_result = m.exec(*args, shell=shell)

    if decode:
        exec_result.out = exec_result.out.decode()
        exec_result.err = exec_result.err.decode()

    if check and not exec_result:
        m.log.error('stdout')
        m.log.error(str(exec_result.out))
        m.log.critical('stderr')
        m.log.critical(str(exec_result.err))
        raise CommandExecutionError(exec_result)

    return exec_result
