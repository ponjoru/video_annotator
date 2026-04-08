"""Unit tests for SessionStore: save/load/migrate."""

import json
import os
import pytest

from video_annotator.models.project import Project, Segment, Video
from video_annotator.models.session_store import (
    SESSION_FILENAME,
    SessionCorruptError,
    SessionSaveError,
    SessionStore,
)


@pytest.fixture
def tmp_project(tmp_path):
    """Return a minimal Project rooted in a temp directory."""
    return Project(
        source_folder=str(tmp_path),
        output_folder=str(tmp_path / "out"),
        created_at="2026-01-01T00:00:00Z",
    )


class TestSessionStoreExists:
    def test_returns_false_when_no_file(self, tmp_path):
        assert not SessionStore.exists(str(tmp_path))

    def test_returns_true_after_save(self, tmp_project, tmp_path):
        SessionStore.save(tmp_project)
        assert SessionStore.exists(str(tmp_path))


class TestSessionStoreSave:
    def test_file_created(self, tmp_project, tmp_path):
        SessionStore.save(tmp_project)
        assert (tmp_path / SESSION_FILENAME).exists()

    def test_tmp_file_cleaned_up(self, tmp_project, tmp_path):
        SessionStore.save(tmp_project)
        assert not (tmp_path / (SESSION_FILENAME + ".tmp")).exists()

    def test_raises_on_readonly_directory(self, tmp_project, tmp_path):
        os.chmod(str(tmp_path), 0o444)
        try:
            with pytest.raises(SessionSaveError):
                SessionStore.save(tmp_project)
        finally:
            os.chmod(str(tmp_path), 0o755)

    def test_output_is_valid_json(self, tmp_project, tmp_path):
        SessionStore.save(tmp_project)
        raw = (tmp_path / SESSION_FILENAME).read_text()
        data = json.loads(raw)
        assert data["version"] == 1
        assert data["source_folder"] == str(tmp_path)


class TestSessionStoreLoad:
    def test_raises_when_file_missing(self, tmp_path):
        with pytest.raises(SessionCorruptError):
            SessionStore.load(str(tmp_path))

    def test_raises_on_invalid_json(self, tmp_path):
        (tmp_path / SESSION_FILENAME).write_text("NOT JSON")
        with pytest.raises(SessionCorruptError):
            SessionStore.load(str(tmp_path))

    def test_raises_on_unsupported_version(self, tmp_path):
        (tmp_path / SESSION_FILENAME).write_text(json.dumps({"version": 999}))
        with pytest.raises(SessionCorruptError):
            SessionStore.load(str(tmp_path))

    def test_roundtrip(self, tmp_project, tmp_path):
        v = Video.create("a.mp4", "a.mp4", str(tmp_path))
        v.tags = ["test"]
        seg = Segment.create(v.id, 1.0, 2.5)
        seg.tags = ["action"]
        v.segments.append(seg)
        tmp_project.videos.append(v)
        tmp_project.tag_vocabulary = ["action", "test"]

        SessionStore.save(tmp_project)
        loaded = SessionStore.load(str(tmp_path))

        assert loaded.source_folder == str(tmp_path)
        assert len(loaded.videos) == 1
        lv = loaded.videos[0]
        assert lv.filename == "a.mp4"
        assert lv.tags == ["test"]
        assert len(lv.segments) == 1
        assert lv.segments[0].tags == ["action"]
        assert loaded.tag_vocabulary == ["action", "test"]

    def test_missing_video_files_flagged(self, tmp_path):
        """Videos whose file is absent get status='missing' on load."""
        p = Project(
            source_folder=str(tmp_path),
            output_folder=str(tmp_path / "out"),
            created_at="2026-01-01T00:00:00Z",
        )
        v = Video.create("ghost.mp4", "ghost.mp4", str(tmp_path))
        # File does NOT exist on disk.
        p.videos.append(v)
        SessionStore.save(p)

        loaded = SessionStore.load(str(tmp_path))
        assert loaded.videos[0].status == "missing"

    def test_existing_video_files_not_flagged(self, tmp_path):
        """Videos whose file exists keep their original status."""
        real_file = tmp_path / "real.mp4"
        real_file.write_bytes(b"")   # empty but present

        p = Project(
            source_folder=str(tmp_path),
            output_folder=str(tmp_path / "out"),
            created_at="2026-01-01T00:00:00Z",
        )
        v = Video.create("real.mp4", "real.mp4", str(tmp_path))
        v.status = "done"
        p.videos.append(v)
        SessionStore.save(p)

        loaded = SessionStore.load(str(tmp_path))
        assert loaded.videos[0].status == "done"
