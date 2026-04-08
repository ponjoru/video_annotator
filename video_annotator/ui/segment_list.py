"""SegmentList: scrollable list of segments for the current video.

Each row:  [start → end]  duration  [tag chips]  [notes field]  [delete btn]

Overlap highlighting: rows whose segment overlaps another are styled amber.
Clicking a row seeks the player to the segment's start time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from video_annotator.ui.tag_editor import TagEditor
from video_annotator.utils.time_fmt import seconds_to_hms

if TYPE_CHECKING:
    from video_annotator.models.project import Segment


def _compute_overlaps(segments: list["Segment"]) -> set[str]:
    """Return the set of segment IDs that overlap at least one other segment."""
    overlapping: set[str] = set()
    for i, a in enumerate(segments):
        for b in segments[i + 1:]:
            if a.start < b.end and b.start < a.end:
                overlapping.add(a.id)
                overlapping.add(b.id)
    return overlapping


class SegmentRow(QWidget):
    """Single row representing one segment."""

    seek_requested = Signal(float)        # seek to segment start
    delete_requested = Signal(str)        # segment_id
    tag_added = Signal(str, str)          # segment_id, tag
    tag_removed = Signal(str, str)        # segment_id, tag
    notes_changed = Signal(str, str)      # segment_id, notes text

    def __init__(
        self,
        segment: "Segment",
        overlaps: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._segment_id = segment.id
        self._tag_editor: TagEditor
        self._build_ui(segment, overlaps)

    def set_vocabulary(self, vocabulary: list[str]) -> None:
        self._tag_editor.set_vocabulary(vocabulary)

    def _build_ui(self, segment: "Segment", overlaps: bool) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        # Timestamp + duration
        time_label = QLabel(
            f"{seconds_to_hms(segment.start)} → {seconds_to_hms(segment.end)}"
        )
        time_label.setStyleSheet("font-family: monospace; font-size: 11px;")
        time_label.setFixedWidth(200)
        time_label.setCursor(Qt.CursorShape.PointingHandCursor)
        time_label.mousePressEvent = lambda _e: self.seek_requested.emit(segment.start)  # type: ignore[assignment]
        layout.addWidget(time_label)

        dur_label = QLabel(f"{segment.duration:.1f}s")
        dur_label.setFixedWidth(50)
        dur_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(dur_label)

        # Tag editor (inline, compact)
        self._tag_editor = TagEditor()
        self._tag_editor.set_tags(segment.tags)
        self._tag_editor.tag_added.connect(
            lambda tag: self.tag_added.emit(self._segment_id, tag)
        )
        self._tag_editor.tag_removed.connect(
            lambda tag: self.tag_removed.emit(self._segment_id, tag)
        )
        layout.addWidget(self._tag_editor, stretch=1)

        # Notes field (single line, collapsed look)
        self._notes = QLineEdit(segment.notes)
        self._notes.setPlaceholderText("Notes…")
        self._notes.setFixedWidth(120)
        self._notes.editingFinished.connect(
            lambda: self.notes_changed.emit(self._segment_id, self._notes.text())
        )
        layout.addWidget(self._notes)

        # Delete button
        del_btn = QPushButton("✕")
        del_btn.setFixedSize(22, 22)
        del_btn.setFlat(True)
        del_btn.setToolTip("Delete segment")
        del_btn.setStyleSheet("color: #888; font-size: 12px;")
        del_btn.clicked.connect(lambda: self.delete_requested.emit(self._segment_id))
        layout.addWidget(del_btn)

        self.set_overlap_style(overlaps)

    def set_overlap_style(self, overlaps: bool) -> None:
        if overlaps:
            self.setStyleSheet("SegmentRow { background-color: #4a3a00; border-radius: 3px; }")
        else:
            self.setStyleSheet("")


class SegmentList(QWidget):
    """Container managing all SegmentRow widgets for the current video."""

    seek_requested = Signal(float)
    delete_requested = Signal(str)
    tag_added = Signal(str, str)
    tag_removed = Signal(str, str)
    notes_changed = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: dict[str, SegmentRow] = {}
        self._vocabulary: list[str] = []
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._inner = QWidget()
        self._inner_layout = QVBoxLayout(self._inner)
        self._inner_layout.setSpacing(2)
        self._inner_layout.addStretch()
        self._scroll.setWidget(self._inner)
        outer.addWidget(self._scroll)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_segments(self, segments: list["Segment"]) -> None:
        """Rebuild the row list from a new segment list."""
        # Remove existing rows (all items except the trailing stretch).
        for row in self._rows.values():
            self._inner_layout.removeWidget(row)
            row.deleteLater()
        self._rows = {}

        overlapping = _compute_overlaps(segments)

        for seg in sorted(segments, key=lambda s: s.start):
            row = SegmentRow(seg, overlaps=seg.id in overlapping)
            if self._vocabulary:
                row.set_vocabulary(self._vocabulary)
            row.seek_requested.connect(self.seek_requested)
            row.delete_requested.connect(self.delete_requested)
            row.tag_added.connect(self.tag_added)
            row.tag_removed.connect(self.tag_removed)
            row.notes_changed.connect(self.notes_changed)
            # Insert before the trailing stretch (last item).
            self._inner_layout.insertWidget(self._inner_layout.count() - 1, row)
            self._rows[seg.id] = row

    def set_vocabulary(self, vocabulary: list[str]) -> None:
        """Push autocomplete vocabulary to all existing segment rows."""
        self._vocabulary = vocabulary
        for row in self._rows.values():
            row.set_vocabulary(vocabulary)

    def scroll_to_segment(self, segment_id: str) -> None:
        row = self._rows.get(segment_id)
        if row:
            self._scroll.ensureWidgetVisible(row)
