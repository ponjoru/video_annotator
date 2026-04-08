"""Unit tests for Phase 11: ToastNotification and PlaybackController validation gates."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from video_annotator.ui.toast_notification import DISPLAY_MS, ToastNotification


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_settings(min_dur: float = 0.5, max_dur: float = 300.0):
    s = MagicMock()
    s.min_segment_duration = min_dur
    s.max_segment_duration_warning = max_dur
    return s


def _make_controller(settings=None):
    from video_annotator.controllers.playback_controller import PlaybackController

    player = MagicMock()
    player.current_position = 0.0
    manager = MagicMock()
    fake_seg = MagicMock()
    fake_seg.id = "seg-1"
    manager.add_segment.return_value = fake_seg

    if settings is None:
        settings = _make_settings()

    ctrl = PlaybackController(player, manager, settings)
    return ctrl, player, manager


# ---------------------------------------------------------------------------
# ToastNotification
# ---------------------------------------------------------------------------

class TestToastNotification:
    def test_hidden_initially(self, qtbot):
        parent = __import__("PySide6.QtWidgets", fromlist=["QWidget"]).QWidget()
        qtbot.addWidget(parent)
        toast = ToastNotification(parent)
        assert toast.isHidden()

    def test_show_message_makes_visible(self, qtbot):
        from PySide6.QtWidgets import QWidget
        parent = QWidget()
        parent.resize(400, 300)
        qtbot.addWidget(parent)
        toast = ToastNotification(parent)
        toast.show_message("Hello")
        assert not toast.isHidden()

    def test_show_message_sets_text(self, qtbot):
        from PySide6.QtWidgets import QWidget
        parent = QWidget()
        qtbot.addWidget(parent)
        toast = ToastNotification(parent)
        toast.show_message("Test message")
        assert toast.text() == "Test message"

    def test_timer_hides_after_display_ms(self, qtbot):
        from PySide6.QtWidgets import QWidget
        parent = QWidget()
        parent.resize(400, 300)
        qtbot.addWidget(parent)
        toast = ToastNotification(parent)
        toast.show_message("Fading")
        with qtbot.waitSignal(toast._timer.timeout, timeout=DISPLAY_MS + 1000):
            pass
        assert toast.isHidden()

    def test_show_message_restarts_timer(self, qtbot):
        from PySide6.QtWidgets import QWidget
        parent = QWidget()
        parent.resize(400, 300)
        qtbot.addWidget(parent)
        toast = ToastNotification(parent)
        toast.show_message("First")
        toast.show_message("Second")
        assert toast._timer.isActive()
        assert toast.text() == "Second"

    def test_positioned_near_bottom_center(self, qtbot):
        from PySide6.QtWidgets import QWidget
        parent = QWidget()
        parent.resize(600, 400)
        qtbot.addWidget(parent)
        toast = ToastNotification(parent)
        toast.show_message("Position check")
        # Should be within 16+height px of the bottom
        assert toast.y() + toast.height() >= parent.height() - 16 - 2


# ---------------------------------------------------------------------------
# PlaybackController — short-segment toast
# ---------------------------------------------------------------------------

class TestPlaybackControllerValidation:
    def test_set_out_before_in_emits_validation_failed(self, qtbot):
        ctrl, player, _ = _make_controller()
        received = []
        ctrl.validation_failed.connect(received.append)
        ctrl.set_out_point()
        assert len(received) == 1
        assert "In point" in received[0]

    def test_short_segment_emits_validation_failed(self, qtbot):
        ctrl, player, _ = _make_controller(_make_settings(min_dur=2.0))
        received = []
        ctrl.validation_failed.connect(received.append)
        ctrl._current_video_id = "vid-1"
        ctrl._in_point = 0.0
        player.current_position = 1.0  # only 1 s < 2.0 min
        ctrl.set_out_point()
        assert len(received) == 1
        assert "short" in received[0].lower()

    def test_out_before_in_position_emits_validation_failed(self, qtbot):
        ctrl, player, _ = _make_controller()
        received = []
        ctrl.validation_failed.connect(received.append)
        ctrl._current_video_id = "vid-1"
        ctrl._in_point = 5.0
        player.current_position = 3.0   # out < in
        ctrl.set_out_point()
        assert len(received) == 1

    def test_valid_segment_emits_segment_confirmed(self, qtbot):
        ctrl, player, manager = _make_controller()
        confirmed = []
        ctrl.segment_confirmed.connect(lambda v, s: confirmed.append((v, s)))
        ctrl._current_video_id = "vid-1"
        ctrl._in_point = 0.0
        player.current_position = 5.0
        ctrl.set_out_point()
        assert len(confirmed) == 1
        assert confirmed[0][0] == "vid-1"

    def test_valid_segment_clears_in_point(self, qtbot):
        ctrl, player, _ = _make_controller()
        ctrl._current_video_id = "vid-1"
        ctrl._in_point = 0.0
        player.current_position = 5.0
        ctrl.set_out_point()
        assert ctrl._in_point is None


# ---------------------------------------------------------------------------
# PlaybackController — long-segment confirmation gate
# ---------------------------------------------------------------------------

class TestLongSegmentGate:
    def test_long_segment_emits_confirm_requested(self, qtbot):
        ctrl, player, _ = _make_controller(_make_settings(max_dur=10.0))
        requested = []
        ctrl.confirm_long_segment_requested.connect(requested.append)
        ctrl._current_video_id = "vid-1"
        ctrl._in_point = 0.0
        player.current_position = 20.0   # 20 s > 10 s max
        ctrl.set_out_point()
        assert len(requested) == 1
        assert requested[0] == pytest.approx(20.0)

    def test_long_segment_stores_pending_range(self, qtbot):
        ctrl, player, _ = _make_controller(_make_settings(max_dur=10.0))
        ctrl._current_video_id = "vid-1"
        ctrl._in_point = 2.0
        player.current_position = 25.0
        ctrl.set_out_point()
        assert ctrl._pending_start == pytest.approx(2.0)
        assert ctrl._pending_end == pytest.approx(25.0)

    def test_long_segment_does_not_add_segment_immediately(self, qtbot):
        ctrl, player, manager = _make_controller(_make_settings(max_dur=10.0))
        ctrl._current_video_id = "vid-1"
        ctrl._in_point = 0.0
        player.current_position = 20.0
        ctrl.set_out_point()
        manager.add_segment.assert_not_called()

    def test_confirm_pending_segment_adds_segment(self, qtbot):
        ctrl, player, manager = _make_controller(_make_settings(max_dur=10.0))
        confirmed = []
        ctrl.segment_confirmed.connect(lambda v, s: confirmed.append((v, s)))
        ctrl._current_video_id = "vid-1"
        ctrl._in_point = 0.0
        player.current_position = 20.0
        ctrl.set_out_point()   # triggers long gate
        ctrl.confirm_pending_segment()
        manager.add_segment.assert_called_once_with("vid-1", pytest.approx(0.0), pytest.approx(20.0))
        assert len(confirmed) == 1

    def test_cancel_pending_segment_does_not_add(self, qtbot):
        ctrl, player, manager = _make_controller(_make_settings(max_dur=10.0))
        ctrl._current_video_id = "vid-1"
        ctrl._in_point = 0.0
        player.current_position = 20.0
        ctrl.set_out_point()
        ctrl.cancel_pending_segment()
        manager.add_segment.assert_not_called()

    def test_cancel_pending_emits_segment_cancelled(self, qtbot):
        ctrl, player, _ = _make_controller(_make_settings(max_dur=10.0))
        cancelled = []
        ctrl.segment_cancelled.connect(lambda: cancelled.append(True))
        ctrl._current_video_id = "vid-1"
        ctrl._in_point = 0.0
        player.current_position = 20.0
        ctrl.set_out_point()
        ctrl.cancel_pending_segment()
        assert len(cancelled) == 1

    def test_confirm_without_pending_is_noop(self, qtbot):
        ctrl, player, manager = _make_controller()
        ctrl._current_video_id = "vid-1"
        ctrl.confirm_pending_segment()
        manager.add_segment.assert_not_called()

    def test_set_current_video_clears_pending(self, qtbot):
        ctrl, player, _ = _make_controller(_make_settings(max_dur=10.0))
        ctrl._current_video_id = "vid-1"
        ctrl._in_point = 0.0
        player.current_position = 20.0
        ctrl.set_out_point()   # sets pending
        ctrl.set_current_video("vid-2")
        assert ctrl._pending_start is None
        assert ctrl._pending_end is None


# ---------------------------------------------------------------------------
# PlaybackController — rubber-band range gate
# ---------------------------------------------------------------------------

class TestRubberBandGate:
    def test_short_rubber_band_emits_validation_failed(self, qtbot):
        ctrl, player, _ = _make_controller(_make_settings(min_dur=2.0))
        failed = []
        ctrl.validation_failed.connect(failed.append)
        ctrl._current_video_id = "vid-1"
        ctrl.set_segment_from_range(0.0, 1.0)   # 1 s < 2.0 s min
        assert len(failed) == 1

    def test_long_rubber_band_emits_confirm_requested(self, qtbot):
        ctrl, player, _ = _make_controller(_make_settings(max_dur=10.0))
        requested = []
        ctrl.confirm_long_segment_requested.connect(requested.append)
        ctrl._current_video_id = "vid-1"
        ctrl.set_segment_from_range(0.0, 50.0)
        assert len(requested) == 1
        assert requested[0] == pytest.approx(50.0)

    def test_valid_rubber_band_adds_segment(self, qtbot):
        ctrl, player, manager = _make_controller()
        ctrl._current_video_id = "vid-1"
        ctrl.set_segment_from_range(1.0, 6.0)
        manager.add_segment.assert_called_once_with("vid-1", pytest.approx(1.0), pytest.approx(6.0))
