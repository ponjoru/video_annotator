"""Unit tests for PlayerPanel backend abstraction and QtMultimediaBackend."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from video_annotator.ui.player_panel import PlayerPanel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(backend: str = "qt_multimedia"):
    s = MagicMock()
    s.player_backend = backend
    return s


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

class TestBackendSelection:
    def test_default_selects_qt_multimedia(self, qtbot):
        settings = _make_settings("qt_multimedia")
        panel = PlayerPanel(settings=settings)
        qtbot.addWidget(panel)
        from video_annotator.ui.backends.qt_multimedia_backend import QtMultimediaBackend
        assert isinstance(panel._backend, QtMultimediaBackend)

    def test_mpv_subprocess_setting_selects_mpv_backend(self, qtbot):
        settings = _make_settings("mpv_subprocess")
        panel = PlayerPanel(settings=settings)
        qtbot.addWidget(panel)
        from video_annotator.ui.backends.mpv_subprocess_backend import MpvSubprocessBackend
        assert isinstance(panel._backend, MpvSubprocessBackend)

    def test_no_settings_falls_back_to_qt_multimedia(self, qtbot):
        panel = PlayerPanel(settings=None)
        qtbot.addWidget(panel)
        from video_annotator.ui.backends.qt_multimedia_backend import QtMultimediaBackend
        assert isinstance(panel._backend, QtMultimediaBackend)

    def test_qt_multimedia_backend_has_video_widget(self, qtbot):
        settings = _make_settings("qt_multimedia")
        panel = PlayerPanel(settings=settings)
        qtbot.addWidget(panel)
        assert hasattr(panel._backend, "video_widget")
        assert panel._backend.video_widget is not None

    def test_panel_signals_forwarded_from_backend(self, qtbot):
        settings = _make_settings("qt_multimedia")
        panel = PlayerPanel(settings=settings)
        qtbot.addWidget(panel)
        received = []
        panel.position_changed.connect(received.append)
        # Emit directly on backend — panel must forward it.
        panel._backend.position_changed.emit(3.14)
        assert received == [pytest.approx(3.14)]


# ---------------------------------------------------------------------------
# QtMultimediaBackend — unit tests (no real video file needed)
# ---------------------------------------------------------------------------

class TestQtMultimediaBackend:
    @pytest.fixture
    def backend(self, qtbot):
        from video_annotator.ui.backends.qt_multimedia_backend import QtMultimediaBackend
        b = QtMultimediaBackend()
        qtbot.addWidget(b.video_widget)
        return b

    def test_initial_position_is_zero(self, backend):
        assert backend.current_position == pytest.approx(0.0)

    def test_position_changed_signal_on_position_update(self, backend, qtbot):
        received = []
        backend.position_changed.connect(received.append)
        backend._on_position_changed(2500)   # 2500 ms = 2.5 s
        assert received == [pytest.approx(2.5)]

    def test_current_position_cached_from_signal(self, backend):
        backend._on_position_changed(4000)
        assert backend.current_position == pytest.approx(4.0)

    def test_duration_known_emitted_once(self, backend, qtbot):
        received = []
        backend.duration_known.connect(received.append)
        backend._on_duration_changed(60000)   # 60 s
        backend._on_duration_changed(60000)   # duplicate — should not re-emit
        assert len(received) == 1
        assert received[0] == pytest.approx(60.0)

    def test_duration_known_not_emitted_for_zero(self, backend, qtbot):
        received = []
        backend.duration_known.connect(received.append)
        backend._on_duration_changed(0)
        assert len(received) == 0

    def test_paused_changed_true_when_paused(self, backend, qtbot):
        from PySide6.QtMultimedia import QMediaPlayer
        received = []
        backend.paused_changed.connect(received.append)
        backend._on_playback_state_changed(QMediaPlayer.PlaybackState.PausedState)
        assert received == [True]

    def test_paused_changed_false_when_playing(self, backend, qtbot):
        from PySide6.QtMultimedia import QMediaPlayer
        received = []
        backend.paused_changed.connect(received.append)
        backend._on_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
        assert received == [False]

    def test_playback_ended_on_end_of_media(self, backend, qtbot):
        from PySide6.QtMultimedia import QMediaPlayer
        ended = []
        backend.playback_ended.connect(lambda: ended.append(True))
        backend._on_media_status_changed(QMediaPlayer.MediaStatus.EndOfMedia)
        assert len(ended) == 1

    def test_set_speed_calls_set_playback_rate(self, backend):
        backend._player = MagicMock()
        backend.set_speed(2.0)
        backend._player.setPlaybackRate.assert_called_once_with(2.0)

    def test_set_volume_scales_to_0_1(self, backend):
        backend._audio_output = MagicMock()
        backend.set_volume(75)
        backend._audio_output.setVolume.assert_called_once_with(pytest.approx(0.75))

    def test_seek_absolute_converts_to_ms(self, backend):
        backend._player = MagicMock()
        backend.seek_absolute(5.5)
        backend._player.setPosition.assert_called_once_with(5500)

    def test_seek_relative_uses_cached_position(self, backend):
        backend._player = MagicMock()
        backend._cached_position = 10.0
        backend.seek_relative(3.0)
        backend._player.setPosition.assert_called_once_with(13000)

    def test_seek_relative_clamps_to_zero(self, backend):
        backend._player = MagicMock()
        backend._cached_position = 2.0
        backend.seek_relative(-10.0)
        backend._player.setPosition.assert_called_once_with(0)

    def test_load_resets_duration_emitted_flag(self, backend):
        backend._duration_emitted = True
        with patch.object(backend._player, "setSource"), \
             patch.object(backend._player, "pause"), \
             patch.object(backend, "_probe_vfr_async"):
            backend.load("/tmp/fake.mp4")
        assert backend._duration_emitted is False

    def test_load_resets_cached_position(self, backend):
        backend._cached_position = 99.0
        with patch.object(backend._player, "setSource"), \
             patch.object(backend._player, "pause"), \
             patch.object(backend, "_probe_vfr_async"):
            backend.load("/tmp/fake.mp4")
        assert backend._cached_position == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# SettingsPanel — backend combo added
# ---------------------------------------------------------------------------

class TestSettingsPanelBackend:
    @pytest.fixture
    def settings(self, qtbot):
        from video_annotator.settings import SettingsManager
        s = SettingsManager()
        s.player_backend = "qt_multimedia"
        return s

    def test_backend_combo_loaded_qt_multimedia(self, qtbot, settings):
        from video_annotator.ui.settings_panel import SettingsPanel
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        assert dlg._backend_combo.currentData() == "qt_multimedia"

    def test_backend_combo_loaded_mpv(self, qtbot, settings):
        from video_annotator.ui.settings_panel import SettingsPanel
        settings.player_backend = "mpv_subprocess"
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        assert dlg._backend_combo.currentData() == "mpv_subprocess"

    def test_apply_persists_backend_selection(self, qtbot, settings):
        from video_annotator.ui.settings_panel import SettingsPanel
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        idx = dlg._backend_combo.findData("mpv_subprocess")
        dlg._backend_combo.setCurrentIndex(idx)
        dlg._apply_and_accept()
        assert settings.player_backend == "mpv_subprocess"
