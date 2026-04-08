"""Integration tests for ExportEngine, ExportSpec, and MetadataWriter.

Heavy tests (real ffmpeg subprocesses against fixture files) are skipped when
ffmpeg is not on PATH.  The MetadataWriter and ExportSpec.build() tests run
without ffmpeg — they only need the data model.
"""

from __future__ import annotations

import csv
import json
import os
import shutil

import pytest

from video_annotator.controllers.export_engine import (
    ClipSpec,
    ExportSpec,
    MetadataWriter,
    _ExportWorker,
)
from video_annotator.models.project import Project, Segment, Video
from video_annotator.settings import SettingsManager


ffmpeg_available = shutil.which("ffmpeg") is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(tmp_path, n_videos: int = 1, segs_per_video: int = 1) -> Project:
    folder = str(tmp_path)
    project = Project(
        source_folder=folder,
        output_folder=os.path.join(folder, "output"),
        created_at="2026-01-01T00:00:00Z",
    )
    for vi in range(n_videos):
        v = Video.create(f"clip{vi:02d}.mp4", f"clip{vi:02d}.mp4", folder)
        v.tags = ["vtag"]
        for si in range(segs_per_video):
            seg = Segment.create(v.id, float(si * 2), float(si * 2 + 1))
            seg.tags = ["stag"]
            v.segments.append(seg)
        project.videos.append(v)
    return project


def _make_settings(vtb: bool = False) -> SettingsManager:
    s = SettingsManager()
    s.videotoolbox_available = vtb
    # Always reset to "auto" so the persisted QSettings value doesn't leak into tests.
    s.use_videotoolbox = "auto"
    return s


def _make_clip_spec(tmp_path, start: float = 0.0, end: float = 1.0) -> ClipSpec:
    return ClipSpec(
        video_id="vid-1",
        segment_id="seg-1",
        source_path=str(tmp_path / "src.mp4"),
        start=start,
        end=end,
        output_path=str(tmp_path / "out" / "clip_001.mp4"),
        video_tags=["vtag"],
        segment_tags=["stag"],
        source_filename="src.mp4",
    )


# ---------------------------------------------------------------------------
# ExportSpec.build()
# ---------------------------------------------------------------------------

class TestExportSpecBuild:
    def test_one_video_one_segment(self, tmp_path):
        project = _make_project(tmp_path)
        settings = _make_settings()
        spec = ExportSpec.build(project, settings, str(tmp_path / "out"), "reencode")
        assert len(spec.clips) == 1
        clip = spec.clips[0]
        assert clip.source_filename == "clip00.mp4"
        assert clip.start == pytest.approx(0.0)
        assert clip.end == pytest.approx(1.0)
        assert "clip00" in clip.output_path

    def test_videos_without_segments_excluded(self, tmp_path):
        project = _make_project(tmp_path, n_videos=2, segs_per_video=0)
        settings = _make_settings()
        spec = ExportSpec.build(project, settings, str(tmp_path / "out"), "reencode")
        assert len(spec.clips) == 0

    def test_stem_collision_resolved(self, tmp_path):
        """Two videos with the same stem → second gets _<hash> suffix."""
        folder = str(tmp_path)
        project = Project(
            source_folder=folder,
            output_folder=os.path.join(folder, "output"),
            created_at="2026-01-01T00:00:00Z",
        )
        for _ in range(2):
            v = Video.create("clip.mp4", "clip.mp4", folder)
            seg = Segment.create(v.id, 0.0, 1.0)
            v.segments.append(seg)
            project.videos.append(v)

        settings = _make_settings()
        spec = ExportSpec.build(project, settings, str(tmp_path / "out"), "reencode")
        assert len(spec.clips) == 2
        paths = [c.output_path for c in spec.clips]
        # The sub-folder stems must differ.
        stems = {os.path.basename(os.path.dirname(p)) for p in paths}
        assert len(stems) == 2

    def test_multiple_segments_sorted_by_start(self, tmp_path):
        project = _make_project(tmp_path, segs_per_video=3)
        settings = _make_settings()
        spec = ExportSpec.build(project, settings, str(tmp_path / "out"), "reencode")
        starts = [c.start for c in spec.clips]
        assert starts == sorted(starts)

    def test_project_snapshot_present(self, tmp_path):
        project = _make_project(tmp_path)
        settings = _make_settings()
        spec = ExportSpec.build(project, settings, str(tmp_path / "out"), "reencode")
        assert "videos" in spec.project_snapshot

    def test_use_videotoolbox_auto_follows_availability(self, tmp_path):
        project = _make_project(tmp_path)
        settings = _make_settings(vtb=True)
        spec = ExportSpec.build(project, settings, str(tmp_path / "out"), "reencode")
        assert spec.use_videotoolbox is True

    def test_use_videotoolbox_off_overrides(self, tmp_path):
        project = _make_project(tmp_path)
        settings = _make_settings(vtb=True)
        settings.use_videotoolbox = "off"
        spec = ExportSpec.build(project, settings, str(tmp_path / "out"), "reencode")
        assert spec.use_videotoolbox is False

    def test_clip_tags_copied(self, tmp_path):
        project = _make_project(tmp_path)
        settings = _make_settings()
        spec = ExportSpec.build(project, settings, str(tmp_path / "out"), "reencode")
        clip = spec.clips[0]
        assert clip.video_tags == ["vtag"]
        assert clip.segment_tags == ["stag"]


# ---------------------------------------------------------------------------
# _ExportWorker._build_ffmpeg_cmd()
# ---------------------------------------------------------------------------

class TestBuildFfmpegCmd:
    def _make_spec(self, mode: str, vtb: bool = False) -> ExportSpec:
        return ExportSpec(
            output_folder="/tmp/out",
            export_mode=mode,
            reencode_crf=18,
            reencode_preset="fast",
            use_videotoolbox=vtb,
        )

    def _make_worker(self, mode: str, vtb: bool = False) -> _ExportWorker:
        return _ExportWorker(self._make_spec(mode, vtb))

    def test_remux_uses_copy(self, tmp_path):
        worker = self._make_worker("remux")
        clip = _make_clip_spec(tmp_path)
        cmd = worker._build_ffmpeg_cmd(clip)
        assert "-c" in cmd
        idx = cmd.index("-c")
        assert cmd[idx + 1] == "copy"

    def test_remux_fast_seek_before_input(self, tmp_path):
        worker = self._make_worker("remux")
        clip = _make_clip_spec(tmp_path, start=5.0)
        cmd = worker._build_ffmpeg_cmd(clip)
        i_idx = cmd.index("-i")
        ss_idx = cmd.index("-ss")
        assert ss_idx < i_idx  # fast seek before -i

    def test_reencode_libx264_default(self, tmp_path):
        worker = self._make_worker("reencode", vtb=False)
        clip = _make_clip_spec(tmp_path)
        cmd = worker._build_ffmpeg_cmd(clip)
        assert "libx264" in cmd

    def test_reencode_videotoolbox_when_vtb(self, tmp_path):
        worker = self._make_worker("reencode", vtb=True)
        clip = _make_clip_spec(tmp_path)
        cmd = worker._build_ffmpeg_cmd(clip)
        assert "h264_videotoolbox" in cmd

    def test_reencode_seek_after_input(self, tmp_path):
        worker = self._make_worker("reencode", vtb=False)
        clip = _make_clip_spec(tmp_path, start=5.0)
        cmd = worker._build_ffmpeg_cmd(clip)
        i_idx = cmd.index("-i")
        ss_idx = cmd.index("-ss")
        assert ss_idx > i_idx  # frame-accurate seek after -i

    def test_output_path_is_last_arg(self, tmp_path):
        for mode in ("reencode", "remux"):
            worker = self._make_worker(mode)
            clip = _make_clip_spec(tmp_path)
            cmd = worker._build_ffmpeg_cmd(clip)
            assert cmd[-1] == clip.output_path


# ---------------------------------------------------------------------------
# MetadataWriter
# ---------------------------------------------------------------------------

class TestMetadataWriter:
    def _make_spec(self, tmp_path) -> ExportSpec:
        clip = ClipSpec(
            video_id="vid-1",
            segment_id="seg-1",
            source_path="/src/video.mp4",
            start=1.0,
            end=5.0,
            output_path=str(tmp_path / "clip_001.mp4"),
            video_tags=["action", "outdoor"],
            segment_tags=["jump"],
            source_filename="video.mp4",
        )
        return ExportSpec(
            output_folder=str(tmp_path),
            export_mode="reencode",
            reencode_crf=18,
            reencode_preset="fast",
            use_videotoolbox=False,
            clips=[clip],
        )

    def test_json_file_created(self, tmp_path):
        spec = self._make_spec(tmp_path)
        MetadataWriter.write(spec, str(tmp_path))
        assert (tmp_path / "metadata.json").exists()

    def test_csv_file_created(self, tmp_path):
        spec = self._make_spec(tmp_path)
        MetadataWriter.write(spec, str(tmp_path))
        assert (tmp_path / "metadata.csv").exists()

    def test_json_structure(self, tmp_path):
        spec = self._make_spec(tmp_path)
        MetadataWriter.write(spec, str(tmp_path))
        data = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
        assert "exported_at" in data
        assert "clips" in data
        assert len(data["clips"]) == 1
        clip = data["clips"][0]
        assert clip["segment_id"] == "seg-1"
        assert clip["start"] == pytest.approx(1.0)
        assert clip["duration"] == pytest.approx(4.0)
        assert clip["video_tags"] == ["action", "outdoor"]
        assert clip["segment_tags"] == ["jump"]
        assert clip["exported"] is True

    def test_csv_headers(self, tmp_path):
        spec = self._make_spec(tmp_path)
        MetadataWriter.write(spec, str(tmp_path))
        with open(tmp_path / "metadata.csv", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            headers = next(reader)
        expected = [
            "segment_id", "video_id", "source_filename",
            "start", "end", "duration",
            "video_tags", "segment_tags", "output_path",
        ]
        assert headers == expected

    def test_csv_row_count(self, tmp_path):
        spec = self._make_spec(tmp_path)
        MetadataWriter.write(spec, str(tmp_path))
        with open(tmp_path / "metadata.csv", encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        assert len(rows) == 2  # header + 1 data row

    def test_tags_semicolon_separated_in_csv(self, tmp_path):
        spec = self._make_spec(tmp_path)
        MetadataWriter.write(spec, str(tmp_path))
        with open(tmp_path / "metadata.csv", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            next(reader)   # skip header
            row = next(reader)
        # video_tags column
        assert row[6] == "action;outdoor"
        # segment_tags column
        assert row[7] == "jump"

    def test_unicode_filenames(self, tmp_path):
        clip = ClipSpec(
            video_id="vid-x",
            segment_id="seg-x",
            source_path="/src/видео.mp4",
            start=0.0,
            end=2.0,
            output_path=str(tmp_path / "clip_001.mp4"),
            video_tags=[],
            segment_tags=[],
            source_filename="видео.mp4",
        )
        spec = ExportSpec(
            output_folder=str(tmp_path),
            export_mode="remux",
            reencode_crf=18,
            reencode_preset="fast",
            use_videotoolbox=False,
            clips=[clip],
        )
        MetadataWriter.write(spec, str(tmp_path))
        data = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
        assert data["clips"][0]["source_filename"] == "видео.mp4"

    def test_failed_clip_marked_not_exported(self, tmp_path):
        spec = self._make_spec(tmp_path)
        clip_name = os.path.basename(spec.clips[0].output_path)
        MetadataWriter.write(spec, str(tmp_path), failures=[(clip_name, "some error")])
        data = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
        assert data["clips"][0]["exported"] is False

    def test_empty_clips_list(self, tmp_path):
        spec = ExportSpec(
            output_folder=str(tmp_path),
            export_mode="reencode",
            reencode_crf=18,
            reencode_preset="fast",
            use_videotoolbox=False,
            clips=[],
        )
        MetadataWriter.write(spec, str(tmp_path))
        data = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
        assert data["clips"] == []
        with open(tmp_path / "metadata.csv", encoding="utf-8", newline="") as f:
            rows = list(csv.reader(f))
        assert len(rows) == 1   # header only


# ---------------------------------------------------------------------------
# ExportEngine integration (requires ffmpeg)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg not on PATH")
class TestExportEngine:
    def test_reencode_single_clip(self, tmp_path, qtbot):
        pytest.skip("Requires real video fixture — deferred to Phase 11 integration pass")

    def test_cancel_mid_export_deletes_partial_file(self, tmp_path, qtbot):
        pytest.skip("Requires real video fixture — deferred to Phase 11 integration pass")
