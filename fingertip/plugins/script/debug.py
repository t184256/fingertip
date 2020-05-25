# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

import logging
import os
import re
import sys
import threading
import time

import colorama
import inotify_simple
import pexpect

import fingertip
import fingertip.util.log
from fingertip.plugins.backend.qemu import NotEnoughSpaceForSnapshotException


WATCHER_DEBOUNCE_TIMEOUT = .25  # seconds
CHECKPOINT_SPARSITY = 1  # seconds
TRAIL_EATING_TIMEOUT = .01  # seconds
MID_PHASE_TIMEOUT = .25  # seconds
DELAY_BEFORE_SEND = .01  # seconds


# File monitoring and waiting #

class RewindNeededException(Exception):
    pass


def _is_event_rerun_worthy(event):
    if event.name.startswith('.#'):  # hi, EMACS
        return False
    if event.name.startswith('.') and event.name.endswith('.swp'):  # hi, VIM
        return False
    if event.name.startswith('.') and event.name.endswith('.swx'):  # why, VIM?
        return False
    return True


class OneOffInotifyWatcher:
    def __init__(self, log):
        self._inotify = inotify_simple.INotify()
        self.rewind_needed = threading.Event()
        self._mask = inotify_simple.flags.MODIFY
        self._mask |= inotify_simple.flags.ATTRIB
        self._mask |= inotify_simple.flags.CLOSE_WRITE
        self._mask |= inotify_simple.flags.MOVED_TO
        self._mask |= inotify_simple.flags.CREATE
        self._mask |= inotify_simple.flags.DELETE_SELF

        def _event_loop():
            log.debug(f'inotify blocks')
            for event in self._inotify.read():
                log.debug(f'inotify {event}')
                if not _is_event_rerun_worthy(event):
                    continue
                log.debug(f'that was rerun-worthy')

            # exhausting the events queue / debouncing
            debounce_end_time = time.time() + WATCHER_DEBOUNCE_TIMEOUT
            while True:
                time_left = debounce_end_time - time.time()
                if time_left < 0:
                    break
                _ = self._inotify.read(timeout=time_left)
            # finally, set the flag and cease functioning
            self.rewind_needed.set()
        threading.Thread(target=_event_loop, daemon=True).start()

    def watch(self, path):
        self._inotify.add_watch(path, mask=self._mask)


# some data classes #

class Segment:
    def __init__(self, input, expected_patterns):
        self.input, self.expected_patterns = input, expected_patterns

    def __eq__(self, other):
        return ((self.input, self.expected_patterns) ==
                (other.input, other.expected_patterns))


class SegmentExecutionResult:
    def __init__(self, segment, brief_output, full_output,
                 encountered_pattern, duration, checkpoint_after):
        self.input = segment.input
        self.expected_patterns = segment.expected_patterns
        self.encountered_pattern = encountered_pattern
        self.brief_output, self.full_output = brief_output, full_output
        self.duration = duration
        self.checkpoint_after = checkpoint_after  # can be None

    def corresponds_to(self, segment):
        return ((self.input, self.expected_patterns) ==
                (segment.input, segment.expected_patterns))


# adding operation on segments to a Machine #

def make_m_segment_aware(m):
    m.results = []
    m.checkpoint_sparsity = CHECKPOINT_SPARSITY
    m.never_executed_anything = True
    m.in_fast_forward = False

    def checkpoint_positions():  # which results have checkpoints after them
        return [i for i, res in enumerate(m.results) if res.checkpoint_after]
    m.checkpoint_positions = checkpoint_positions

    def since_last_checkpoint(results):
        duration = 0
        for res in results:
            duration = 0 if res.checkpoint_after else duration + res.duration
        return duration

    def checkpoint_cleanup():
        if not any((res.checkpoint_after is not None for res in m.results)):
            m.log.error('Error: ran out of checkpoints to clean up!')
            return
        deleted_checkpoints = 0
        m.checkpoint_sparsity *= 2
        m.log.info(f'checkpoint cleanup, now with {m.checkpoint_sparsity} sec')
        for i in range(len(m.results)):
            if m.results[i].checkpoint_after:
                since_prev_checkpoint = (since_last_checkpoint(m.results[:i])
                                         + m.results[i].duration)
                if since_prev_checkpoint <= m.checkpoint_sparsity:
                    m.log.warning(f'deleting checkpoint after {i} ' +
                                  f'({int(since_prev_checkpoint * 1000)}ms) '
                                  'to free space')
                    deleted_checkpoints += 1
                    m.snapshot.remove(m.results[i].checkpoint_after)
                    m.results[i].checkpoint_after = None
                else:
                    m.log.info(f' keeping checkpoint after {i} '
                               f'({int(since_prev_checkpoint * 1000)}ms)')
        if not deleted_checkpoints:
            checkpoint_cleanup()

    def maybe_checkpoint_already():
        latest_checkpoint_age = since_last_checkpoint(m.results)
        if not m.results or latest_checkpoint_age > m.checkpoint_sparsity:
            checkpoint_name = f'after-{len(m.results)}'
            try:
                m.log.debug(f'checkpointing after {checkpoint_name}')
                m.snapshot.checkpoint(checkpoint_name)
            except NotEnoughSpaceForSnapshotException:
                checkpoint_cleanup()  # try to remove at least one
                m.snapshot.checkpoint(checkpoint_name)
            return checkpoint_name

    def execute_segment(segment, no_checkpoint=False):
        start_time = time.time()

        m.log.debug(f'sending {segment.input}')
        m.never_executed_anything = False
        pre = ''
        if segment.input is not None:
            m.console.sendline(segment.input)
            m.console.expect_exact(segment.input[:40])  # avoid line-wrapping
            ignored, pre = m.console.before, m.console.after
            if ignored:
                m.log.warning(f'(ignored: {repr(ignored)})')
        else:
            m.console.sendcontrol('d')
        m.log.debug(f'sent {segment.input}')

        while True:
            try:
                i = m.console.expect(segment.expected_patterns,
                                     timeout=MID_PHASE_TIMEOUT)
                break
            except pexpect.exceptions.TIMEOUT:
                m.consider_interrupt_and_rewind()

        m.log.debug('-'*80)
        m.log.debug(segment.expected_patterns[i])
        m.log.debug('-'*80)

        end_time = time.time()
        result = SegmentExecutionResult(
            segment=segment,
            brief_output=pre + m.console.before,
            full_output=pre + m.console.before + m.console.after,
            encountered_pattern=segment.expected_patterns[i],
            duration=(end_time - start_time),
            checkpoint_after=None  # set later, see below
        )
        m.results.append(result)
        if not no_checkpoint:
            result.checkpoint_after = maybe_checkpoint_already()
        m.consider_interrupt_and_rewind()
    m.execute_segment = execute_segment

    def eat_trailing():
        m.console.expect(pexpect.TIMEOUT, timeout=TRAIL_EATING_TIMEOUT)
        m.log.debug(f'ate trailing output: {repr(m.console.before)}')
    m.console.eat_trailing = eat_trailing

    def rewind_before_segment(i):
        if i == 0 and m.never_executed_anything:
            m.log.debug(f'clean VM, no rewind needed')
            return
        m.snapshot.freeze()
        for res in m.results[i:]:
            if res.checkpoint_after:
                m.log.debug(f'removing snapshot {res.checkpoint_after}...')
                m.snapshot.remove(res.checkpoint_after)
        m.log.debug(f'preparing for a rewind...')
        eat_trailing()
        m.console.buffer = ''
        m.log.info(f'rewinding before segment {i}')
        m.snapshot.revert(m.results[i-1].checkpoint_after
                          if i else m.snapshot.base_name)
        m.snapshot.unfreeze()
        m.results = m.results[:i]
    m.rewind_before_segment = rewind_before_segment

    def count_matching_segments(segments):
        for i, (segment, result) in enumerate(zip(segments, m.results)):
            if not result.corresponds_to(segment):
                return i
        return len(m.results)

    def reexecute(segments, watcher, reloader, terse):
        def change_affects_current_segment():
            # we're in the process of executing a segment, did it change?
            updated_segments = reloader()
            matching_segments_n = count_matching_segments(updated_segments)
            if matching_segments_n < len(m.results):
                m.log.info('rewind due to changes in previous segments')
                return True
            elif matching_segments_n > len(segments):
                m.log.info('rare overexecution-after-truncation, rewinding')
                return True
            elif len(segments) > matching_segments_n:  # we have a current one
                assert len(m.results) == matching_segments_n < len(segments)
                currently_executing_segment = updated_segments[len(m.results)]
                replacement_segment = segments[len(m.results)]
                if currently_executing_segment != replacement_segment:
                    m.log.debug(f'rewind affects current segment')
                    return True
            return False

        def consider_interrupt_and_rewind():
            if watcher.rewind_needed.is_set():
                if change_affects_current_segment():
                    m.log.info('interrupt!')
                    raise RewindNeededException()
                # TODO: rate-limit rechecks?
        m.consider_interrupt_and_rewind = consider_interrupt_and_rewind

        i = count_matching_segments(segments)  # first mismatching segment idx
        if i == len(segments):
            m.log.debug(f'no changes, not reexecuting anything, {i}/{i}')
            return  # None -> no changes
        # i is now pointing at the first mismatching/missing segment,
        # but it doesn't necessarily mean we have a checkpoint right before it
        while i > 0 and not m.results[i-1].checkpoint_after:
            i -= 1  # 0th segment is guaranteed to have one, the base one
            m.log.debug(f'slipping back past uncheckpointed segment {i}')
        # i is now pointing at the closest segment with a checkpoint
        m.rewind_before_segment(i)

        # pseudo fast-forward
        m.in_fast_forward = True
        sys.stderr.flush()
        sys.stdout.flush()
        m.log.debug('---')
        if os.getenv('FINGERTIP_DEBUG') != '1':
            os.system('clear')
        previous_output = ''.join([
            result.full_output
            for result in m.results[:i]
        ])
        # TODO: use strip_control_sequences?
        m.console.logfile_read.write(colorama.Style.DIM +
                                     previous_output +
                                     colorama.Style.RESET_ALL)
        m.in_fast_forward = False

        # execute the rest for real
        for j, segment in enumerate(segments[i:], i):
            if j == 0:
                m.console.logfile_read.write(m.repl_header)
            last = j == len(segments) - 1
            m.log.debug(f'Executing segment {j} for real:')
            m.execute_segment(segment, no_checkpoint=last)
            if j == 0:
                m.results[0].full_output = (m.repl_header +
                                            m.results[0].full_output)
        return True  # -> Completed till the end
    m.reexecute = reexecute


# Different REPLs support #

class REPLBash:
    @staticmethod
    def segment(code):
        lines = code.rstrip().split('\n')
        segments = [Segment(s, [r'\r\n\u200C\$ ', r'\r\n\u200C> '])
                    for s in lines]
        segments.append(Segment(None, [r'\r\n\u200Creturn code \d+\r+\n']))
        return segments

    @staticmethod
    def prepare(m, scriptpath, terse):
        with m:
            if m('command -v bash', check=False).retcode:
                m.apply('ansible', 'package', name='bash', state='installed')
            m('command -v bash')
            INVISIBLE_IN_PS = u'\\[\\]'  # trick taken from pexpect.replwrap
            if terse != 'most':
                bash_version = m('bash --version').out.split('\n')[0]
                m.repl_header = bash_version + '\n' + '\u200C$ '
            else:
                m.repl_header = bash_version + '\n' + '\u200C$ '
            m.console.sendline(f'PS1=""')
            m.console.sendline(f'PS1="{INVISIBLE_IN_PS}\u200C$ " '
                               f'PS2="{INVISIBLE_IN_PS}\u200C> " '
                               'bash --noprofile --norc; '
                               r'bash -c "echo -e \\\\u200Creturn code $?"')
            m.console.sendline(r'echo -e \\u200C""READY')
            m.console.expect(r'\u200C\$ echo -e \\\\u200C""READY\r+\n'  # x2?
                             r'\u200CREADY\r+\n')
        return m


class REPLPython:
    @staticmethod
    def segment(code):
        lines = code.rstrip().split('\n')
        if lines[-1][0].isspace():
            lines.append('')  # terminate an open '... '
        segments = [Segment(s, [r'\r\n\u200C>>> ', r'\r\n\u200C... '])
                    for s in lines]
        segments.append(Segment(None, [r'\r\n\u200Creturn code \d+\r+\n']))
        return segments

    @staticmethod
    def prepare(m, scriptpath, terse):
        with m:
            if m('command -v python3', check=False).retcode:
                m.apply('ansible', 'package',
                        name='python3', state='installed')
            m('command -v bash')
            m.console.sendline(f'PS1=""')
            m.console.sendline('python3; '
                               r'bash -c "echo -e \\\\u200Creturn code $?"')
            m.console.sendline('import sys')
            m.console.sendline('sys.ps1, sys.ps2 = "\u200C>>> ", "\u200C... "')
            m.console.sendline('del sys')
            m.console.sendline(r'print("\u200C" + "READY")')
            m.console.expect(r'\r+\n(Python.*?)\r\n')
            if terse != 'most':
                m.repl_header = f'{m.console.match.group(1)}\n\u200C>>> '
            else:
                m.repl_header = f'\u200C>>> '
            m.console.expect(r'\r+\n\u200CREADY\r+\n')
        return m

    @staticmethod
    def format(line, fast_forward):
        fmt = ''
        if line == 'Traceback (most recent call last):':
            fmt = colorama.Fore.RED
        elif re.match(r'  File ".*", line \d+, in ', line):
            fmt = colorama.Fore.RED
        elif re.match(r'\w*Error: ', line):
            fmt = colorama.Fore.RED
        elif re.search(r':\d+:.*Warning: ', line):
            fmt = colorama.Fore.YELLOW
        elif line.startswith('\u200C>>> ') or line.startswith('\u200C... '):
            fmt += colorama.Fore.BLUE
        elif line == '\u200Creturn code 0':
            fmt += colorama.Fore.GREEN
        elif re.match(r'\u200Creturn code \d+', line):
            fmt += colorama.Fore.MAGENTA
        if fast_forward:
            fmt = colorama.Style.DIM + fmt
        return fmt + line + (colorama.Style.RESET_ALL if fmt else '')

    @staticmethod
    def filter(line, terseness):
        if terseness and line in ('\u200C>>> ', '\u200C... '):
            return False
        if terseness in ('more', 'most'):
            if line.startswith('\u200C... '):
                return False
            if line == 'Traceback (most recent call last):':
                return False
        if terseness == 'most':
            if line.startswith('\u200C>>> ') or line.startswith('\u200C... '):
                return False
            if re.match(r'\u200Creturn code \d+', line):
                return False
        return True


repls = {
    'bash': REPLBash,
    'python': REPLPython,
}


@fingertip.transient
def main(m, scriptpath, language='bash', no_unseal=False,
         terse=False, no_color=False):
    if not no_unseal:
        m = m.apply('unseal')
    # m = m.apply('.hooks.disable_proxy')

    repl = repls[language]
    m = m.apply(repl.prepare, scriptpath, terse)

    def reloader():
        with open(scriptpath) as f:
            code = f.read()
        return repl.segment(code)

    fingertip.util.log.plain()

    if not no_color and hasattr(repl, 'format'):
        class Formatter(logging.Formatter):
            def format(self, record):
                return repl.format(record.msg, fast_forward=m.in_fast_forward)
        fingertip.util.log.current_handler.setFormatter(Formatter())
    if terse and hasattr(repl, 'filter'):
        class Filter(logging.Filter):
            def filter(self, record):
                return repl.filter(record.msg, terse)
        fingertip.util.log.current_handler.addFilter(Filter())

    with m:
        make_m_segment_aware(m)
        # disable input echoing
        m.console.logfile_read = m.console.logfile
        m.console.logfile = None
        # speed up
        m.console.delaybeforesend = DELAY_BEFORE_SEND

        while True:
            try:
                watcher = OneOffInotifyWatcher(m.log)
                watcher.watch(scriptpath)
                segments = reloader()
                any_changes = m.reexecute(segments, watcher, reloader, terse)
                if any_changes:
                    m.console.eat_trailing()
                    checkpoints_i = [str(i) for i in m.checkpoint_positions()]
                    m.log.info(f'done. checkpoints: {checkpoints_i}, '
                               f'sparsity={m.checkpoint_sparsity}s. '
                               'waiting for changes...')
                watcher.rewind_needed.wait()
            except RewindNeededException:
                continue
