"""PlayerPanel: video display widget.

Owns one AbstractPlayerBackend instance selected at construction time via
SettingsManager.player_backend:
  "qt_multimedia"   — in-app QVideoWidget (default, macOS + Linux)
  "mpv_subprocess"  — separate mpv window via Unix socket IPC (legacy)

The public API (signals + methods) is identical regardless of backend.
Nothing above PlayerPanel in the stack knows which backend is active.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QStackedLayout, QWidget

from video_annotator.ui.backends.abstract_backend import AbstractPlayerBackend

if TYPE_CHECKING:
    from video_annotator.settings import SettingsManager

SPEED_LEVELS = [1.0, 1.5, 2.0, 4.0, 8.0]


class PlayerPanel(QWidget):
    # ------------------------------------------------------------------ #
    # Signals — forwarded verbatim from the active backend               #
    # ------------------------------------------------------------------ #
    position_changed = Signal(float)   # current position in seconds
    duration_known   = Signal(float)   # total duration (once per file load)
    vfr_detected     = Signal(bool)    # True if source is variable frame rate
    playback_ended   = Signal()
    speed_changed    = Signal(float)
    paused_changed   = Signal(bool)    # True = paused, False = playing

    def __init__(
        self,
        settings: "SettingsManager | None" = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.setStyleSheet("background: #1a1a1a;")

        self._backend: AbstractPlayerBackend = self._create_backend(settings)
        self._connect_backend_signals()
        self._build_ui()

    # ------------------------------------------------------------------ #
    # Backend factory                                                     #
    # ------------------------------------------------------------------ #

    def _create_backend(
        self, settings: "SettingsManager | None"
    ) -> AbstractPlayerBackend:
        name = "qt_multimedia"
        if settings is not None:
            name = settings.player_backend

        if name == "mpv_subprocess":
            from video_annotator.ui.backends.mpv_subprocess_backend import MpvSubprocessBackend
            return MpvSubprocessBackend(self)

        from video_annotator.ui.backends.qt_multimedia_backend import QtMultimediaBackend
        return QtMultimediaBackend(self)

    def _connect_backend_signals(self) -> None:
        b = self._backend
        b.position_changed.connect(self.position_changed)
        b.duration_known.connect(self.duration_known)
        b.vfr_detected.connect(self.vfr_detected)
        b.speed_changed.connect(self.speed_changed)
        b.paused_changed.connect(self.paused_changed)
        b.playback_ended.connect(self.playback_ended)

    # ------------------------------------------------------------------ #
    # UI construction                                                     #
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        # QStackedLayout: placeholder label on index 0, video widget on index 1.
        self._stack = QStackedLayout(self)
        self._stack.setStackingMode(QStackedLayout.StackingMode.StackAll)

        self._placeholder = QLabel("Open a folder and select a video to begin.", self)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            "color: #555; font-size: 13px; background: transparent;"
        )
        self._stack.addWidget(self._placeholder)

        # If the backend provides an embeddable video widget, add it now.
        video_widget = getattr(self._backend, "video_widget", None)
        if video_widget is not None:
            self._stack.addWidget(video_widget)
            self._stack.setCurrentIndex(0)   # placeholder until first load

    # ------------------------------------------------------------------ #
    # Public API — delegates to backend                                  #
    # ------------------------------------------------------------------ #

    def load_video(self, abs_path: str) -> None:
        """Load the video at *abs_path* and start paused."""
        self._backend.load(abs_path)
        # Switch to video widget if embedded backend.
        video_widget = getattr(self._backend, "video_widget", None)
        if video_widget is not None and self._stack.count() > 1:
            self._stack.setCurrentIndex(1)

    def toggle_play_pause(self) -> None:
        self._backend.play_pause()

    def seek_relative(self, delta: float) -> None:
        self._backend.seek_relative(delta)

    def seek_absolute(self, position: float) -> None:
        self._backend.seek_absolute(position)

    def step_frame(self, forward: bool = True) -> None:
        # Not available in all backends; silently ignored if not implemented.
        if hasattr(self._backend, "step_frame"):
            self._backend.step_frame(forward)  # type: ignore[attr-defined]

    def set_speed(self, speed: float) -> None:
        assert speed in SPEED_LEVELS, f"Invalid speed: {speed}"
        self._backend.set_speed(speed)

    def set_volume(self, volume: int) -> None:
        self._backend.set_volume(volume)

    # ------------------------------------------------------------------ #
    # State accessors                                                     #
    # ------------------------------------------------------------------ #

    @property
    def current_position(self) -> float:
        return self._backend.current_position

    # ------------------------------------------------------------------ #
    # Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._backend.stop()
        super().closeEvent(event)
