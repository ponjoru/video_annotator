"""TransportBar: timestamp display, speed buttons, play/pause, volume."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QWidget,
)

from video_annotator.ui.player_panel import SPEED_LEVELS
from video_annotator.utils.time_fmt import seconds_to_hms

_SPEED_LABELS = {1.0: "1×", 1.5: "1.5×", 2.0: "2×", 4.0: "4×", 8.0: "8×"}


class TransportBar(QWidget):
    # --- Signals (user intent; PlaybackController consumes these) ---
    play_pause_clicked = Signal()
    speed_selected = Signal(float)      # one of SPEED_LEVELS
    volume_changed = Signal(int)        # 0–100

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._duration: float = 0.0
        self._speed_buttons: dict[float, QPushButton] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(6)

        # Play / Pause button
        self._play_btn = QPushButton("▶")
        self._play_btn.setFixedWidth(36)
        self._play_btn.setToolTip("Play / Pause  (Space)")
        self._play_btn.clicked.connect(self.play_pause_clicked)
        layout.addWidget(self._play_btn)

        layout.addSpacing(4)

        # Timestamp label — monospaced so the digits don't jump around
        self._timestamp = QLabel("00:00:00.000 / 00:00:00.000")
        mono = QFont("Menlo")
        if not mono.exactMatch():
            mono = QFont("Courier New")
        mono.setPointSize(11)
        self._timestamp.setFont(mono)
        self._timestamp.setMinimumWidth(220)
        layout.addWidget(self._timestamp)

        # Stretch separates timestamp from speed buttons
        layout.addStretch()

        # Speed button group — checkable, mutually exclusive managed manually
        for speed in SPEED_LEVELS:
            btn = QPushButton(_SPEED_LABELS[speed])
            btn.setCheckable(True)
            btn.setFixedWidth(42)
            btn.setToolTip(f"Set speed to {speed}×")
            btn.clicked.connect(lambda _checked, s=speed: self.speed_selected.emit(s))
            self._speed_buttons[speed] = btn
            layout.addWidget(btn)
        self._speed_buttons[1.0].setChecked(True)

        layout.addSpacing(8)

        # Volume slider
        vol_label = QLabel("Vol")
        vol_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(vol_label)

        self._volume = QSlider(Qt.Orientation.Horizontal)
        self._volume.setRange(0, 100)
        self._volume.setValue(100)
        self._volume.setFixedWidth(90)
        self._volume.setToolTip("Volume")
        self._volume.valueChanged.connect(self.volume_changed)
        layout.addWidget(self._volume)

    # ------------------------------------------------------------------
    # Slots (called by PlayerPanel signals)
    # ------------------------------------------------------------------

    def on_position_changed(self, position: float) -> None:
        self._timestamp.setText(
            f"{seconds_to_hms(position)} / {seconds_to_hms(self._duration)}"
        )

    def on_duration_known(self, duration: float) -> None:
        self._duration = duration
        self._timestamp.setText(
            f"{seconds_to_hms(0.0)} / {seconds_to_hms(duration)}"
        )

    def on_speed_changed(self, speed: float) -> None:
        """Highlight the active speed button; uncheck all others."""
        for s, btn in self._speed_buttons.items():
            btn.setChecked(s == speed)

    def set_playing(self, playing: bool) -> None:
        self._play_btn.setText("⏸" if playing else "▶")
