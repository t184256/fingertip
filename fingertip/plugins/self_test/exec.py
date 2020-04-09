# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import fingertip


INDENTED_SCRIPT = '''
    echo '
    a
    '
'''


SCRIPT_WITH_HEREDOCS = r'''
 b=X
 cat <<EOF
  'a'
  $b
 EOF
 cat <<\EOF
  'a'
  $b
 EOF
'''


@fingertip.transient
def main(m):
    with m:
        assert m.exec('true')
        assert m('true')
        assert m('sh -c true')
        assert m('sh', '-c', 'true', shell=False)

        assert m(INDENTED_SCRIPT).out == '\na\n\n'
        assert m(INDENTED_SCRIPT, dedent=False).out == '\n    a\n    \n'

        assert m(SCRIPT_WITH_HEREDOCS).out == " 'a'\n X\n 'a'\n $b\n"

        assert m.exec('echo', 'x', 'y', 'z').out == b'x y z\n'
        assert m('echo x y z').out == 'x y z\n'
        assert m('echo x y z', decode=False).out == b'x y z\n'

        assert m.exec('echo', '""').out == b'""\n'
        assert m('echo', '""', shell=False).out == '""\n'
        assert m('echo ""').out == '\n'

        # TODO: these won't work for SSH and ash
        # assert m.exec('echo', "''").out == b"''\n"
        # assert m('echo', "''", shell=False).out == "''\n"
        # assert m("echo ''").out == '\n'

        t = m('true')
        assert t

        f = m('false', check=False)
        assert not f
        ret, out, err = f
        assert (ret, out, err) == (1, '', '')

        e = m('echo a; echo e >/dev/stderr; echo b; exit 3', check=False)
        assert e.retcode == 3
        assert e.out == 'a\nb\n'
        assert e.err == 'e\n'

        e = m('''
          yes out1 | head -n 5000
          yes err1 | head -n 5000 > /dev/stderr
          yes out2 | head -n 5000
          yes err2 | head -n 5000 > /dev/stderr
        ''')
        assert e.out == 'out1\n' * 5000 + 'out2\n' * 5000
        assert e.err == 'err1\n' * 5000 + 'err2\n' * 5000
