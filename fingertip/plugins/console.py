import sys


from fingertip.util import log


def main(m):
    assert hasattr(m, 'qemu')
    assert not m._up_counter

    m = m.apply('unseal')

    m.qemu._mode = 'direct'
    log.warn('^A x (could be ^A^A x in screen) or power off to exit.')
    log.warn("Here's a fake prompt to ease your mind:")
    sys.stdout.write(m.prompt)
    sys.stdout.flush()
    with m:
        sys.exit(0)
