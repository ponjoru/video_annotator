"""WaveformCache: async audio extraction to numpy array, with disk caching.

Extraction: ffmpeg -v error -i <src> -vn -f f32le -ac 1 -ar 4000 pipe:1
Array format: float32, shape (N,), normalized to [-1.0, 1.0].
Cache key: sha1(abs_path + file_size + mtime)[:16].
Cache location: <source_folder>/.cache/<key>_waveform.npy

Thread safety:
  _ExtractWorker runs entirely in a QThread.
  All signals it emits are delivered to the main thread via Qt's queued connection.
  WaveformCache itself is only touched from the main thread.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from video_annotator.utils.hash_file import hash_file

SAMPLE_RATE = 4000          # Hz — matches TimelineWidget._render_waveform_pixmap
_CHUNK_SIZE = 65536         # bytes read per iteration from ffmpeg stdout
_FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


class _ExtractWorker(QThread):
    """Background thread: runs ffmpeg, emits result or error."""

    ready    = Signal(str, object)  # video_id, np.ndarray (f32)
    failed   = Signal(str, str)     # video_id, error message
    no_audio = Signal(str)          # video_id — source has no audio track

    def __init__(
        self,
        video_id: str,
        abs_path: str,
        cache_path: Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._video_id  = video_id
        self._abs_path  = abs_path
        self._cache_path = cache_path
        self._cancelled  = False
        self._proc: subprocess.Popen | None = None  # type: ignore[type-arg]

    def cancel(self) -> None:
        """Request cancellation; terminates ffmpeg subprocess if running."""
        self._cancelled = True
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def run(self) -> None:
        # ------------------------------------------------------------------
        # 1. Check for audio track via ffprobe (fast, no decode needed).
        # ------------------------------------------------------------------
        try:
            probe_result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "a:0",
                    "-show_entries", "stream=codec_type",
                    "-of", "csv=p=0",
                    self._abs_path,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            self.failed.emit(self._video_id, f"ffprobe not available: {exc}")
            return

        if self._cancelled:
            return

        has_audio = "audio" in probe_result.stdout.lower()
        if not has_audio:
            self.no_audio.emit(self._video_id)
            return

        # ------------------------------------------------------------------
        # 2. Extract audio as raw f32le PCM via ffmpeg pipe.
        # ------------------------------------------------------------------
        cmd = [
            _FFMPEG,
            "-v", "error",
            "-i", self._abs_path,
            "-vn",                   # no video
            "-f", "f32le",           # raw IEEE float
            "-ac", "1",              # mono
            "-ar", str(SAMPLE_RATE),
            "pipe:1",
        ]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            self.failed.emit(self._video_id, "ffmpeg not found on PATH")
            return

        # Read chunks; check cancellation between each.
        buf = bytearray()
        assert self._proc.stdout is not None
        while True:
            chunk = self._proc.stdout.read(_CHUNK_SIZE)
            if not chunk:
                break
            buf.extend(chunk)
            if self._cancelled:
                self._proc.terminate()
                self._proc.wait()
                return

        _, stderr_bytes = self._proc.communicate()
        if self._cancelled:
            return

        if self._proc.returncode != 0:
            msg = stderr_bytes.decode(errors="replace").strip()
            self.failed.emit(self._video_id, f"ffmpeg error (exit {self._proc.returncode}): {msg}")
            return

        if not buf:
            # File has an audio stream but produced no samples (e.g., 0-length clip).
            self.no_audio.emit(self._video_id)
            return

        # ------------------------------------------------------------------
        # 3. Convert bytes → float32 numpy array and normalize.
        # ------------------------------------------------------------------
        array = np.frombuffer(buf, dtype=np.float32).copy()
        peak = float(np.max(np.abs(array)))
        if peak > 0.0:
            array /= peak

        # ------------------------------------------------------------------
        # 4. Persist to .npy cache file.
        # ------------------------------------------------------------------
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            np.save(str(self._cache_path), array)
        except OSError:
            pass  # Cache write failure is non-fatal; we still emit the data.

        self.ready.emit(self._video_id, array)


class WaveformCache(QObject):
    """Public interface for waveform data.  Manages cache hits and worker lifecycle.

    All public methods must be called from the main (Qt GUI) thread.
    """

    ready              = Signal(str, object)  # video_id, np.ndarray
    no_audio           = Signal(str)          # video_id
    extraction_failed  = Signal(str, str)     # video_id, reason

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source_folder: str = ""
        self._active_worker: _ExtractWorker | None = None

    def set_source_folder(self, folder: str) -> None:
        self._source_folder = folder
        cache_dir = Path(folder) / ".cache"
        cache_dir.mkdir(exist_ok=True)

    def request(self, video_id: str, abs_path: str) -> None:
        """Request waveform data for a video.

        - Cache hit  → load .npy synchronously, emit ready().
        - Cache miss → cancel any running worker, start a new one.
        """
        # Cancel any prior extraction.
        self.cancel_current()

        # Build cache path.
        try:
            key = hash_file(abs_path)
        except OSError:
            # File not accessible; silently skip waveform.
            return

        cache_path = Path(self._source_folder) / ".cache" / f"{key}_waveform.npy"

        # Cache hit: load synchronously (fast; just mmap + emit).
        if cache_path.exists():
            try:
                array = np.load(str(cache_path))
                self.ready.emit(video_id, array)
                return
            except Exception:
                # Corrupt cache file — fall through to re-extract.
                try:
                    cache_path.unlink(missing_ok=True)
                except OSError:
                    pass

        # Cache miss: start background worker.
        worker = _ExtractWorker(video_id, abs_path, cache_path, self)
        worker.ready.connect(self._on_worker_ready)
        worker.failed.connect(self._on_worker_failed)
        worker.no_audio.connect(self._on_worker_no_audio)
        worker.finished.connect(self._on_worker_finished)
        self._active_worker = worker
        worker.start()

    def cancel_current(self) -> None:
        """Cancel any in-progress extraction (blocking wait up to 2 s)."""
        if self._active_worker and self._active_worker.isRunning():
            self._active_worker.cancel()
            self._active_worker.wait(2000)
        self._active_worker = None

    # ------------------------------------------------------------------
    # Worker signal handlers (called on the main thread via queued conn.)
    # ------------------------------------------------------------------

    def _on_worker_ready(self, video_id: str, array: np.ndarray) -> None:
        self.ready.emit(video_id, array)

    def _on_worker_failed(self, video_id: str, reason: str) -> None:
        self.extraction_failed.emit(video_id, reason)

    def _on_worker_no_audio(self, video_id: str) -> None:
        self.no_audio.emit(video_id)

    def _on_worker_finished(self) -> None:
        """Clean up the worker reference once the thread has exited."""
        self._active_worker = None
