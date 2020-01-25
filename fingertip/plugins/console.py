import sys

import fingertip


@fingertip.transient
def main(m):
    assert hasattr(m, 'qemu')
    assert not m._up_counter

    m = m.apply('unseal')

    m.log.warning('^A x (could be ^A^A x in screen) or power off to exit.')
    m.log.plain()
    sys.stderr.flush()
    sys.stderr.write(m.prompt)
    sys.stderr.flush()

    m.qemu._mode = 'direct'
    with m.transient():
        sys.exit(0)  # will happen only when qemu exits
