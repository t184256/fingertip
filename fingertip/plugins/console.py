import sys

import fingertip


@fingertip.transient
def main(m, unseal=True):
    m = m.apply('unseal') if unseal else m

    assert hasattr(m, '_backend_mode')
    if hasattr(m, 'qemu'):
        m.log.warning('^A x (could be ^A^A x in screen) or power off to exit. '
                      'Fake prompt:')
        assert not m._up_counter

        m.log.plain()

        sys.stderr.flush()
        sys.stderr.write(m.prompt)
        sys.stderr.flush()
    else:
        m.log.plain()

    m._backend_mode = 'direct'
    with m:
        sys.exit(0)  # will happen only the underlying process exits
