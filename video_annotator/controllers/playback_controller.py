"""PlaybackController: bridges keyboard shortcuts and UI events to the mpv player.

Owns the pending segment state (in_point). When both in/out are set and valid,
delegates segment creation to ProjectManager.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from video_annotator.controllers.project_manager import ProjectManager
    from video_annotator.settings import SettingsManager
    from video_annotator.ui.player_panel import PlayerPanel


class PlaybackController(QObject):
    # --- Signals ---
    in_point_set = Signal(float)                   # position in seconds
    segment_confirmed = Signal(str, str)           # video_id, segment_id
    segment_cancelled = Signal()
    validation_failed = Signal(str)                # human-readable reason for toast
    confirm_long_segment_requested = Signal(float) # duration in seconds — awaits confirm/cancel

    def __init__(
        self,
        player: "PlayerPanel",
        manager: "ProjectManager",
        settings: "SettingsManager",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._player = player
        self._manager = manager
        self._settings = settings
        self._in_point: float | None = None
        self._current_video_id: str | None = None
        # Pending segment awaiting long-duration confirmation.
        self._pending_start: float | None = None
        self._pending_end: float | None = None

    @property
    def in_point(self) -> float | None:
        return self._in_point

    def set_current_video(self, video_id: str) -> None:
        """Called when a new video is loaded. Clears any pending in-point and pending segment."""
        self._current_video_id = video_id
        self._pending_start = self._pending_end = None
        self.cancel_segment()

    # ------------------------------------------------------------------
    # Segment marking
    # ------------------------------------------------------------------

    def set_in_point(self) -> None:
        """Mark the current playback position as segment start (I key)."""
        pos = self._player.current_position
        self._in_point = pos
        self.in_point_set.emit(pos)

    def set_out_point(self) -> None:
        """Mark current position as segment end and confirm (O key)."""
        if self._in_point is None:
            self.validation_failed.emit("Set In point first (I)")
            return
        if self._current_video_id is None:
            self.validation_failed.emit("No video loaded")
            return

        end = self._player.current_position
        duration = end - self._in_point

        if end <= self._in_point:
            self.validation_failed.emit("Out point must be after In point")
            return
        if duration < self._settings.min_segment_duration:
            self.validation_failed.emit(
                f"Segment too short (minimum {self._settings.min_segment_duration:.1f}s)"
            )
            return
        if duration > self._settings.max_segment_duration_warning:
            # Pause and ask for confirmation; store the pending range.
            self._pending_start = self._in_point
            self._pending_end = end
            self._in_point = None
            self.confirm_long_segment_requested.emit(duration)
            return

        seg = self._manager.add_segment(self._current_video_id, self._in_point, end)
        video_id = self._current_video_id
        self._in_point = None
        self.segment_confirmed.emit(video_id, seg.id)

    def cancel_segment(self) -> None:
        """Clear in-point state (Escape key)."""
        if self._in_point is not None:
            self._in_point = None
            self.segment_cancelled.emit()

    def confirm_pending_segment(self) -> None:
        """User confirmed an unusually long segment — create it now."""
        if self._pending_start is None or self._pending_end is None:
            return
        if self._current_video_id is None:
            self._pending_start = self._pending_end = None
            return
        seg = self._manager.add_segment(
            self._current_video_id, self._pending_start, self._pending_end
        )
        video_id = self._current_video_id
        self._pending_start = self._pending_end = None
        self.segment_confirmed.emit(video_id, seg.id)

    def cancel_pending_segment(self) -> None:
        """User rejected the long-segment confirmation — discard the pending range."""
        self._pending_start = self._pending_end = None
        self.segment_cancelled.emit()

    # ------------------------------------------------------------------
    # Playback control (delegates to PlayerPanel)
    # ------------------------------------------------------------------

    def play_pause(self) -> None:
        self._player.toggle_play_pause()

    def seek_relative(self, delta: float) -> None:
        self._player.seek_relative(delta)

    def seek_absolute(self, position: float) -> None:
        self._player.seek_absolute(position)

    def step_frame(self, forward: bool = True) -> None:
        self._player.step_frame(forward)

    def set_speed(self, speed: float) -> None:
        self._player.set_speed(speed)

    def set_segment_from_range(self, start: float, end: float) -> None:
        """Create a segment from an explicit time range (e.g., timeline rubber-band drag)."""
        if self._current_video_id is None:
            self.validation_failed.emit("No video loaded")
            return
        duration = end - start
        if end <= start:
            return
        if duration < self._settings.min_segment_duration:
            self.validation_failed.emit(
                f"Segment too short (minimum {self._settings.min_segment_duration:.1f}s)"
            )
            return
        if duration > self._settings.max_segment_duration_warning:
            self._pending_start = start
            self._pending_end = end
            self.confirm_long_segment_requested.emit(duration)
            return
        seg = self._manager.add_segment(self._current_video_id, start, end)
        self.segment_confirmed.emit(self._current_video_id, seg.id)
