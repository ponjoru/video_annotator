"""Unit tests for Phase 6 UI components: TagEditor, SegmentList, SidePanel, VideoListPanel."""

from __future__ import annotations

import pytest

from video_annotator.models.project import Segment, Video
from video_annotator.ui.segment_list import SegmentList
from video_annotator.ui.side_panel import SidePanel
from video_annotator.ui.tag_editor import TagEditor
from video_annotator.ui.video_list_panel import VideoListPanel


# ---------------------------------------------------------------------------
# TagEditor
# ---------------------------------------------------------------------------

class TestTagEditor:
    def test_set_tags_populates_chips(self, qtbot):
        editor = TagEditor()
        qtbot.addWidget(editor)
        editor.set_tags(["alpha", "beta"])
        assert set(editor._tags) == {"alpha", "beta"}

    def test_add_tag_emits_signal(self, qtbot):
        editor = TagEditor()
        qtbot.addWidget(editor)
        with qtbot.waitSignal(editor.tag_added, timeout=500) as blocker:
            editor._input.setText("newTag")
            editor._on_input_confirmed()
        assert blocker.args[0] == "newTag"

    def test_duplicate_tag_not_added(self, qtbot):
        editor = TagEditor()
        qtbot.addWidget(editor)
        editor.set_tags(["dup"])
        editor._input.setText("dup")
        editor._on_input_confirmed()
        assert editor._tags.count("dup") == 1

    def test_remove_tag_emits_signal(self, qtbot):
        editor = TagEditor()
        qtbot.addWidget(editor)
        editor.set_tags(["remove_me"])
        with qtbot.waitSignal(editor.tag_removed, timeout=500) as blocker:
            editor._on_chip_removed("remove_me")
        assert blocker.args[0] == "remove_me"
        assert "remove_me" not in editor._tags

    def test_vocabulary_updates_model(self, qtbot):
        editor = TagEditor()
        qtbot.addWidget(editor)
        editor.set_vocabulary(["cat", "dog", "fish"])
        assert editor._vocab_model.stringList() == ["cat", "dog", "fish"]

    def test_comma_in_text_triggers_confirmation(self, qtbot):
        editor = TagEditor()
        qtbot.addWidget(editor)
        with qtbot.waitSignal(editor.tag_added, timeout=500):
            editor._input.setText("tagName,")
            # _on_text_changed is triggered by setText; call it explicitly for the test
            editor._on_text_changed("tagName,")
        assert "tagName" in editor._tags


# ---------------------------------------------------------------------------
# SegmentList
# ---------------------------------------------------------------------------

class TestSegmentList:
    def _make_seg(self, start: float, end: float) -> Segment:
        return Segment.create("vid-1", start, end)

    def test_set_segments_creates_rows(self, qtbot):
        sl = SegmentList()
        qtbot.addWidget(sl)
        segs = [self._make_seg(0.0, 2.0), self._make_seg(3.0, 5.0)]
        sl.set_segments(segs)
        assert len(sl._rows) == 2

    def test_set_segments_clears_previous(self, qtbot):
        sl = SegmentList()
        qtbot.addWidget(sl)
        segs = [self._make_seg(0.0, 2.0)]
        sl.set_segments(segs)
        sl.set_segments([])
        assert len(sl._rows) == 0

    def test_delete_signal_forwarded(self, qtbot):
        sl = SegmentList()
        qtbot.addWidget(sl)
        seg = self._make_seg(0.0, 2.0)
        sl.set_segments([seg])
        with qtbot.waitSignal(sl.delete_requested, timeout=500) as blocker:
            row = sl._rows[seg.id]
            row.delete_requested.emit(seg.id)
        assert blocker.args[0] == seg.id

    def test_set_vocabulary_applied_to_rows(self, qtbot):
        sl = SegmentList()
        qtbot.addWidget(sl)
        seg = self._make_seg(1.0, 3.0)
        sl.set_segments([seg])
        sl.set_vocabulary(["alpha", "beta"])
        row = sl._rows[seg.id]
        assert row._tag_editor._vocab_model.stringList() == ["alpha", "beta"]

    def test_vocabulary_applied_on_set_segments(self, qtbot):
        sl = SegmentList()
        qtbot.addWidget(sl)
        sl.set_vocabulary(["x", "y"])
        seg = self._make_seg(0.0, 1.0)
        sl.set_segments([seg])
        row = sl._rows[seg.id]
        assert row._tag_editor._vocab_model.stringList() == ["x", "y"]

    def test_overlap_style_applied(self, qtbot):
        sl = SegmentList()
        qtbot.addWidget(sl)
        # Two overlapping segments
        s1 = self._make_seg(0.0, 5.0)
        s2 = self._make_seg(3.0, 7.0)
        sl.set_segments([s1, s2])
        # Both rows should have amber background (non-empty stylesheet)
        assert sl._rows[s1.id].styleSheet() != ""
        assert sl._rows[s2.id].styleSheet() != ""


# ---------------------------------------------------------------------------
# SidePanel
# ---------------------------------------------------------------------------

class TestSidePanel:
    def _make_video(self, status: str = "unseen") -> Video:
        v = Video.create("test.mp4", "test.mp4", "/tmp")
        v.status = status
        return v

    def test_load_video_enables_controls(self, qtbot):
        panel = SidePanel()
        qtbot.addWidget(panel)
        video = self._make_video()
        panel.load_video(video, [])
        assert panel._done_btn.isEnabled()
        assert panel._skipped_btn.isEnabled()

    def test_status_done_checked(self, qtbot):
        panel = SidePanel()
        qtbot.addWidget(panel)
        video = self._make_video(status="done")
        panel.load_video(video, [])
        assert panel._done_btn.isChecked()
        assert not panel._skipped_btn.isChecked()

    def test_status_skipped_checked(self, qtbot):
        panel = SidePanel()
        qtbot.addWidget(panel)
        video = self._make_video(status="skipped")
        panel.load_video(video, [])
        assert panel._skipped_btn.isChecked()
        assert not panel._done_btn.isChecked()

    def test_status_unseen_neither_checked(self, qtbot):
        panel = SidePanel()
        qtbot.addWidget(panel)
        video = self._make_video(status="unseen")
        panel.load_video(video, [])
        assert not panel._done_btn.isChecked()
        assert not panel._skipped_btn.isChecked()

    def test_update_video_status_toggles_buttons(self, qtbot):
        panel = SidePanel()
        qtbot.addWidget(panel)
        panel.update_video_status("done")
        assert panel._done_btn.isChecked()
        panel.update_video_status("in_progress")
        assert not panel._done_btn.isChecked()

    def test_update_vocabulary_propagates_to_segment_list(self, qtbot):
        panel = SidePanel()
        qtbot.addWidget(panel)
        video = self._make_video()
        seg = Segment.create(video.id, 0.0, 1.0)
        video.segments.append(seg)
        panel.load_video(video, [])
        panel.update_vocabulary(["foo", "bar"])
        assert panel._segment_list._vocabulary == ["foo", "bar"]


# ---------------------------------------------------------------------------
# VideoListPanel
# ---------------------------------------------------------------------------

class TestVideoListPanel:
    def _make_video(self, filename: str, status: str = "unseen") -> Video:
        v = Video.create(filename, filename, "/tmp")
        v.status = status
        return v

    def test_refresh_populates_list(self, qtbot):
        panel = VideoListPanel()
        qtbot.addWidget(panel)
        videos = [self._make_video("a.mp4"), self._make_video("b.mp4")]
        panel.refresh(videos)
        assert panel._list.count() == 2

    def test_refresh_clears_previous(self, qtbot):
        panel = VideoListPanel()
        qtbot.addWidget(panel)
        panel.refresh([self._make_video("a.mp4")])
        panel.refresh([])
        assert panel._list.count() == 0

    def test_progress_bar_counts_done_skipped(self, qtbot):
        panel = VideoListPanel()
        qtbot.addWidget(panel)
        videos = [
            self._make_video("a.mp4", "done"),
            self._make_video("b.mp4", "skipped"),
            self._make_video("c.mp4", "unseen"),
        ]
        panel.refresh(videos)
        assert panel._progress_bar.value() == 2
        assert panel._progress_bar.maximum() == 3

    def test_relink_button_hidden_when_no_missing(self, qtbot):
        panel = VideoListPanel()
        qtbot.addWidget(panel)
        panel.refresh([self._make_video("a.mp4", "done")])
        # isHidden() reflects the explicit hide/show state independent of parent visibility
        assert panel._relink_btn.isHidden()

    def test_relink_button_shown_when_missing(self, qtbot):
        panel = VideoListPanel()
        qtbot.addWidget(panel)
        v = self._make_video("missing.mp4", "missing")
        panel.refresh([v])
        assert not panel._relink_btn.isHidden()

    def test_double_click_emits_video_selected(self, qtbot):
        panel = VideoListPanel()
        qtbot.addWidget(panel)
        video = self._make_video("click.mp4")
        panel.refresh([video])
        with qtbot.waitSignal(panel.video_selected, timeout=500) as blocker:
            panel._list.itemDoubleClicked.emit(panel._list.item(0))
        assert blocker.args[0] == video.id
