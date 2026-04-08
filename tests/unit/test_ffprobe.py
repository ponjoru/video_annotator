"""Unit tests for ffprobe wrapper using mock subprocess output."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from video_annotator.utils.ffprobe import ProbeError, VideoInfo, probe


FIXTURE_CFR = {
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "r_frame_rate": "30/1",
            "avg_frame_rate": "30/1",
            "duration": "60.0",
        },
        {
            "codec_type": "audio",
            "codec_name": "aac",
        },
    ],
    "format": {"duration": "60.0"},
}

FIXTURE_VFR = {
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1280,
            "height": 720,
            "r_frame_rate": "60/1",
            "avg_frame_rate": "30000/1001",
            "duration": "120.0",
        },
    ],
    "format": {"duration": "120.0"},
}


def _mock_run(stdout_data: dict, returncode: int = 0):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = json.dumps(stdout_data)
    result.stderr = ""
    return result


class TestProbe:
    @patch("subprocess.run")
    def test_cfr_video(self, mock_run):
        mock_run.return_value = _mock_run(FIXTURE_CFR)
        info = probe("/fake/video.mp4")
        assert isinstance(info, VideoInfo)
        assert info.duration == pytest.approx(60.0)
        assert info.has_audio is True
        assert info.is_vfr is False
        assert info.codec_name == "h264"

    @patch("subprocess.run")
    def test_vfr_detected(self, mock_run):
        mock_run.return_value = _mock_run(FIXTURE_VFR)
        info = probe("/fake/video.mp4")
        assert info.is_vfr is True
        assert info.has_audio is False

    @patch("subprocess.run")
    def test_raises_on_nonzero_exit(self, mock_run):
        mock_run.return_value = _mock_run({}, returncode=1)
        with pytest.raises(ProbeError):
            probe("/fake/video.mp4")

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_raises_when_ffprobe_not_found(self, mock_run):
        with pytest.raises(ProbeError, match="ffprobe not found"):
            probe("/fake/video.mp4")

    @patch("subprocess.run")
    def test_raises_on_invalid_json(self, mock_run):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "NOT JSON"
        mock_run.return_value = result
        with pytest.raises(ProbeError):
            probe("/fake/video.mp4")
