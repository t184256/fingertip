# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2019 Red Hat, Inc., see CONTRIBUTORS.

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
from fingertip.util.log import strip_control_sequences


WATCHER_DEBOUNCE_TIMEOUT = .25  # seconds
CHECKPOINT_SPARSITY = 1  # seconds
TRAIL_EATING_TIMEOUT = .5  # seconds
MID_PHASE_TIMEOUT = .5  # seconds
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
        self._mask = inotify_simple.masks.ALL_EVENTS
        self._mask &= ~inotify_simple.flags.ACCESS

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

    def checkpoint_positions():  # which results have checkpoints after them
        return [i for i, res in enumerate(m.results) if res.checkpoint_after]
    m.checkpoint_positions = checkpoint_positions

    def since_last_checkpoint(results):
        duration = 0
        for res in results:
            duration = 0 if res.checkpoint_after else duration + res.duration
        return duration

    def checkpoint_cleanup():
        if not any ((res.checkpoint_after is not None for res in m.results)):
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
                               f'({int(since_prev_checkpoint * 1000)}ms) {m.checkpoint_sparsity}')
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
        if segment.input is not None:
            m.console.sendline(segment.input)
        else:
            m.console.sendcontrol('d')
            m.console.sendline('')
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
            brief_output=m.console.before,
            full_output=m.console.before + m.console.after,
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
    m.console.eat_trailing = eat_trailing

    def rewind_before_segment(i):
        if i == 0 and m.never_executed_anything:
            m.log.debug(f'clean VM, no rewind needed')
            return
        m.log.info(f'rewinding before segment {i}')
        for res in m.results[i:]:
            if res.checkpoint_after:
                m.snapshot.remove(res.checkpoint_after)
        m.snapshot.revert(m.results[i-1].checkpoint_after
                          if i else m.snapshot.base_name)
        m.results = m.results[:i]
    m.rewind_before_segment = rewind_before_segment

    def count_matching_segments(segments):
        for i, (segment, result) in enumerate(zip(segments, m.results)):
            if not result.corresponds_to(segment):
                return i
        return len(m.results)

    def reexecute(segments, watcher, reloader):
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
                old_segment = segments[len(m.results)]
                currently_executing_segment = updated_segments[len(m.results)]
                r = currently_executing_segment != old_segment
                m.log.debug(f'rewind affects current segment: {r}')
                return currently_executing_segment != old_segment
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
            return
        # i is now pointing at the first mismatching/missing segment,
        # but it doesn't necessarily mean we have a checkpoint right before it
        while i > 0 and not m.results[i-1].checkpoint_after:
            i -= 1  # 0th segment is guaranteed to have one, the base one
            m.log.debug(f'slipping back past uncheckpointed segment {i}')
        # i is now pointing at the closest segment with a checkpoint
        m.rewind_before_segment(i)
        # pseudo fast-forward
        os.system('clear')
        for j, result in enumerate(m.results[:i]):
            m.log.debug(f'Output of previously executed segment {j}:')
            sys.stderr.flush()
            # TODO: use strip_control_sequences?
            decolored = re.sub(r'\x1B[@-_][0-?]*[ -/]*[@-~]', '',
                               result.full_output)
            sys.stdout.write(colorama.Style.DIM +
                             decolored +
                             colorama.Style.RESET_ALL)
            sys.stdout.flush()
        # execute the rest for real
        for j, segment in enumerate(segments[i:], i):
            last = j == len(segments) - 1
            m.log.debug(f'Executing segment {j} for real:')
            m.execute_segment(segment, no_checkpoint=last)
        return True
    m.reexecute = reexecute


# File formats support #

class FormatBash:
    def __init__(self):
        pass

    @staticmethod
    def segment(code):
        segments = code.split('\n')
        segments = [Segment(s, ['ft\$ ', 'ft> ']) for s in segments]
        segments.append(Segment(None, [r'ft:return code \d+']))
        return segments

    @staticmethod
    def prepare(m, scriptpath):
        with m:
            if m('command -v bash', check=False).retcode:
                m.apply('ansible', 'package', name='bash', state='installed')
            m('command -v bash')
            #m.console.sendline('stty -echo')
            INVISIBLE = u'\\[\\]' # trick taken from pexpect.replwrap
            m.console.sendline(f'PS1=""')
            m.console.sendline(f'PS1="{INVISIBLE}ft$ " PS2="{INVISIBLE}ft> " '
                               'bash --noprofile --norc; '
                               'echo ft:return code $?')
            m.console.sendline('echo fingertip"": READY')
            m.console.expect_exact('ft$ echo fingertip"": READY')
            m.console.expect_exact('fingertip: READY')
        return m


@fingertip.transient
def main(m, scriptpath, no_unseal=False):
    if not no_unseal:
        m = m.apply('unseal')
    # m = m.apply('.hooks.disable_proxy')
    m = m.apply(FormatBash.prepare, scriptpath)

    def reloader():
        with open(scriptpath) as f:
            code = f.read()
        return FormatBash.segment(code)

    fingertip.util.log.plain()
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
                any_changes = m.reexecute(segments, watcher, reloader)
                if any_changes:
                    m.console.eat_trailing()
                    checkpoints_i = [str(i) for i in m.checkpoint_positions()]
                    m.log.info(f'done. checkpoints: {checkpoints_i}, '
                               f'sparsity={m.checkpoint_sparsity}s. '
                               'waiting for changes...')
                watcher.rewind_needed.wait()
            except RewindNeededException:
                continue
