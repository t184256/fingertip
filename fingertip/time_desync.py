# Licensed under GNU General Public License v3 or later, see COPYING.
# Copyright (c) 2020 Red Hat, Inc., see CONTRIBUTORS.

from fingertip.util import log


class TimeDesync:
    """
    An abstraction to track the rough scale of time desync of the machines and:

    * invoke time synchronization (``.time_desync.fix``)
    * invoke time synchronization only if the scale of the current desync
      is too high (``.time_desync.fix_if_needed``)
    * request automatic time synchronization
      for the rest of the pipeline (``.time_desync.tighten``)

    ``NONE`` scale denotes as little-as-possible desynchronization;
    ``SMALL`` is for desyncs less then execution duration,
    caused by pauses or rewinds; and
    ``LARGE`` is for desyncs less than expiration time,
    caused by booting an old persisted snapshot.
    """
    NONE = 0
    SMALL = 1
    LARGE = 2

    @classmethod
    def _parse_scale(cls, scale):
        if isinstance(scale, str):
            return getattr(cls, scale.upper())
        return int(scale)

    def __init__(self, m):
        self._m = m
        self._current_scale = TimeDesync.NONE
        self._allowed_scale = TimeDesync.LARGE

    def report(self, scale):
        """
        Make note of a time desync of a specified scale
        """
        scale = self._parse_scale(scale)
        if scale > self._current_scale:
            log.debug(f'time desync scale increased to {scale}')
            self._current_scale = scale

    def tighten(self, allowed_scale):
        """
        Request that this and all the further stages of the pipeline
        must maintain desynchronization at or below the specified scale
        """
        allowed_scale = self._parse_scale(allowed_scale)
        if allowed_scale < self._allowed_scale:
            log.debug(f'time desync requirement tightened to {allowed_scale}')
            self._allowed_scale = allowed_scale
        self.fix_if_needed()

    def fix_if_needed(self, at_least_if_scale=None):
        """
        Perform one-off time synchronization
        if the desync is larger than the specified scale
        or the scale previously set with ``.desync.tighten``
        """
        scale = at_least_if_scale or TimeDesync.LARGE
        if min(scale, self._allowed_scale) < self._current_scale:
            log.debug('timesync '
                      f'(requested: {scale},'
                      f' allowed: {self._allowed_scale},'
                      f' current: {self._current_scale})')
            self.fix()

    def fix(self, force=False):
        """
        Perform one-off time synchronization
        :param bool force: Synchronize even if there's no detected desync
        """
        if self._current_scale != TimeDesync.NONE or force:
            assert self._m.hooks.timesync()
            self._current_scale = TimeDesync.NONE
