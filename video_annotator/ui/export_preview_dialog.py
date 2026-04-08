"""ExportPreviewDialog: shown before any files are written.

Displays a summary of what will be exported and lets the user:
  - Change the output folder
  - Switch export mode (re-encode / lossless re-mux)
  - See warnings (VFR sources, overlapping segments, overwrites)
  - Confirm or cancel
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from video_annotator.utils.time_fmt import seconds_to_hms

if TYPE_CHECKING:
    from video_annotator.controllers.export_engine import ExportSpec
    from video_annotator.models.project import Project
    from video_annotator.settings import SettingsManager


def _compute_overlaps_any(project: "Project") -> int:
    """Return the count of segment pairs that overlap across the whole project."""
    count = 0
    for video in project.videos:
        segs = video.segments
        for i, a in enumerate(segs):
            for b in segs[i + 1:]:
                if a.start < b.end and b.start < a.end:
                    count += 1
    return count


class ExportPreviewDialog(QDialog):
    # Emitted when user clicks Export; carries the finalized spec
    export_confirmed = Signal(object)   # ExportSpec

    def __init__(
        self,
        project: "Project",
        settings: "SettingsManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Preview")
        self.setMinimumWidth(540)
        self._project = project
        self._settings = settings
        self._output_folder = settings.last_output_folder
        self._export_mode = settings.default_export_mode
        self._build_ui()
        self._populate_summary()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Output folder row
        folder_row = QHBoxLayout()
        self._folder_label = QLabel(self._output_folder or "(not set)")
        self._folder_label.setWordWrap(False)
        folder_row.addWidget(QLabel("Output folder:"))
        folder_row.addWidget(self._folder_label, stretch=1)
        change_btn = QPushButton("Change…")
        change_btn.clicked.connect(self._choose_output_folder)
        folder_row.addWidget(change_btn)
        layout.addLayout(folder_row)

        # Summary table (form layout)
        self._summary_form = QFormLayout()
        layout.addLayout(self._summary_form)

        # Warnings area
        self._warnings_label = QLabel()
        self._warnings_label.setWordWrap(True)
        self._warnings_label.setStyleSheet("color: #E74C3C;")
        layout.addWidget(self._warnings_label)

        # Export mode
        mode_row = QHBoxLayout()
        self._reencode_rb = QRadioButton("Re-encode (h264, frame-accurate)")
        self._remux_rb = QRadioButton("Lossless re-mux (faster, keyframe boundaries)")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._reencode_rb, 0)
        self._mode_group.addButton(self._remux_rb, 1)
        if self._export_mode == "reencode":
            self._reencode_rb.setChecked(True)
        else:
            self._remux_rb.setChecked(True)
        self._mode_group.buttonToggled.connect(self._on_mode_changed)
        mode_row.addWidget(self._reencode_rb)
        mode_row.addWidget(self._remux_rb)
        layout.addLayout(mode_row)

        # Dialog buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._export_btn = QPushButton("Export")
        self._export_btn.setDefault(True)
        buttons.addButton(self._export_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)
        self._export_btn.clicked.connect(self._on_export_clicked)
        layout.addWidget(buttons)

    def _populate_summary(self) -> None:
        """Fill in the summary table with stats from the current project."""
        # Clear previous rows.
        while self._summary_form.rowCount():
            self._summary_form.removeRow(0)

        project = self._project

        videos_with_segs = [v for v in project.videos if v.segments]
        total_clips = sum(len(v.segments) for v in videos_with_segs)
        total_dur = sum(
            seg.duration
            for v in videos_with_segs
            for seg in v.segments
        )

        # Estimated size: rough heuristic — 4 Mbit/s for reencode, 8 Mbit/s for remux.
        bitrate_mbps = 4.0 if self._export_mode == "reencode" else 8.0
        est_mb = (total_dur * bitrate_mbps * 1_000_000 / 8) / 1_048_576

        self._summary_form.addRow("Source videos with segments:", QLabel(str(len(videos_with_segs))))
        self._summary_form.addRow("Total clips:", QLabel(str(total_clips)))
        self._summary_form.addRow("Total output duration:", QLabel(seconds_to_hms(total_dur)))
        self._summary_form.addRow("Estimated size:", QLabel(f"~{est_mb:.0f} MB"))

        # --- Warnings ---
        warnings: list[str] = []

        vfr_names = [v.filename for v in videos_with_segs if v.is_vfr]
        if vfr_names:
            warnings.append(
                "VFR source(s): " + ", ".join(vfr_names)
                + " — re-encode output may have minor timestamp drift."
            )

        overlap_count = _compute_overlaps_any(project)
        if overlap_count:
            warnings.append(
                f"{overlap_count} overlapping segment pair(s) detected — "
                "clips will be exported separately but may contain duplicate frames."
            )

        # Potential overwrites: check if any output path already exists.
        if self._output_folder:
            try:
                from video_annotator.controllers.export_engine import ExportSpec
                # Build a preview spec just to enumerate output paths.
                spec = ExportSpec.build(
                    project, self._settings, self._output_folder, self._export_mode
                )
                overwrites = [c for c in spec.clips if os.path.exists(c.output_path)]
                if overwrites:
                    warnings.append(
                        f"{len(overwrites)} output file(s) already exist and will be overwritten."
                    )
            except Exception:
                pass

        self._warnings_label.setText("\n".join(warnings))

        # Enable Export only when output folder is set and writable.
        folder_ok = bool(self._output_folder) and self._is_folder_writable(self._output_folder)
        has_clips = total_clips > 0
        self._export_btn.setEnabled(folder_ok and has_clips)
        if not self._output_folder:
            self._export_btn.setToolTip("Select an output folder first.")
        elif not folder_ok:
            self._export_btn.setToolTip("Output folder is not writable.")
        elif not has_clips:
            self._export_btn.setToolTip("No segments to export.")
        else:
            self._export_btn.setToolTip("")

    def _choose_output_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select Output Folder", self._output_folder
        )
        if path:
            self._output_folder = path
            self._folder_label.setText(path)
            self._settings.last_output_folder = path
            self._populate_summary()

    def _on_mode_changed(self) -> None:
        self._export_mode = "reencode" if self._reencode_rb.isChecked() else "remux"
        self._populate_summary()

    def _on_export_clicked(self) -> None:
        from video_annotator.controllers.export_engine import ExportSpec
        spec = ExportSpec.build(
            self._project,
            self._settings,
            self._output_folder,
            self._export_mode,
        )
        self.export_confirmed.emit(spec)
        self.accept()

    @staticmethod
    def _is_folder_writable(path: str) -> bool:
        """Return True if path exists and is writable, or can be created."""
        if os.path.exists(path):
            return os.access(path, os.W_OK)
        # Try the first existing ancestor.
        parent = os.path.dirname(path)
        while parent and parent != path:
            if os.path.exists(parent):
                return os.access(parent, os.W_OK)
            path = parent
            parent = os.path.dirname(path)
        return False
