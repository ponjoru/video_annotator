"""QtMultimediaBackend: in-app video playback via PySide6 QtMultimedia.

Uses AVFoundation on macOS (Metal-native, no rendering conflicts) and
GStreamer on Linux. Zero extra binaries required beyond PySide6.

Linux note: H.264 playback requires GStreamer plugins:
    sudo apt install gstreamer1.0-plugins-base gstreamer1.0-libav

Known limitations vs MpvSubprocessBackend (acceptable for this use case):
  - Seeking lands on the nearest keyframe (±0–500 ms); not frame-exact.
  - VFR detection is done via ffprobe, not from the player itself.
  - frame-step is not supported (not needed for segment-cutting workflow).
"""

from __future__ import annotations

import subprocess
import shutil

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget

from video_annotator.ui.backends.abstract_backend import AbstractPlayerBackend

_FFPROBE_BINARY: str = shutil.which("ffprobe") or "ffprobe"


class QtMultimediaBackend(AbstractPlayerBackend):
    """In-process video backend built on QMediaPlayer + QVideoWidget."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)

        # QVideoWidget is the rendering surface embedded in PlayerPanel.
        self._video_widget = QVideoWidget()
        self._player.setVideoOutput(self._video_widget)

        self._cached_position: float = 0.0
        self._duration_emitted: bool = False

        # Wire Qt Multimedia signals → AbstractPlayerBackend signals.
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.playbackRateChanged.connect(self.speed_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.errorOccurred.connect(self._on_error)

    # ------------------------------------------------------------------ #
    # Video widget — PlayerPanel embeds this in its layout               #
    # ------------------------------------------------------------------ #

    @property
    def video_widget(self) -> QVideoWidget:
        return self._video_widget

    # ------------------------------------------------------------------ #
    # AbstractPlayerBackend interface                                     #
    # ------------------------------------------------------------------ #

    def load(self, abs_path: str) -> None:
        self._duration_emitted = False
        self._cached_position = 0.0
        self._player.setSource(QUrl.fromLocalFile(abs_path))
        # Start paused — consistent with MpvSubprocessBackend behaviour.
        self._player.pause()
        self._video_widget.show()
        # Probe VFR in a thread so we don't block the UI.
        self._probe_vfr_async(abs_path)

    def play_pause(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def seek_absolute(self, seconds: float) -> None:
        self._player.setPosition(int(seconds * 1000))

    def seek_relative(self, delta: float) -> None:
        new_pos = max(0, self._cached_position + delta)
        self._player.setPosition(int(new_pos * 1000))

    def set_speed(self, speed: float) -> None:
        self._player.setPlaybackRate(speed)

    def set_volume(self, volume: int) -> None:
        self._audio_output.setVolume(volume / 100.0)

    def stop(self) -> None:
        self._player.stop()
        self._player.setSource(QUrl())
        self._video_widget.hide()

    @property
    def current_position(self) -> float:
        return self._cached_position

    # ------------------------------------------------------------------ #
    # Internal — signal handlers                                         #
    # ------------------------------------------------------------------ #

    def _on_position_changed(self, ms: int) -> None:
        self._cached_position = ms / 1000.0
        self.position_changed.emit(self._cached_position)

    def _on_duration_changed(self, ms: int) -> None:
        if ms > 0 and not self._duration_emitted:
            self._duration_emitted = True
            self.duration_known.emit(ms / 1000.0)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        paused = state != QMediaPlayer.PlaybackState.PlayingState
        self.paused_changed.emit(paused)
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.playback_ended.emit()

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.playback_ended.emit()

    def _on_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        if error != QMediaPlayer.Error.NoError:
            # Surface the error as a position_changed=0 + duration so the UI
            # doesn't hang; the user will see a black frame.
            import warnings
            warnings.warn(f"QtMultimediaBackend error: {error_string}", RuntimeWarning, stacklevel=1)

    # ------------------------------------------------------------------ #
    # Internal — VFR detection via ffprobe                               #
    # ------------------------------------------------------------------ #

    def _probe_vfr_async(self, abs_path: str) -> None:
        """Run ffprobe in a QThread to detect VFR without blocking the UI."""
        from PySide6.QtCore import QThread, Signal, QObject

        class _ProbeWorker(QObject):
            done = Signal(bool)

            def __init__(self, path: str) -> None:
                super().__init__()
                self._path = path

            def run(self) -> None:
                is_vfr = False
                try:
                    import json as _json
                    result = subprocess.run(
                        [
                            _FFPROBE_BINARY,
                            "-v", "error",
                            "-select_streams", "v:0",
                            "-show_entries", "stream=r_frame_rate,avg_frame_rate",
                            "-of", "json",
                            self._path,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    data = _json.loads(result.stdout)
                    streams = data.get("streams", [])
                    if streams:
                        from fractions import Fraction

                        def _fps(s: str) -> float:
                            try:
                                f = Fraction(s)
                                return float(f) if f.denominator != 0 else 0.0
                            except Exception:
                                return 0.0

                        r_fps = _fps(streams[0].get("r_frame_rate", "0/1"))
                        avg_fps = _fps(streams[0].get("avg_frame_rate", "0/1"))
                        if r_fps > 0 and avg_fps > 0:
                            is_vfr = abs(r_fps - avg_fps) > 0.5
                except Exception:
                    pass
                self.done.emit(is_vfr)

        self._probe_thread = QThread(self)
        self._probe_worker = _ProbeWorker(abs_path)
        self._probe_worker.moveToThread(self._probe_thread)
        self._probe_worker.done.connect(self.vfr_detected)
        self._probe_worker.done.connect(self._probe_thread.quit)
        self._probe_thread.started.connect(self._probe_worker.run)
        self._probe_thread.start()
