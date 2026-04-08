"""Unit tests for SettingsPanel dialog."""

from __future__ import annotations

import pytest

from video_annotator.settings import SettingsManager
from video_annotator.ui.settings_panel import SettingsPanel


@pytest.fixture
def settings(qtbot):
    """Fresh SettingsManager with known defaults (writes to real QSettings storage)."""
    s = SettingsManager()
    # Reset to known state so persisted values from previous runs don't bleed in.
    s.min_segment_duration = 0.5
    s.max_segment_duration_warning = 300.0
    s.default_export_mode = "reencode"
    s.reencode_crf = 18
    s.reencode_preset = "fast"
    s.use_videotoolbox = "auto"
    return s


class TestSettingsPanelLoad:
    def test_min_duration_loaded(self, qtbot, settings):
        settings.min_segment_duration = 1.5
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        assert dlg._min_dur_spin.value() == pytest.approx(1.5)

    def test_max_duration_loaded(self, qtbot, settings):
        settings.max_segment_duration_warning = 120.0
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        assert dlg._max_dur_spin.value() == pytest.approx(120.0)

    def test_export_mode_reencode_selected(self, qtbot, settings):
        settings.default_export_mode = "reencode"
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        assert dlg._export_mode_combo.currentData() == "reencode"

    def test_export_mode_remux_selected(self, qtbot, settings):
        settings.default_export_mode = "remux"
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        assert dlg._export_mode_combo.currentData() == "remux"

    def test_crf_loaded(self, qtbot, settings):
        settings.reencode_crf = 23
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        assert dlg._crf_spin.value() == 23

    def test_preset_loaded(self, qtbot, settings):
        settings.reencode_preset = "medium"
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        assert dlg._preset_combo.currentData() == "medium"

    def test_vtb_auto_selected(self, qtbot, settings):
        settings.use_videotoolbox = "auto"
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        assert dlg._vtb_combo.currentData() == "auto"

    def test_vtb_off_selected(self, qtbot, settings):
        settings.use_videotoolbox = "off"
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        assert dlg._vtb_combo.currentData() == "off"


class TestSettingsPanelApply:
    def test_ok_persists_min_duration(self, qtbot, settings):
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        dlg._min_dur_spin.setValue(2.0)
        dlg._apply_and_accept()
        assert settings.min_segment_duration == pytest.approx(2.0)

    def test_ok_persists_max_duration(self, qtbot, settings):
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        dlg._max_dur_spin.setValue(600.0)
        dlg._apply_and_accept()
        assert settings.max_segment_duration_warning == pytest.approx(600.0)

    def test_ok_persists_export_mode(self, qtbot, settings):
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        idx = dlg._export_mode_combo.findData("remux")
        dlg._export_mode_combo.setCurrentIndex(idx)
        dlg._apply_and_accept()
        assert settings.default_export_mode == "remux"

    def test_ok_persists_crf(self, qtbot, settings):
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        dlg._crf_spin.setValue(28)
        dlg._apply_and_accept()
        assert settings.reencode_crf == 28

    def test_ok_persists_preset(self, qtbot, settings):
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        idx = dlg._preset_combo.findData("slow")
        dlg._preset_combo.setCurrentIndex(idx)
        dlg._apply_and_accept()
        assert settings.reencode_preset == "slow"

    def test_ok_persists_videotoolbox(self, qtbot, settings):
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        idx = dlg._vtb_combo.findData("on")
        dlg._vtb_combo.setCurrentIndex(idx)
        dlg._apply_and_accept()
        assert settings.use_videotoolbox == "on"

    def test_cancel_does_not_persist_changes(self, qtbot, settings):
        original_crf = settings.reencode_crf
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        dlg._crf_spin.setValue(51)
        dlg.reject()
        # Settings object should be unchanged.
        assert settings.reencode_crf == original_crf

    def test_cancel_does_not_persist_mode_change(self, qtbot, settings):
        settings.default_export_mode = "reencode"
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        idx = dlg._export_mode_combo.findData("remux")
        dlg._export_mode_combo.setCurrentIndex(idx)
        dlg.reject()
        assert settings.default_export_mode == "reencode"


class TestSettingsPanelSpinBounds:
    def test_min_duration_minimum_bound(self, qtbot, settings):
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        dlg._min_dur_spin.setValue(0.05)   # below minimum
        assert dlg._min_dur_spin.value() >= 0.1

    def test_crf_maximum_bound(self, qtbot, settings):
        dlg = SettingsPanel(settings)
        qtbot.addWidget(dlg)
        dlg._crf_spin.setValue(99)   # above maximum
        assert dlg._crf_spin.value() <= 51
