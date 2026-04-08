"""Unit tests for WaveformCache and _ExtractWorker.

Strategy:
  - hash_file() and subprocess calls are mocked so tests run without ffmpeg or
    real audio files.
  - _ExtractWorker.run() is tested by injecting fake subprocess output.
  - WaveformCache cache-hit / cache-miss paths are tested with a tmp_path fixture.
"""

from __future__ import annotations

import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from video_annotator.controllers.waveform_cache import (
    SAMPLE_RATE,
    WaveformCache,
    _ExtractWorker,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _float_bytes(values: list[float]) -> bytes:
    """Pack a list of floats into raw f32le bytes."""
    return struct.pack(f"{len(values)}f", *values)


def _make_worker(tmp_path: Path, values: list[float] | None = None) -> _ExtractWorker:
    """Return a worker pointing at a dummy path with a tmp cache file."""
    cache_path = tmp_path / "test_waveform.npy"
    worker = _ExtractWorker("vid-1", "/fake/video.mp4", cache_path)
    return worker


# ---------------------------------------------------------------------------
# _ExtractWorker unit tests (run() called synchronously in the test thread)
# ---------------------------------------------------------------------------

class TestExtractWorker:
    def _run_with_mock(
        self,
        tmp_path: Path,
        stdout_bytes: bytes = b"",
        has_audio: bool = True,
        ffmpeg_returncode: int = 0,
        probe_returncode: int = 0,
    ) -> _ExtractWorker:
        """Run the worker synchronously with mocked subprocesses."""
        cache_path = tmp_path / "out.npy"
        worker = _ExtractWorker("vid-1", "/fake/video.mp4", cache_path)

        # Mock ffprobe (audio check)
        probe_mock = MagicMock()
        probe_mock.returncode = probe_returncode
        probe_mock.stdout = "audio" if has_audio else ""

        # Mock ffmpeg Popen
        proc_mock = MagicMock()
        proc_mock.poll.return_value = None
        proc_mock.returncode = ffmpeg_returncode
        proc_mock.stdout.read.side_effect = [stdout_bytes, b""]  # one chunk then EOF
        proc_mock.communicate.return_value = (b"", b"some stderr")

        with (
            patch("subprocess.run", return_value=probe_mock),
            patch("subprocess.Popen", return_value=proc_mock),
        ):
            worker.run()

        return worker

    def test_ready_emitted_with_normalized_array(self, tmp_path, qtbot):
        raw = _float_bytes([0.5, -1.0, 0.5, 1.0])
        received: list = []

        worker = _ExtractWorker("vid-1", "/fake/video.mp4", tmp_path / "w.npy")
        worker.ready.connect(lambda vid, arr: received.append((vid, arr)))

        probe_mock = MagicMock()
        probe_mock.returncode = 0
        probe_mock.stdout = "audio"

        proc_mock = MagicMock()
        proc_mock.poll.return_value = None
        proc_mock.returncode = 0
        proc_mock.stdout.read.side_effect = [raw, b""]
        proc_mock.communicate.return_value = (b"", b"")

        with (
            patch("subprocess.run", return_value=probe_mock),
            patch("subprocess.Popen", return_value=proc_mock),
        ):
            worker.run()

        assert len(received) == 1
        vid, arr = received[0]
        assert vid == "vid-1"
        assert isinstance(arr, np.ndarray)
        assert arr.dtype == np.float32
        assert float(np.max(np.abs(arr))) == pytest.approx(1.0)

    def test_cache_file_written(self, tmp_path, qtbot):
        raw = _float_bytes([0.2, 0.4])
        cache_path = tmp_path / "w.npy"
        worker = _ExtractWorker("vid-1", "/fake/video.mp4", cache_path)

        probe_mock = MagicMock()
        probe_mock.returncode = 0
        probe_mock.stdout = "audio"

        proc_mock = MagicMock()
        proc_mock.poll.return_value = None
        proc_mock.returncode = 0
        proc_mock.stdout.read.side_effect = [raw, b""]
        proc_mock.communicate.return_value = (b"", b"")

        with (
            patch("subprocess.run", return_value=probe_mock),
            patch("subprocess.Popen", return_value=proc_mock),
        ):
            worker.run()

        assert cache_path.exists()
        loaded = np.load(str(cache_path))
        assert loaded.dtype == np.float32
        assert len(loaded) == 2

    def test_no_audio_emitted_when_no_audio_stream(self, tmp_path, qtbot):
        received: list[str] = []
        cache_path = tmp_path / "w.npy"
        worker = _ExtractWorker("vid-1", "/fake/video.mp4", cache_path)
        worker.no_audio.connect(lambda vid: received.append(vid))

        probe_mock = MagicMock()
        probe_mock.returncode = 0
        probe_mock.stdout = ""  # no "audio" in output

        with patch("subprocess.run", return_value=probe_mock):
            worker.run()

        assert received == ["vid-1"]
        assert not cache_path.exists()

    def test_failed_emitted_on_ffmpeg_nonzero_exit(self, tmp_path, qtbot):
        received: list = []
        cache_path = tmp_path / "w.npy"
        worker = _ExtractWorker("vid-1", "/fake/video.mp4", cache_path)
        worker.failed.connect(lambda vid, reason: received.append((vid, reason)))

        probe_mock = MagicMock()
        probe_mock.returncode = 0
        probe_mock.stdout = "audio"

        proc_mock = MagicMock()
        proc_mock.poll.return_value = None
        proc_mock.returncode = 1
        proc_mock.stdout.read.side_effect = [b"", b""]
        proc_mock.communicate.return_value = (b"", b"conversion failed")

        with (
            patch("subprocess.run", return_value=probe_mock),
            patch("subprocess.Popen", return_value=proc_mock),
        ):
            worker.run()

        assert len(received) == 1
        assert received[0][0] == "vid-1"
        assert "exit 1" in received[0][1]

    def test_failed_emitted_when_ffmpeg_not_found(self, tmp_path, qtbot):
        received: list = []
        cache_path = tmp_path / "w.npy"
        worker = _ExtractWorker("vid-1", "/fake/video.mp4", cache_path)
        worker.failed.connect(lambda vid, reason: received.append((vid, reason)))

        probe_mock = MagicMock()
        probe_mock.returncode = 0
        probe_mock.stdout = "audio"

        with (
            patch("subprocess.run", return_value=probe_mock),
            patch("subprocess.Popen", side_effect=FileNotFoundError),
        ):
            worker.run()

        assert received and "ffmpeg not found" in received[0][1]

    def test_all_zeros_array_not_normalized(self, tmp_path, qtbot):
        """An all-silent track should not be divided by zero."""
        raw = _float_bytes([0.0, 0.0, 0.0])
        received: list = []
        cache_path = tmp_path / "w.npy"
        worker = _ExtractWorker("vid-1", "/fake/video.mp4", cache_path)
        worker.ready.connect(lambda vid, arr: received.append(arr))

        probe_mock = MagicMock()
        probe_mock.returncode = 0
        probe_mock.stdout = "audio"

        proc_mock = MagicMock()
        proc_mock.poll.return_value = None
        proc_mock.returncode = 0
        proc_mock.stdout.read.side_effect = [raw, b""]
        proc_mock.communicate.return_value = (b"", b"")

        with (
            patch("subprocess.run", return_value=probe_mock),
            patch("subprocess.Popen", return_value=proc_mock),
        ):
            worker.run()

        assert len(received) == 1
        assert np.all(received[0] == 0.0)


# ---------------------------------------------------------------------------
# WaveformCache integration tests
# ---------------------------------------------------------------------------

class TestWaveformCache:
    def test_cache_hit_emits_ready_synchronously(self, tmp_path, qtbot):
        """If a .npy file already exists, ready is emitted without spawning a worker."""
        cache = WaveformCache()
        cache.set_source_folder(str(tmp_path))

        # Pre-populate cache file.
        key = "deadbeef01234567"
        cache_path = tmp_path / ".cache" / f"{key}_waveform.npy"
        arr = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        np.save(str(cache_path), arr)

        received: list = []
        cache.ready.connect(lambda vid, a: received.append((vid, a)))

        with patch("video_annotator.controllers.waveform_cache.hash_file", return_value=key):
            cache.request("vid-x", "/fake/video.mp4")

        assert len(received) == 1
        assert received[0][0] == "vid-x"
        np.testing.assert_array_almost_equal(received[0][1], arr)
        assert cache._active_worker is None   # no worker created

    def test_cache_miss_starts_worker(self, tmp_path, qtbot):
        """A cache miss should create an active worker."""
        cache = WaveformCache()
        cache.set_source_folder(str(tmp_path))

        with patch("video_annotator.controllers.waveform_cache.hash_file", return_value="aabbccdd11223344"):
            # Don't actually start the thread — just check the worker is created.
            with patch.object(_ExtractWorker, "start"):
                cache.request("vid-y", "/fake/video.mp4")

        assert cache._active_worker is not None
        cache.cancel_current()

    def test_second_request_cancels_first_worker(self, tmp_path, qtbot):
        """Requesting a second video cancels the prior worker."""
        cache = WaveformCache()
        cache.set_source_folder(str(tmp_path))

        with patch("video_annotator.controllers.waveform_cache.hash_file", return_value="0011223344556677"):
            with patch.object(_ExtractWorker, "start"):
                cache.request("vid-1", "/fake/a.mp4")
                first_worker = cache._active_worker

            with patch("video_annotator.controllers.waveform_cache.hash_file", return_value="aabbccddeeff0011"):
                with patch.object(_ExtractWorker, "start"):
                    with patch.object(_ExtractWorker, "isRunning", return_value=True):
                        with patch.object(_ExtractWorker, "cancel") as mock_cancel:
                            with patch.object(_ExtractWorker, "wait", return_value=True):
                                cache.request("vid-2", "/fake/b.mp4")
                            mock_cancel.assert_called_once()

        cache.cancel_current()

    def test_corrupt_cache_file_triggers_re_extraction(self, tmp_path, qtbot):
        """A corrupt .npy file is deleted and a worker is started instead."""
        cache = WaveformCache()
        cache.set_source_folder(str(tmp_path))

        key = "cafebabe12345678"
        cache_path = tmp_path / ".cache" / f"{key}_waveform.npy"
        cache_path.write_bytes(b"NOT A NUMPY FILE")

        with patch("video_annotator.controllers.waveform_cache.hash_file", return_value=key):
            with patch.object(_ExtractWorker, "start"):
                cache.request("vid-z", "/fake/video.mp4")

        assert not cache_path.exists()
        assert cache._active_worker is not None
        cache.cancel_current()
