"""MpvSubprocessBackend: drives mpv as a child process via Unix socket IPC.

This is the original PlayerPanel implementation extracted verbatim into the
backend abstraction. Behaviour is identical to the pre-UPD1 code.

mpv opens its own OS window (--force-window=yes). Position is polled at
10 Hz via a QTimer; each poll is one IPC round-trip over a Unix socket.
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

from PySide6.QtCore import QTimer

from video_annotator.ui.backends.abstract_backend import AbstractPlayerBackend

# Prefer the Homebrew-installed binary; fall back to whatever is on PATH.
_MPV_BINARY: str = (
    "/opt/homebrew/bin/mpv"
    if os.path.isfile("/opt/homebrew/bin/mpv")
    else (shutil.which("mpv") or "mpv")
)

_POSITION_POLL_INTERVAL_MS = 100   # 10 Hz


class MpvSubprocessBackend(AbstractPlayerBackend):
    """Launches mpv as a subprocess; communicates via Unix domain socket IPC."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._proc: subprocess.Popen | None = None  # type: ignore[type-arg]
        self._socket_path: str | None = None
        self._lock = threading.Lock()
        self._duration: float = 0.0
        self._current_speed: float = 1.0
        self._is_paused: bool = True

        self._position_timer = QTimer(self)
        self._position_timer.setInterval(_POSITION_POLL_INTERVAL_MS)
        self._position_timer.timeout.connect(self._poll_position)

    # ------------------------------------------------------------------ #
    # AbstractPlayerBackend interface                                     #
    # ------------------------------------------------------------------ #

    def load(self, abs_path: str) -> None:
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
                "--geometry=1280x720-0+0",  # top-right corner (legacy separate-window mode)
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

        QTimer.singleShot(400, self._fetch_duration_and_notify)

    def play_pause(self) -> None:
        self._ipc_cmd("cycle", "pause")

    def seek_absolute(self, seconds: float) -> None:
        self._ipc_cmd("seek", seconds, "absolute+exact")

    def seek_relative(self, delta: float) -> None:
        self._ipc_cmd("seek", delta, "relative+exact")

    def set_speed(self, speed: float) -> None:
        self._current_speed = speed
        self._ipc_set("speed", speed)
        self.speed_changed.emit(speed)

    def set_volume(self, volume: int) -> None:
        self._ipc_set("volume", float(volume))

    def stop(self) -> None:
        self._stop_mpv()

    @property
    def current_position(self) -> float:
        val = self._ipc_get("time-pos")
        return float(val) if val is not None else 0.0

    # ------------------------------------------------------------------ #
    # Internal — IPC                                                      #
    # ------------------------------------------------------------------ #

    def _ipc(self, command: list) -> dict | None:
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

    # ------------------------------------------------------------------ #
    # Internal — lifecycle                                                #
    # ------------------------------------------------------------------ #

    def _fetch_duration_and_notify(self) -> None:
        dur = self._ipc_get("duration")
        if dur is not None:
            self._duration = float(dur)
            self.duration_known.emit(self._duration)

        container_fps = self._ipc_get("container-fps")
        estimated_fps = self._ipc_get("estimated-vf-fps")
        is_vfr = False
        if container_fps is not None and estimated_fps is not None:
            is_vfr = abs(float(container_fps) - float(estimated_fps)) > 0.5
        self.vfr_detected.emit(is_vfr)

    def _poll_position(self) -> None:
        if self._proc is None:
            return
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
