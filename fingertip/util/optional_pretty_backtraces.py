# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.


try:
    import backtrace
    styles = backtrace.STYLES.copy()
    styles['call'] = styles['call'].replace('--> ', '')
    backtrace.hook(strip_path=True, on_tty=True, styles=styles)
except ImportError:
    pass
