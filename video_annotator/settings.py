"""Application preferences, persisted via QSettings.

Storage location: ~/Library/Preferences/com.videoannotator.app.plist (macOS).
videotoolbox_available is detected at runtime and NOT persisted.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings


_ORG = "VideoAnnotator"
_APP = "VideoAnnotator"


class SettingsManager:
    """Typed wrapper around QSettings with documented defaults."""

    def __init__(self) -> None:
        self._qs = QSettings(_ORG, _APP)
        # Runtime-only; detected once in app.py at startup
        self.videotoolbox_available: bool = False

    # --- Segment validation ---

    @property
    def min_segment_duration(self) -> float:
        return float(self._qs.value("min_segment_duration", 0.5))

    @min_segment_duration.setter
    def min_segment_duration(self, value: float) -> None:
        self._qs.setValue("min_segment_duration", value)

    @property
    def max_segment_duration_warning(self) -> float:
        return float(self._qs.value("max_segment_duration_warning", 300.0))

    @max_segment_duration_warning.setter
    def max_segment_duration_warning(self, value: float) -> None:
        self._qs.setValue("max_segment_duration_warning", value)

    # --- Export ---

    @property
    def default_export_mode(self) -> str:
        """'reencode' | 'remux'"""
        return str(self._qs.value("default_export_mode", "reencode"))

    @default_export_mode.setter
    def default_export_mode(self, value: str) -> None:
        assert value in ("reencode", "remux")
        self._qs.setValue("default_export_mode", value)

    @property
    def reencode_crf(self) -> int:
        return int(self._qs.value("reencode_crf", 18))

    @reencode_crf.setter
    def reencode_crf(self, value: int) -> None:
        self._qs.setValue("reencode_crf", value)

    @property
    def reencode_preset(self) -> str:
        return str(self._qs.value("reencode_preset", "fast"))

    @reencode_preset.setter
    def reencode_preset(self, value: str) -> None:
        self._qs.setValue("reencode_preset", value)

    @property
    def use_videotoolbox(self) -> str:
        """'auto' | 'on' | 'off'"""
        return str(self._qs.value("use_videotoolbox", "auto"))

    @use_videotoolbox.setter
    def use_videotoolbox(self, value: str) -> None:
        assert value in ("auto", "on", "off")
        self._qs.setValue("use_videotoolbox", value)

    # --- Paths ---

    @property
    def last_output_folder(self) -> str:
        return str(self._qs.value("last_output_folder", ""))

    @last_output_folder.setter
    def last_output_folder(self, value: str) -> None:
        self._qs.setValue("last_output_folder", value)

    @property
    def last_source_folder(self) -> str:
        return str(self._qs.value("last_source_folder", ""))

    @last_source_folder.setter
    def last_source_folder(self, value: str) -> None:
        self._qs.setValue("last_source_folder", value)

    def sync(self) -> None:
        """Force flush to disk (normally QSettings does this automatically)."""
        self._qs.sync()
