"""SidePanel: right column of the main window.

Layout (top to bottom):
  - Video status buttons: Done (D) / Skipped (S)
  - TagEditor for video-level tags
  - SegmentList (scrollable)
  - Export button (Ctrl+E)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from video_annotator.ui.segment_list import SegmentList
from video_annotator.ui.tag_editor import TagEditor

if TYPE_CHECKING:
    from video_annotator.models.project import Segment, Video


class SidePanel(QWidget):
    # --- Signals (forwarded to MainWindow / controllers) ---
    status_done_clicked = Signal()
    status_skipped_clicked = Signal()

    video_tag_added = Signal(str)           # tag text; MainWindow supplies video_id
    video_tag_removed = Signal(str)

    segment_seek_requested = Signal(float)
    segment_delete_requested = Signal(str)  # segment_id
    segment_tag_added = Signal(str, str)    # segment_id, tag
    segment_tag_removed = Signal(str, str)
    segment_notes_changed = Signal(str, str)

    export_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self.set_export_enabled(False)
        self.set_video_controls_enabled(False)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # Status buttons row
        status_row = QHBoxLayout()
        self._done_btn = QPushButton("Done (D)")
        self._done_btn.setCheckable(True)
        self._skipped_btn = QPushButton("Skipped (S)")
        self._skipped_btn.setCheckable(True)
        self._done_btn.clicked.connect(self.status_done_clicked)
        self._skipped_btn.clicked.connect(self.status_skipped_clicked)
        # Checked style: green for done, amber for skipped
        self._done_btn.setStyleSheet(
            "QPushButton:checked { background: #27AE60; color: #fff; font-weight: bold; }"
        )
        self._skipped_btn.setStyleSheet(
            "QPushButton:checked { background: #F39C12; color: #fff; font-weight: bold; }"
        )
        status_row.addWidget(self._done_btn)
        status_row.addWidget(self._skipped_btn)
        layout.addLayout(status_row)

        # Video-level tag editor
        self._video_tag_editor = TagEditor()
        self._video_tag_editor.tag_added.connect(self.video_tag_added)
        self._video_tag_editor.tag_removed.connect(self.video_tag_removed)
        layout.addWidget(self._video_tag_editor)

        # Segment list
        self._segment_list = SegmentList()
        self._segment_list.seek_requested.connect(self.segment_seek_requested)
        self._segment_list.delete_requested.connect(self.segment_delete_requested)
        self._segment_list.tag_added.connect(self.segment_tag_added)
        self._segment_list.tag_removed.connect(self.segment_tag_removed)
        self._segment_list.notes_changed.connect(self.segment_notes_changed)
        layout.addWidget(self._segment_list, stretch=1)

        # Export button
        self._export_btn = QPushButton("Export All Segments  (Ctrl+E)")
        self._export_btn.clicked.connect(self.export_clicked)
        layout.addWidget(self._export_btn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_video(self, video: "Video", vocabulary: list[str]) -> None:
        """Populate the panel for the given video."""
        self.update_video_status(video.status)
        self._video_tag_editor.set_tags(video.tags)
        self._video_tag_editor.set_vocabulary(vocabulary)
        self._segment_list.set_vocabulary(vocabulary)
        self._segment_list.set_segments(video.segments)
        self.set_video_controls_enabled(True)

    def update_segments(self, segments: list["Segment"]) -> None:
        self._segment_list.set_segments(segments)

    def update_video_status(self, status: str) -> None:
        """Update visual state of Done / Skipped buttons."""
        self._done_btn.setChecked(status == "done")
        self._skipped_btn.setChecked(status == "skipped")

    def update_vocabulary(self, vocabulary: list[str]) -> None:
        self._video_tag_editor.set_vocabulary(vocabulary)
        self._segment_list.set_vocabulary(vocabulary)

    def set_export_enabled(self, enabled: bool) -> None:
        self._export_btn.setEnabled(enabled)

    def set_video_controls_enabled(self, enabled: bool) -> None:
        """Disable all controls when no video is loaded."""
        self._done_btn.setEnabled(enabled)
        self._skipped_btn.setEnabled(enabled)
        self._video_tag_editor.setEnabled(enabled)
        self._segment_list.setEnabled(enabled)

    def focus_tag_input(self) -> None:
        """Called by T shortcut to focus the video-level tag field."""
        self._video_tag_editor.focus_input()

    def scroll_to_segment(self, segment_id: str) -> None:
        self._segment_list.scroll_to_segment(segment_id)
