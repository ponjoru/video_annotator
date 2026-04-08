"""SettingsPanel: modal dialog for application preferences."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from video_annotator.settings import SettingsManager


class SettingsPanel(QDialog):

    def __init__(self, settings: "SettingsManager", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(400)
        self._settings = settings
        self._build_ui()
        self._load_values()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # --- Segment validation group ---
        validation_group = QGroupBox("Segment Validation")
        form = QFormLayout(validation_group)

        self._min_dur_spin = QDoubleSpinBox()
        self._min_dur_spin.setRange(0.1, 10.0)
        self._min_dur_spin.setSuffix(" s")
        self._min_dur_spin.setDecimals(1)
        form.addRow("Minimum segment duration:", self._min_dur_spin)

        self._max_dur_spin = QDoubleSpinBox()
        self._max_dur_spin.setRange(10.0, 7200.0)
        self._max_dur_spin.setSuffix(" s")
        self._max_dur_spin.setDecimals(0)
        form.addRow("Long-segment warning threshold:", self._max_dur_spin)
        layout.addWidget(validation_group)

        # --- Export group ---
        export_group = QGroupBox("Export Defaults")
        export_form = QFormLayout(export_group)

        self._export_mode_combo = QComboBox()
        self._export_mode_combo.addItem("Re-encode (frame-accurate)", "reencode")
        self._export_mode_combo.addItem("Lossless re-mux (keyframe boundaries)", "remux")
        export_form.addRow("Default export mode:", self._export_mode_combo)

        self._crf_spin = QSpinBox()
        self._crf_spin.setRange(0, 51)
        export_form.addRow("Re-encode CRF (0=lossless, 23=default):", self._crf_spin)

        self._preset_combo = QComboBox()
        for p in ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]:
            self._preset_combo.addItem(p, p)
        export_form.addRow("Re-encode preset:", self._preset_combo)

        self._vtb_combo = QComboBox()
        self._vtb_combo.addItem("Auto-detect", "auto")
        self._vtb_combo.addItem("Always use VideoToolbox", "on")
        self._vtb_combo.addItem("Never use VideoToolbox", "off")
        export_form.addRow("VideoToolbox (macOS HW encode):", self._vtb_combo)

        layout.addWidget(export_group)

        # --- Player backend group ---
        player_group = QGroupBox("Video Player")
        player_form = QFormLayout(player_group)

        self._backend_combo = QComboBox()
        self._backend_combo.addItem("Embedded player (recommended)", "qt_multimedia")
        self._backend_combo.addItem("External mpv window (legacy)", "mpv_subprocess")
        player_form.addRow("Player backend:", self._backend_combo)

        from PySide6.QtWidgets import QLabel
        note = QLabel("Restart required to apply backend change.")
        note.setStyleSheet("color: #888; font-size: 11px;")
        player_form.addRow("", note)

        layout.addWidget(player_group)

        # --- Dialog buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._apply_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_values(self) -> None:
        self._min_dur_spin.setValue(self._settings.min_segment_duration)
        self._max_dur_spin.setValue(self._settings.max_segment_duration_warning)

        idx = self._export_mode_combo.findData(self._settings.default_export_mode)
        self._export_mode_combo.setCurrentIndex(max(0, idx))

        self._crf_spin.setValue(self._settings.reencode_crf)

        idx = self._preset_combo.findData(self._settings.reencode_preset)
        self._preset_combo.setCurrentIndex(max(0, idx))

        idx = self._vtb_combo.findData(self._settings.use_videotoolbox)
        self._vtb_combo.setCurrentIndex(max(0, idx))

        idx = self._backend_combo.findData(self._settings.player_backend)
        self._backend_combo.setCurrentIndex(max(0, idx))

    def _apply_and_accept(self) -> None:
        self._settings.min_segment_duration = self._min_dur_spin.value()
        self._settings.max_segment_duration_warning = self._max_dur_spin.value()
        self._settings.default_export_mode = self._export_mode_combo.currentData()
        self._settings.reencode_crf = self._crf_spin.value()
        self._settings.reencode_preset = self._preset_combo.currentData()
        self._settings.use_videotoolbox = self._vtb_combo.currentData()
        self._settings.player_backend = self._backend_combo.currentData()
        self._settings.sync()
        self.accept()
