"""Unit tests for ProjectManager mutations and signal emissions."""

import pytest

from video_annotator.controllers.project_manager import ProjectManager
from video_annotator.models.project import Video
from video_annotator.settings import SettingsManager


@pytest.fixture
def manager(qtbot, tmp_path):
    """A ProjectManager with a fresh project in a temp folder."""
    settings = SettingsManager()
    pm = ProjectManager(settings)
    pm.new_project(str(tmp_path), [])
    # Add one video so mutation tests have something to work with.
    v = Video.create("clip.mp4", "clip.mp4", str(tmp_path))
    pm.project.videos.append(v)
    return pm


@pytest.fixture
def video_id(manager):
    return manager.project.videos[0].id


class TestAddSegment:
    def test_segment_appears_in_video(self, manager, video_id):
        seg = manager.add_segment(video_id, 1.0, 5.0)
        video = manager.project.get_video(video_id)
        assert any(s.id == seg.id for s in video.segments)

    def test_segment_added_signal_emitted(self, manager, video_id, qtbot):
        with qtbot.waitSignal(manager.segment_added, timeout=500) as blocker:
            manager.add_segment(video_id, 1.0, 5.0)
        assert blocker.args[0] == video_id

    def test_video_status_becomes_in_progress(self, manager, video_id):
        assert manager.project.get_video(video_id).status == "unseen"
        manager.add_segment(video_id, 1.0, 5.0)
        assert manager.project.get_video(video_id).status == "in_progress"

    def test_segments_inserted_sorted_by_start(self, manager, video_id):
        manager.add_segment(video_id, 5.0, 6.0)
        manager.add_segment(video_id, 1.0, 2.0)
        manager.add_segment(video_id, 3.0, 4.0)
        starts = [s.start for s in manager.project.get_video(video_id).segments]
        assert starts == sorted(starts)


class TestDeleteSegment:
    def test_segment_removed_from_video(self, manager, video_id):
        seg = manager.add_segment(video_id, 1.0, 5.0)
        manager.delete_segment(video_id, seg.id)
        video = manager.project.get_video(video_id)
        assert not any(s.id == seg.id for s in video.segments)

    def test_segment_deleted_signal_emitted(self, manager, video_id, qtbot):
        seg = manager.add_segment(video_id, 1.0, 5.0)
        with qtbot.waitSignal(manager.segment_deleted, timeout=500) as blocker:
            manager.delete_segment(video_id, seg.id)
        assert blocker.args[1] == seg.id


class TestRenameTag:
    def test_tag_renamed_across_all_videos_and_segments(self, manager, video_id):
        manager.add_video_tag(video_id, "old")
        seg = manager.add_segment(video_id, 0.0, 1.0)
        manager.add_segment_tag(video_id, seg.id, "old")
        manager.rename_tag("old", "new")
        video = manager.project.get_video(video_id)
        assert "new" in video.tags
        assert "old" not in video.tags
        assert "new" in video.segments[0].tags
        assert "old" not in video.segments[0].tags

    def test_vocabulary_updated(self, manager, video_id):
        manager.add_video_tag(video_id, "old")
        manager.rename_tag("old", "new")
        assert "new" in manager.project.tag_vocabulary
        assert "old" not in manager.project.tag_vocabulary

    def test_tag_vocabulary_changed_signal_emitted(self, manager, video_id, qtbot):
        manager.add_video_tag(video_id, "alpha")
        with qtbot.waitSignal(manager.tag_vocabulary_changed, timeout=500):
            manager.rename_tag("alpha", "beta")


class TestUndoIntegration:
    def test_undo_add_removes_segment(self, manager, video_id, qtbot):
        from video_annotator.undo import UndoStack
        stack = UndoStack()
        manager._undo_stack = stack
        seg = manager.add_segment(video_id, 1.0, 5.0)
        assert len(manager.project.get_video(video_id).segments) == 1
        stack.undo()
        assert len(manager.project.get_video(video_id).segments) == 0

    def test_undo_delete_restores_segment(self, manager, video_id, qtbot):
        from video_annotator.undo import UndoStack
        stack = UndoStack()
        manager._undo_stack = stack
        seg = manager.add_segment(video_id, 1.0, 5.0)
        stack.clear()   # clear the add from undo history
        manager._undo_stack = stack
        manager.delete_segment(video_id, seg.id)
        assert len(manager.project.get_video(video_id).segments) == 0
        stack.undo()
        assert len(manager.project.get_video(video_id).segments) == 1
        assert manager.project.get_video(video_id).segments[0].id == seg.id

    def test_undo_trim_restores_original_bounds(self, manager, video_id, qtbot):
        from video_annotator.undo import UndoStack
        stack = UndoStack()
        manager._undo_stack = stack
        seg = manager.add_segment(video_id, 1.0, 5.0)
        stack.clear()
        manager._undo_stack = stack
        manager.trim_segment(video_id, seg.id, 2.0, 4.0)
        trimmed = manager.project.get_video(video_id).segments[0]
        assert trimmed.start == pytest.approx(2.0)
        assert trimmed.end == pytest.approx(4.0)
        stack.undo()
        restored = manager.project.get_video(video_id).segments[0]
        assert restored.start == pytest.approx(1.0)
        assert restored.end == pytest.approx(5.0)


class TestTrimSegment:
    def test_trim_updates_bounds(self, manager, video_id):
        seg = manager.add_segment(video_id, 1.0, 10.0)
        manager.trim_segment(video_id, seg.id, 2.0, 8.0)
        updated = manager.project.get_video(video_id).segments[0]
        assert updated.start == pytest.approx(2.0)
        assert updated.end == pytest.approx(8.0)
        assert updated.duration == pytest.approx(6.0)

    def test_trim_re_sorts_segments(self, manager, video_id):
        s1 = manager.add_segment(video_id, 1.0, 3.0)
        s2 = manager.add_segment(video_id, 5.0, 7.0)
        # Trim s2 to start before s1 ends (overlap allowed, but order should be by start)
        manager.trim_segment(video_id, s2.id, 0.5, 2.5)
        starts = [s.start for s in manager.project.get_video(video_id).segments]
        assert starts == sorted(starts)
