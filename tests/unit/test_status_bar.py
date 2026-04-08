"""Unit tests for StatusBar widget."""

from __future__ import annotations

import pytest

from video_annotator.ui.status_bar import StatusBar


@pytest.fixture
def bar(qtbot):
    w = StatusBar()
    qtbot.addWidget(w)
    return w


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

class TestStatusBarInit:
    def test_label_default_text(self, bar):
        assert bar._label.text() == "No folder open"

    def test_progress_bar_hidden_initially(self, bar):
        assert bar._progress_bar.isHidden()

    def test_cancel_btn_hidden_initially(self, bar):
        assert bar._cancel_btn.isHidden()


# ---------------------------------------------------------------------------
# Idle state
# ---------------------------------------------------------------------------

class TestSetIdle:
    def test_idle_text_format(self, bar):
        bar.set_idle("video.mp4", done=2, skipped=1, segments=5, total=10)
        assert bar._label.text() == '"video.mp4"  —  2 done, 1 skipped, 5 segments  [2/10]'

    def test_idle_text_not_shown_during_export(self, bar):
        bar.on_export_started(5)
        bar.set_idle("video.mp4", done=1, skipped=0, segments=3, total=10)
        # Label should still show export text, not idle text
        assert "Starting export" in bar._label.text()

    def test_idle_text_stored_for_later_restore(self, bar):
        bar.set_idle("clip.mp4", done=0, skipped=0, segments=1, total=4)
        assert '"clip.mp4"' in bar._idle_text


# ---------------------------------------------------------------------------
# Export started
# ---------------------------------------------------------------------------

class TestExportStarted:
    def test_progress_bar_becomes_visible(self, bar):
        bar.on_export_started(10)
        assert not bar._progress_bar.isHidden()

    def test_cancel_btn_becomes_visible(self, bar):
        bar.on_export_started(10)
        assert not bar._cancel_btn.isHidden()

    def test_progress_bar_maximum_set(self, bar):
        bar.on_export_started(7)
        assert bar._progress_bar.maximum() == 7

    def test_progress_bar_value_reset_to_zero(self, bar):
        bar._progress_bar.setValue(5)
        bar.on_export_started(10)
        assert bar._progress_bar.value() == 0

    def test_label_shows_clip_count(self, bar):
        bar.on_export_started(3)
        assert "3" in bar._label.text()


# ---------------------------------------------------------------------------
# Clip started / done
# ---------------------------------------------------------------------------

class TestClipProgress:
    def test_clip_started_updates_label(self, bar):
        bar.on_export_started(5)
        bar.on_clip_started(2, 5, "clip_002.mp4")
        assert "2" in bar._label.text()
        assert "5" in bar._label.text()
        assert "clip_002.mp4" in bar._label.text()

    def test_clip_started_sets_progress_bar_to_index_minus_one(self, bar):
        bar.on_export_started(5)
        bar.on_clip_started(3, 5, "clip.mp4")
        assert bar._progress_bar.value() == 2

    def test_clip_done_sets_progress_bar_to_index(self, bar):
        bar.on_export_started(5)
        bar.on_clip_done(3)
        assert bar._progress_bar.value() == 3

    def test_clip_done_full_completion(self, bar):
        bar.on_export_started(4)
        bar.on_clip_done(4)
        assert bar._progress_bar.value() == 4


# ---------------------------------------------------------------------------
# Export complete
# ---------------------------------------------------------------------------

class TestExportComplete:
    def test_progress_bar_hidden(self, bar):
        bar.on_export_started(2)
        bar.on_export_complete(2, "/out/folder")
        assert bar._progress_bar.isHidden()

    def test_cancel_btn_hidden(self, bar):
        bar.on_export_started(2)
        bar.on_export_complete(2, "/out/folder")
        assert bar._cancel_btn.isHidden()

    def test_label_shows_clip_count_and_folder(self, bar):
        bar.on_export_complete(5, "/tmp/exports")
        assert "5" in bar._label.text()
        assert "/tmp/exports" in bar._label.text()

    def test_clear_timer_started(self, bar):
        bar.on_export_complete(1, "/out")
        assert bar._clear_timer.isActive()

    def test_return_to_idle_after_timer(self, bar, qtbot):
        bar.set_idle("f.mp4", done=0, skipped=0, segments=0, total=1)
        bar.on_export_complete(1, "/out")
        with qtbot.waitSignal(bar._clear_timer.timeout, timeout=6000):
            pass
        assert bar._label.text() == bar._idle_text


# ---------------------------------------------------------------------------
# Export cancelled
# ---------------------------------------------------------------------------

class TestExportCancelled:
    def test_progress_bar_hidden(self, bar):
        bar.on_export_started(3)
        bar.on_export_cancelled()
        assert bar._progress_bar.isHidden()

    def test_cancel_btn_hidden(self, bar):
        bar.on_export_started(3)
        bar.on_export_cancelled()
        assert bar._cancel_btn.isHidden()

    def test_label_shows_cancelled(self, bar):
        bar.on_export_cancelled()
        assert "cancelled" in bar._label.text().lower()

    def test_clear_timer_started(self, bar):
        bar.on_export_cancelled()
        assert bar._clear_timer.isActive()


# ---------------------------------------------------------------------------
# Export error
# ---------------------------------------------------------------------------

class TestExportError:
    def test_progress_bar_hidden(self, bar):
        bar.on_export_started(2)
        bar.on_export_error(1, "/tmp/export_log.txt")
        assert bar._progress_bar.isHidden()

    def test_cancel_btn_hidden(self, bar):
        bar.on_export_started(2)
        bar.on_export_error(1, "/tmp/export_log.txt")
        assert bar._cancel_btn.isHidden()

    def test_label_shows_failure_count_and_log_path(self, bar):
        bar.on_export_error(3, "/logs/export_log.txt")
        assert "3" in bar._label.text()
        assert "/logs/export_log.txt" in bar._label.text()

    def test_clear_timer_started(self, bar):
        bar.on_export_error(1, "/log.txt")
        assert bar._clear_timer.isActive()


# ---------------------------------------------------------------------------
# Return to idle
# ---------------------------------------------------------------------------

class TestReturnToIdle:
    def test_progress_bar_hidden(self, bar):
        bar._progress_bar.setVisible(True)
        bar._return_to_idle()
        assert bar._progress_bar.isHidden()

    def test_cancel_btn_hidden(self, bar):
        bar._cancel_btn.setVisible(True)
        bar._return_to_idle()
        assert bar._cancel_btn.isHidden()

    def test_label_restored_to_idle_text(self, bar):
        bar.set_idle("my_file.mp4", done=1, skipped=0, segments=2, total=5)
        bar.on_export_cancelled()
        bar._return_to_idle()
        assert bar._label.text() == bar._idle_text

    def test_default_idle_text_when_no_set_idle_called(self, bar):
        bar._return_to_idle()
        assert bar._label.text() == "No folder open"


# ---------------------------------------------------------------------------
# Cancel signal
# ---------------------------------------------------------------------------

class TestCancelSignal:
    def test_cancel_btn_emits_signal(self, bar, qtbot):
        bar.on_export_started(5)
        with qtbot.waitSignal(bar.cancel_export_clicked, timeout=1000):
            bar._cancel_btn.click()
