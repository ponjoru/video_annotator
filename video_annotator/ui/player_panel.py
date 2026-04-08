"""PlayerPanel: drives mpv as a subprocess via Unix socket IPC.

Architecture decision (Phase 1 spike result):
  python-mpv (in-process) causes SIGBUS on macOS with Qt6/Metal because mpv cannot
  attach its own CAMetalLayer to a Qt-managed NSView. Running mpv as a child process
  avoids all shared-address-space conflicts.

IPC protocol:
  mpv --input-ipc-server=<path>  listens on a Unix domain socket.
  Each command is a single JSON line: {"command": [...]}
  Each response is a single JSON line: {"data": ..., "error": "success"|<msg>, "request_id": ...}
  A new connection is opened per command (same pattern as mpv's own example clients).
  Socket timeout of 0.5 s prevents blocking the main thread if mpv is busy.

Position is polled at 10 Hz by a QTimer; each poll is one IPC round-trip (~0.1 ms on macOS).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

SPEED_LEVELS = [1.0, 1.5, 2.0, 4.0, 8.0]
POSITION_POLL_INTERVAL_MS = 100   # 10 Hz

# Prefer the Homebrew-installed binary; fall back to whatever is on PATH.
_MPV_BINARY: str = (
    "/opt/homebrew/bin/mpv"
    if os.path.isfile("/opt/homebrew/bin/mpv")
    else (shutil.which("mpv") or "mpv")
)


class PlayerPanel(QWidget):
    # --- Signals ---
    position_changed = Signal(float)      # current position in seconds
    duration_known = Signal(float)        # total duration in seconds (once per file load)
    vfr_detected = Signal(bool)           # True if source is variable frame rate
    playback_ended = Signal()             # mpv reached end-of-file
    speed_changed = Signal(float)         # new speed value
    paused_changed = Signal(bool)         # True = paused, False = playing

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.setStyleSheet("background: #1a1a1a;")

        self._proc: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._socket_path: str | None = None
        self._lock = threading.Lock()               # guards all IPC calls
        self._duration: float = 0.0
        self._current_speed: float = 1.0
        self._initialized: bool = False
        self._is_paused: bool = True               # mpv starts paused

        self._position_timer = QTimer(self)
        self._position_timer.setInterval(POSITION_POLL_INTERVAL_MS)
        self._position_timer.timeout.connect(self._poll_position)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._initialized:
            self._initialized = True
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            placeholder = QLabel("Open a folder and select a video to begin.", self)
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #555; font-size: 13px; background: transparent;")
            layout.addWidget(placeholder)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_mpv()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Playback control
    # ------------------------------------------------------------------

    def load_video(self, abs_path: str) -> None:
        """Launch mpv subprocess for abs_path; emits duration_known when ready."""
        self._stop_mpv()

        self._socket_path = os.path.join(
            tempfile.gettempdir(), f"va-mpv-{os.getpid()}.sock"
        )
        if os.path.exists(self._socket_path):
            os.remove(self._socket_path)

        self._proc = subprocess.Popen(
            [
                _MPV_BINARY,
                "--no-terminal",
                "--force-window=yes",
                "--pause=yes",
                "--keep-open=yes",
                "--hr-seek=yes",
                "--geometry=1280x720-0+0",  # top-right corner; temporary until embedded player
                f"--input-ipc-server={self._socket_path}",
                abs_path,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Block until the IPC socket appears (mpv is ready) — max 5 s.
        for _ in range(50):
            if os.path.exists(self._socket_path):
                break
            time.sleep(0.1)
        else:
            self._proc.kill()
            self._proc = None
            return

        self._duration = 0.0
        self._is_paused = True
        self._position_timer.start()

        # mpv needs a moment to probe the file before duration is available.
        QTimer.singleShot(400, self._fetch_duration_and_notify)

    def toggle_play_pause(self) -> None:
        self._ipc_cmd("cycle", "pause")

    def seek_relative(self, delta: float) -> None:
        self._ipc_cmd("seek", delta, "relative+exact")

    def seek_absolute(self, position: float) -> None:
        self._ipc_cmd("seek", position, "absolute+exact")

    def step_frame(self, forward: bool = True) -> None:
        self._ipc_cmd("frame-step" if forward else "frame-back-step")

    def set_speed(self, speed: float) -> None:
        assert speed in SPEED_LEVELS, f"Invalid speed: {speed}"
        self._current_speed = speed
        self._ipc_set("speed", speed)
        self.speed_changed.emit(speed)

    def set_volume(self, volume: int) -> None:
        self._ipc_set("volume", float(volume))

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    @property
    def current_position(self) -> float:
        val = self._ipc_get("time-pos")
        return float(val) if val is not None else 0.0

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def current_speed(self) -> float:
        return self._current_speed

    # ------------------------------------------------------------------
    # Internal — IPC
    # ------------------------------------------------------------------

    def _ipc(self, command: list) -> dict | None:
        """Send one JSON command and return the parsed response dict, or None on error."""
        if not self._socket_path or not os.path.exists(self._socket_path):
            return None
        with self._lock:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                sock.connect(self._socket_path)
                sock.sendall((json.dumps({"command": command}) + "\n").encode())
                data = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"\n" in chunk:
                        break
                sock.close()
                # mpv may send event lines before the response; find the one with "error".
                for line in data.decode(errors="replace").split("\n"):
                    line = line.strip()
                    if line:
                        obj = json.loads(line)
                        if "error" in obj:
                            return obj
            except Exception:
                pass
        return None

    def _ipc_get(self, prop: str) -> object:
        result = self._ipc(["get_property", prop])
        if result and result.get("error") == "success":
            return result.get("data")
        return None

    def _ipc_set(self, prop: str, value: object) -> None:
        self._ipc(["set_property", prop, value])

    def _ipc_cmd(self, *args: object) -> None:
        self._ipc(list(args))

    # ------------------------------------------------------------------
    # Internal — lifecycle helpers
    # ------------------------------------------------------------------

    def _fetch_duration_and_notify(self) -> None:
        dur = self._ipc_get("duration")
        if dur is not None:
            self._duration = float(dur)
            self.duration_known.emit(self._duration)

        # VFR: container fps vs average decoded fps differ by more than 0.5
        container_fps = self._ipc_get("container-fps")
        estimated_fps = self._ipc_get("estimated-vf-fps")
        is_vfr = False
        if container_fps is not None and estimated_fps is not None:
            is_vfr = abs(float(container_fps) - float(estimated_fps)) > 0.5
        self.vfr_detected.emit(is_vfr)

    def _poll_position(self) -> None:
        """Called at 10 Hz; emits position_changed, paused_changed, and detects end-of-file."""
        if self._proc is None:
            return
        # Check if process has exited (end of file with keep-open=no would quit mpv).
        if self._proc.poll() is not None:
            self._position_timer.stop()
            self.playback_ended.emit()
            return

        pos = self._ipc_get("time-pos")
        if pos is not None:
            self.position_changed.emit(float(pos))

        paused = self._ipc_get("pause")
        if isinstance(paused, bool) and paused != self._is_paused:
            self._is_paused = paused
            self.paused_changed.emit(paused)

    def _stop_mpv(self) -> None:
        self._position_timer.stop()
        if self._proc is not None:
            self._ipc_cmd("quit")
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
        if self._socket_path and os.path.exists(self._socket_path):
            try:
                os.remove(self._socket_path)
            except OSError:
                pass
        self._socket_path = None
