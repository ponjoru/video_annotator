"""StatusBar: persistent strip at the bottom of MainWindow.

States:
  idle    — shows session summary stats
  export  — shows per-clip progress bar + cancel button
  done    — shows completion message for 5 s, then returns to idle
  error   — shows error summary (N failures)
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QWidget,
)


class StatusBar(QWidget):
    # --- Signals ---
    cancel_export_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.setInterval(5000)
        self._clear_timer.timeout.connect(self._return_to_idle)
        self._idle_text: str = "No folder open"

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)

        self._label = QLabel("No folder open")
        layout.addWidget(self._label, stretch=1)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setFixedWidth(200)
        layout.addWidget(self._progress_bar)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setVisible(False)
        self._cancel_btn.clicked.connect(self.cancel_export_clicked)
        layout.addWidget(self._cancel_btn)

    # ------------------------------------------------------------------
    # Idle state
    # ------------------------------------------------------------------

    def set_idle(self, filename: str, done: int, skipped: int, segments: int, total: int) -> None:
        """Update the idle status text."""
        self._idle_text = (
            f'"{filename}"  —  {done} done, {skipped} skipped, {segments} segments  [{done}/{total}]'
        )
        if self._progress_bar.isHidden():
            self._label.setText(self._idle_text)

    # ------------------------------------------------------------------
    # Export state
    # ------------------------------------------------------------------

    def on_export_started(self, total_clips: int) -> None:
        self._progress_bar.setMaximum(total_clips)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._cancel_btn.setVisible(True)
        self._label.setText(f"Starting export of {total_clips} clip(s)…")

    def on_clip_started(self, index: int, total: int, clip_name: str) -> None:
        self._label.setText(f"Exporting clip {index} / {total} — {clip_name}")
        self._progress_bar.setValue(index - 1)

    def on_clip_done(self, index: int) -> None:
        self._progress_bar.setValue(index)

    def on_export_complete(self, clips_written: int, output_folder: str) -> None:
        self._progress_bar.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._label.setText(
            f"Export complete — {clips_written} clip(s) written to {output_folder}"
        )
        self._idle_text = self._label.text()
        self._clear_timer.start()

    def on_export_cancelled(self) -> None:
        self._progress_bar.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._label.setText("Export cancelled.")
        self._clear_timer.start()

    def on_export_error(self, failure_count: int, log_path: str) -> None:
        self._progress_bar.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._label.setText(
            f"Export completed with {failure_count} error(s) — see {log_path}"
        )
        self._clear_timer.start()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _return_to_idle(self) -> None:
        self._progress_bar.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._label.setText(self._idle_text)
