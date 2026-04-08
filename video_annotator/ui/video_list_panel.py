"""VideoListPanel: left column showing all videos in the opened folder.

Each item: status dot (colored) | filename | VFR badge | segment count
Footer: progress bar (done+skipped / total) + optional "Relink missing…" button.

Item rendering uses a QStyledItemDelegate so the status color is painted as a
solid circle rather than being embedded in the text label.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from video_annotator.models.project import Video

# Status → badge color
STATUS_COLORS: dict[str, QColor] = {
    "unseen":      QColor("#888888"),
    "in_progress": QColor("#2980B9"),
    "done":        QColor("#27AE60"),
    "skipped":     QColor("#F39C12"),
    "missing":     QColor("#E74C3C"),
}

# Custom data roles stored on QListWidgetItem
_ROLE_VIDEO_ID = Qt.ItemDataRole.UserRole          # str
_ROLE_STATUS   = Qt.ItemDataRole.UserRole + 1      # str
_ROLE_IS_VFR   = Qt.ItemDataRole.UserRole + 2      # bool
_ROLE_SEG_CNT  = Qt.ItemDataRole.UserRole + 3      # int

_DOT_DIAMETER = 10
_DOT_MARGIN   = 6    # space between dot edge and text


class _VideoItemDelegate(QStyledItemDelegate):
    """Paints a colored status dot to the left of the filename text."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # type: ignore[override]
        # Let the base class draw selection highlight and background only —
        # clear the display text so it doesn't render the filename a second time.
        from PySide6.QtWidgets import QStyleOptionViewItem as _Opt
        opt = _Opt(option)
        opt.text = ""
        painter.fillRect(option.rect, option.palette.base())
        # super().paint(painter, opt, index)

        status = index.data(_ROLE_STATUS) or "unseen"
        is_vfr = index.data(_ROLE_IS_VFR) or False
        seg_cnt = index.data(_ROLE_SEG_CNT) or 0
        display = index.data(Qt.ItemDataRole.DisplayRole) or ""

        r = option.rect  # type: ignore[attr-defined]

        # --- Status dot ---
        dot_x = r.left() + _DOT_MARGIN
        dot_y = r.top() + (r.height() - _DOT_DIAMETER) // 2
        dot_rect = QRect(dot_x, dot_y, _DOT_DIAMETER, _DOT_DIAMETER)
        color = STATUS_COLORS.get(status, STATUS_COLORS["unseen"])

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(dot_rect)
        painter.restore()

        # --- Filename text ---
        text_x = dot_x + _DOT_DIAMETER + _DOT_MARGIN
        text_rect = QRect(text_x, r.top(), r.right() - text_x, r.height())

        suffix_parts: list[str] = []
        if is_vfr:
            suffix_parts.append("[VFR]")
        if seg_cnt:
            suffix_parts.append(f"({seg_cnt})")
        label = display
        if suffix_parts:
            label = f"{display}  {'  '.join(suffix_parts)}"

        painter.save()
        painter.setPen(QPen(option.palette.text().color()))  # type: ignore[attr-defined]
        # Elide text so it never escapes text_rect — prevents overflow into adjacent rows.
        fm = painter.fontMetrics()
        elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, text_rect.width())
        painter.setClipRect(text_rect)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # type: ignore[override]
        base = super().sizeHint(option, index)
        return QSize(base.width(), max(base.height(), 26))


class VideoListPanel(QWidget):
    # --- Signals ---
    video_selected = Signal(str)       # video_id
    relink_requested = Signal()        # user clicked "Relink missing files…"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._videos: list["Video"] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._list = QListWidget()
        self._list.setItemDelegate(_VideoItemDelegate(self._list))
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._list)

        footer = QHBoxLayout()
        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%v / %m videos done")
        footer.addWidget(self._progress_bar)

        self._relink_btn = QPushButton("Relink missing files…")
        self._relink_btn.setVisible(False)
        self._relink_btn.clicked.connect(self.relink_requested)
        footer.addWidget(self._relink_btn)

        layout.addLayout(footer)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh(self, videos: list["Video"]) -> None:
        """Rebuild the list from an updated video list."""
        self._videos = videos
        self._list.clear()

        done_count = 0
        has_missing = False

        for video in videos:
            item = QListWidgetItem(video.filename)
            item.setData(_ROLE_VIDEO_ID, video.id)
            item.setData(_ROLE_STATUS, video.status)
            item.setData(_ROLE_IS_VFR, video.is_vfr)
            item.setData(_ROLE_SEG_CNT, len(video.segments))

            if video.status == "missing":
                item.setToolTip(f"File not found: {video.abs_path}")
                has_missing = True
            elif video.is_vfr:
                item.setToolTip("Variable frame rate source — export may have minor drift")

            if video.status in ("done", "skipped"):
                done_count += 1

            self._list.addItem(item)

        self._progress_bar.setMaximum(len(videos))
        self._progress_bar.setValue(done_count)
        self._relink_btn.setVisible(has_missing)

    def select_video_id(self, video_id: str) -> None:
        """Programmatically select a row by video_id."""
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(_ROLE_VIDEO_ID) == video_id:
                self._list.setCurrentItem(item)
                return

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        video_id = item.data(_ROLE_VIDEO_ID)
        if video_id:
            self.video_selected.emit(video_id)
