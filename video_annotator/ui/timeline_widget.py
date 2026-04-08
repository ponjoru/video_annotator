"""TimelineWidget: custom QWidget rendering three stacked visual lanes.

Lanes (top to bottom):
  1. Ruler        — time labels and tick marks
  2. Segment band — colored rects per segment; trim handles; overlap indicators
  3. Waveform lane — audio amplitude curve; collapses when no audio
  4. Minimap strip — full-duration view; highlight rect shows visible window

Coordinate system:
  time_to_x(t) -> int   maps a time (seconds) to a pixel x within the widget
  x_to_time(x) -> float maps a pixel x to a time (seconds)

Zoom state: (visible_start, visible_end) — a sub-range of [0, duration].

Performance:
  - Waveform is rendered to a QPixmap once per zoom change; composited per-frame.
  - Minimap is rendered to a QPixmap once per zoom change.
  - Only the playhead line and segment hover state are repainted per frame.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from video_annotator.models.project import Segment
from video_annotator.utils.time_fmt import seconds_to_hms

# Lane heights in pixels
RULER_HEIGHT = 20
SEGMENT_BAND_HEIGHT = 40
WAVEFORM_LANE_HEIGHT = 60
MINIMAP_HEIGHT = 20

TRIM_HANDLE_PX = 5          # width of drag zone at each segment edge
SEGMENT_MARGIN_Y = 4        # vertical margin inside the segment band

# Colors
COLOR_RULER_BG = QColor("#2c2c2c")
COLOR_RULER_TEXT = QColor("#aaaaaa")
COLOR_SEGMENT_BAND_BG = QColor("#1e1e1e")
COLOR_WAVEFORM_BG = QColor("#161616")
COLOR_MINIMAP_BG = QColor("#333333")
COLOR_SEGMENT = QColor("#4A90D9")
COLOR_SEGMENT_OVERLAP = QColor("#E74C3C")
COLOR_SEGMENT_HANDLE = QColor(255, 255, 255, 80)
COLOR_PLAYHEAD = QColor("#F39C12")
COLOR_MINIMAP_WINDOW = QColor(255, 255, 255, 50)
COLOR_MINIMAP_SEGMENT = QColor("#4A90D9")
COLOR_WAVEFORM = QColor("#27AE60")
COLOR_IN_POINT = QColor("#E67E22")
COLOR_RUBBER_BAND = QColor(255, 255, 255, 30)
COLOR_RUBBER_BAND_BORDER = QColor(255, 255, 255, 120)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_overlaps(segments: list[Segment]) -> set[str]:
    """Return IDs of segments that overlap at least one other."""
    overlapping: set[str] = set()
    for i, a in enumerate(segments):
        for b in segments[i + 1:]:
            if a.start < b.end and b.start < a.end:
                overlapping.add(a.id)
                overlapping.add(b.id)
    return overlapping


def _pick_tick_interval(visible_span: float) -> tuple[float, float]:
    """Return (major_interval, minor_interval) for ruler ticks.

    Targets ~5-15 major ticks in the visible window.
    """
    candidates = [
        (0.5,  0.1),
        (1.0,  0.25),
        (2.0,  0.5),
        (5.0,  1.0),
        (10.0, 2.0),
        (15.0, 5.0),
        (30.0, 5.0),
        (60.0, 15.0),
        (120.0, 30.0),
        (300.0, 60.0),
        (600.0, 120.0),
        (1800.0, 300.0),
        (3600.0, 600.0),
    ]
    for major, minor in candidates:
        if visible_span / major <= 15:
            return major, minor
    return 3600.0, 600.0


def _fmt_tick(t: float) -> str:
    """Format a tick time as a compact label."""
    h = int(t) // 3600
    m = (int(t) % 3600) // 60
    s = t % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:05.2f}"
    if m > 0:
        return f"{m}:{s:05.2f}"
    # Sub-minute: show seconds with up to 1 decimal if needed
    if s == int(s):
        return f"{int(s)}s"
    return f"{s:.1f}s"


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

class TimelineWidget(QWidget):
    # --- Signals ---
    seek_requested = Signal(float)                       # user clicked on timeline
    segment_trim_requested = Signal(str, float, float)   # segment_id, new_start, new_end
    segment_selected = Signal(str)                       # segment_id
    in_point_drag_set = Signal(float)                    # rubber-band drag start
    out_point_drag_set = Signal(float)                   # rubber-band drag end

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(RULER_HEIGHT + SEGMENT_BAND_HEIGHT + WAVEFORM_LANE_HEIGHT + MINIMAP_HEIGHT)

        self._duration: float = 0.0
        self._position: float = 0.0
        self._visible_start: float = 0.0
        self._visible_end: float = 0.0
        self._fps: float = 30.0
        self._in_point: float | None = None

        self._segments: list[Segment] = []
        self._waveform: np.ndarray | None = None
        self._has_audio: bool = True

        self._waveform_pixmap: QPixmap | None = None
        self._minimap_pixmap: QPixmap | None = None

        # Drag state
        self._drag_type: str | None = None     # "seek", "rubber", "trim", "minimap"
        self._drag_seg_id: str | None = None   # for trim
        self._drag_trim_edge: str | None = None # "left" or "right"
        self._drag_trim_new: float = 0.0       # provisional new boundary
        self._drag_trim_orig_start: float = 0.0
        self._drag_trim_orig_end: float = 0.0
        self._rubber_start: float | None = None
        self._rubber_end: float | None = None

        self.setMouseTracking(True)

        font = QFont("Menlo")
        if not font.exactMatch():
            font.setFamily("Courier New")
        font.setPointSize(9)
        self._ruler_font = font

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_video(self, duration: float, fps: float, segments: list[Segment]) -> None:
        """Reset timeline for a new video."""
        self._duration = duration
        self._fps = fps
        self._visible_start = 0.0
        self._visible_end = duration
        self._segments = list(segments)
        self._waveform = None
        self._waveform_pixmap = None
        self._position = 0.0
        self._in_point = None
        self._invalidate_pixmaps()
        self.update()

    def set_position(self, position: float) -> None:
        """Update playhead without triggering a seek."""
        self._position = position
        self.update()

    def set_segments(self, segments: list[Segment]) -> None:
        self._segments = list(segments)
        self._invalidate_pixmaps()
        self.update()

    def set_waveform(self, array: np.ndarray) -> None:
        self._waveform = array
        self._has_audio = True
        self._waveform_pixmap = None
        self.update()

    def set_no_audio(self) -> None:
        self._has_audio = False
        self._waveform = None
        self.update()

    def set_in_point(self, position: float | None) -> None:
        """Show or clear the pending in-point marker dashed line."""
        self._in_point = position
        self.update()

    def set_duration(self, duration: float) -> None:
        """Update duration (e.g. after player reports real duration) without resetting segments."""
        self._duration = duration
        if self._visible_end == 0.0 or self._visible_end > duration:
            self._visible_end = duration
        self._invalidate_pixmaps()
        self.update()

    # ------------------------------------------------------------------
    # Zoom / pan
    # ------------------------------------------------------------------

    def zoom_in(self) -> None:
        self._zoom_around(self._position, factor=0.5)

    def zoom_out(self) -> None:
        self._zoom_around(self._position, factor=2.0)

    def zoom_fit(self) -> None:
        """Ctrl+0: reset to show the full video."""
        self._visible_start = 0.0
        self._visible_end = self._duration
        self._invalidate_pixmaps()
        self.update()

    def _zoom_around(self, anchor: float, factor: float) -> None:
        """Zoom the visible window by factor, keeping anchor at the same relative position."""
        if self._duration <= 0:
            return
        old_span = self._visible_end - self._visible_start
        if old_span <= 0:
            return

        new_span = old_span * factor

        # Limits: min = 1 s (or enough for at least 10 frames); max = full duration.
        min_span = max(1.0, 10.0 / max(self._fps, 1.0))
        new_span = max(min_span, min(self._duration, new_span))

        # Keep anchor at the same fractional position inside the window.
        ratio = (anchor - self._visible_start) / old_span
        new_start = anchor - ratio * new_span
        new_end = new_start + new_span

        # Clamp to [0, duration].
        if new_start < 0:
            new_start = 0.0
            new_end = new_span
        if new_end > self._duration:
            new_end = self._duration
            new_start = new_end - new_span
        new_start = max(0.0, new_start)

        self._visible_start = new_start
        self._visible_end = min(self._duration, new_end)
        self._invalidate_pixmaps()
        self.update()

    def _pan_to(self, center_time: float) -> None:
        """Pan visible window so center_time is centred (used by minimap)."""
        if self._duration <= 0:
            return
        half = (self._visible_end - self._visible_start) / 2.0
        new_start = center_time - half
        new_end = center_time + half
        if new_start < 0:
            new_start = 0.0
            new_end = 2 * half
        if new_end > self._duration:
            new_end = self._duration
            new_start = new_end - 2 * half
        self._visible_start = max(0.0, new_start)
        self._visible_end = min(self._duration, new_end)
        self._invalidate_pixmaps()
        self.update()

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def time_to_x(self, t: float) -> int:
        if self._visible_end == self._visible_start:
            return 0
        ratio = (t - self._visible_start) / (self._visible_end - self._visible_start)
        return int(ratio * self.width())

    def x_to_time(self, x: int) -> float:
        if self.width() == 0:
            return 0.0
        ratio = x / self.width()
        return self._visible_start + ratio * (self._visible_end - self._visible_start)

    def _minimap_time_to_x(self, t: float) -> int:
        if self._duration == 0:
            return 0
        return int(t / self._duration * self.width())

    def _minimap_x_to_time(self, x: int) -> float:
        if self.width() == 0:
            return 0.0
        return x / self.width() * self._duration

    # ------------------------------------------------------------------
    # Lane geometry
    # ------------------------------------------------------------------

    def _ruler_rect(self) -> QRect:
        return QRect(0, 0, self.width(), RULER_HEIGHT)

    def _segment_band_rect(self) -> QRect:
        return QRect(0, RULER_HEIGHT, self.width(), SEGMENT_BAND_HEIGHT)

    def _waveform_rect(self) -> QRect:
        if not self._has_audio:
            return QRect(0, RULER_HEIGHT + SEGMENT_BAND_HEIGHT, self.width(), 0)
        return QRect(0, RULER_HEIGHT + SEGMENT_BAND_HEIGHT, self.width(), WAVEFORM_LANE_HEIGHT)

    def _minimap_rect(self) -> QRect:
        y = RULER_HEIGHT + SEGMENT_BAND_HEIGHT + (WAVEFORM_LANE_HEIGHT if self._has_audio else 0)
        return QRect(0, y, self.width(), MINIMAP_HEIGHT)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        self._draw_ruler(painter)
        self._draw_segment_band(painter)
        if self._has_audio:
            self._draw_waveform(painter)
        self._draw_minimap(painter)
        self._draw_playhead(painter)
        self._draw_in_point(painter)
        self._draw_rubber_band(painter)

    def _draw_ruler(self, painter: QPainter) -> None:
        rect = self._ruler_rect()
        painter.fillRect(rect, COLOR_RULER_BG)

        if self._duration <= 0:
            return

        visible_span = self._visible_end - self._visible_start
        if visible_span <= 0:
            return

        major, minor = _pick_tick_interval(visible_span)
        painter.setFont(self._ruler_font)
        painter.setPen(QPen(COLOR_RULER_TEXT, 1))

        # Minor ticks
        t = (self._visible_start // minor) * minor
        while t <= self._visible_end:
            if t >= 0:
                x = self.time_to_x(t)
                painter.drawLine(x, RULER_HEIGHT - 5, x, RULER_HEIGHT - 1)
            t += minor

        # Major ticks + labels
        t = (self._visible_start // major) * major
        while t <= self._visible_end:
            if t >= 0:
                x = self.time_to_x(t)
                painter.drawLine(x, RULER_HEIGHT - 10, x, RULER_HEIGHT - 1)
                label = _fmt_tick(t)
                painter.drawText(x + 3, RULER_HEIGHT - 3, label)
            t += major

    def _draw_segment_band(self, painter: QPainter) -> None:
        band = self._segment_band_rect()
        painter.fillRect(band, COLOR_SEGMENT_BAND_BG)

        if not self._segments:
            return

        overlapping = _compute_overlaps(self._segments)
        seg_y = RULER_HEIGHT + SEGMENT_MARGIN_Y
        seg_h = SEGMENT_BAND_HEIGHT - 2 * SEGMENT_MARGIN_Y

        # During trim drag, substitute the dragged boundary.
        def effective_bounds(seg: Segment) -> tuple[float, float]:
            if self._drag_type == "trim" and self._drag_seg_id == seg.id:
                if self._drag_trim_edge == "left":
                    return self._drag_trim_new, seg.end
                else:
                    return seg.start, self._drag_trim_new
            return seg.start, seg.end

        for seg in self._segments:
            start, end = effective_bounds(seg)
            x1 = self.time_to_x(start)
            x2 = self.time_to_x(end)
            w = max(4, x2 - x1)

            seg_rect = QRect(x1, seg_y, w, seg_h)

            if seg.id in overlapping:
                painter.fillRect(seg_rect, COLOR_SEGMENT_OVERLAP)
                # Diagonal stripe overlay
                painter.save()
                painter.setClipRect(seg_rect)
                stripe_pen = QPen(QColor(0, 0, 0, 60), 2)
                painter.setPen(stripe_pen)
                step = 8
                for offset in range(-seg_h, w + seg_h, step):
                    painter.drawLine(x1 + offset, seg_y, x1 + offset + seg_h, seg_y + seg_h)
                painter.restore()
            else:
                painter.fillRect(seg_rect, COLOR_SEGMENT)

            # Trim handles (left edge)
            handle_rect_l = QRect(x1, seg_y, TRIM_HANDLE_PX, seg_h)
            painter.fillRect(handle_rect_l, COLOR_SEGMENT_HANDLE)

            # Trim handles (right edge)
            handle_rect_r = QRect(x1 + w - TRIM_HANDLE_PX, seg_y, TRIM_HANDLE_PX, seg_h)
            painter.fillRect(handle_rect_r, COLOR_SEGMENT_HANDLE)

    def _draw_waveform(self, painter: QPainter) -> None:
        rect = self._waveform_rect()
        if rect.height() <= 0:
            return

        # Lazy render pixmap on first paint or after zoom change.
        if self._waveform_pixmap is None:
            self._render_waveform_pixmap()

        if self._waveform_pixmap is not None:
            painter.drawPixmap(rect.topLeft(), self._waveform_pixmap)
        else:
            painter.fillRect(rect, COLOR_WAVEFORM_BG)
            # Hatched placeholder while extraction is in progress.
            pen = QPen(QColor("#333333"), 1)
            painter.setPen(pen)
            step = 12
            for i in range(0, rect.width() + rect.height(), step):
                painter.drawLine(
                    rect.x() + i, rect.y(),
                    rect.x() + max(0, i - rect.height()), rect.y() + rect.height(),
                )

    def _render_waveform_pixmap(self) -> None:
        if self._waveform is None or self.width() <= 0:
            return
        rect = self._waveform_rect()
        if rect.height() <= 0:
            return

        w = self.width()
        h = rect.height()
        visible_span = self._visible_end - self._visible_start
        if visible_span <= 0:
            return

        pixmap = QPixmap(w, h)
        pixmap.fill(COLOR_WAVEFORM_BG)
        p = QPainter(pixmap)
        p.setPen(QPen(COLOR_WAVEFORM, 1))

        sample_rate = 4000   # WaveformCache extracts at 4 kHz
        n_samples = len(self._waveform)
        total_dur = n_samples / sample_rate

        cy = h // 2
        half_h = max(1, cy - 2)

        for px in range(w):
            t0 = self._visible_start + px / w * visible_span
            t1 = t0 + visible_span / w
            i0 = int(t0 / total_dur * n_samples) if total_dur > 0 else 0
            i1 = int(t1 / total_dur * n_samples) if total_dur > 0 else 1
            i0 = max(0, min(i0, n_samples - 1))
            i1 = max(i0 + 1, min(i1, n_samples))
            if i0 >= n_samples:
                break
            amp = float(np.max(np.abs(self._waveform[i0:i1])))
            bar = int(amp * half_h)
            if bar > 0:
                p.drawLine(px, cy - bar, px, cy + bar)

        p.end()
        self._waveform_pixmap = pixmap

    def _draw_minimap(self, painter: QPainter) -> None:
        mm = self._minimap_rect()
        painter.fillRect(mm, COLOR_MINIMAP_BG)

        if self._duration <= 0:
            return

        # Segment overview
        for seg in self._segments:
            x1 = self._minimap_time_to_x(seg.start)
            x2 = self._minimap_time_to_x(seg.end)
            seg_rect = QRect(x1, mm.y() + 2, max(2, x2 - x1), mm.height() - 4)
            painter.fillRect(seg_rect, COLOR_MINIMAP_SEGMENT)

        # Visible window highlight
        x1 = self._minimap_time_to_x(self._visible_start)
        x2 = self._minimap_time_to_x(self._visible_end)
        window_rect = QRect(x1, mm.y(), max(2, x2 - x1), mm.height())
        painter.fillRect(window_rect, COLOR_MINIMAP_WINDOW)

        # Visible window border
        pen = QPen(QColor(255, 255, 255, 150), 1)
        painter.setPen(pen)
        painter.drawRect(window_rect.adjusted(0, 0, -1, -1))

    def _draw_playhead(self, painter: QPainter) -> None:
        if self._duration == 0.0:
            return
        x = self.time_to_x(self._position)
        pen = QPen(COLOR_PLAYHEAD, 2)
        painter.setPen(pen)
        painter.drawLine(x, 0, x, self.height())
        # Triangle handle at top
        painter.setBrush(QBrush(COLOR_PLAYHEAD))
        painter.setPen(Qt.PenStyle.NoPen)
        pts = [QPoint(x - 5, 0), QPoint(x + 5, 0), QPoint(x, 8)]
        from PySide6.QtGui import QPolygon
        painter.drawPolygon(QPolygon(pts))

    def _draw_in_point(self, painter: QPainter) -> None:
        if self._in_point is None or self._duration == 0.0:
            return
        x = self.time_to_x(self._in_point)
        pen = QPen(COLOR_IN_POINT, 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(x, 0, x, self.height())

    def _draw_rubber_band(self, painter: QPainter) -> None:
        if self._rubber_start is None or self._rubber_end is None:
            return
        x1 = self.time_to_x(min(self._rubber_start, self._rubber_end))
        x2 = self.time_to_x(max(self._rubber_start, self._rubber_end))
        if x2 <= x1:
            return
        band_y = RULER_HEIGHT + SEGMENT_MARGIN_Y
        band_h = SEGMENT_BAND_HEIGHT - 2 * SEGMENT_MARGIN_Y
        rect = QRect(x1, band_y, x2 - x1, band_h)
        painter.fillRect(rect, COLOR_RUBBER_BAND)
        pen = QPen(COLOR_RUBBER_BAND_BORDER, 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

    def _invalidate_pixmaps(self) -> None:
        self._waveform_pixmap = None
        self._minimap_pixmap = None

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._duration <= 0:
            return
        x = event.position().x()
        y = event.position().y()
        t = self.x_to_time(int(x))

        # --- Minimap ---
        if self._minimap_rect().contains(int(x), int(y)):
            self._drag_type = "minimap"
            self._pan_to(self._minimap_x_to_time(int(x)))
            return

        # --- Segment band ---
        band = self._segment_band_rect()
        if band.contains(int(x), int(y)):
            seg_y = RULER_HEIGHT + SEGMENT_MARGIN_Y
            seg_h = SEGMENT_BAND_HEIGHT - 2 * SEGMENT_MARGIN_Y

            # Check trim handles (priority over body click)
            for seg in self._segments:
                x1 = self.time_to_x(seg.start)
                x2 = self.time_to_x(seg.end)
                if abs(x - x1) <= TRIM_HANDLE_PX:
                    self._drag_type = "trim"
                    self._drag_seg_id = seg.id
                    self._drag_trim_edge = "left"
                    self._drag_trim_new = seg.start
                    self._drag_trim_orig_start = seg.start
                    self._drag_trim_orig_end = seg.end
                    return
                if abs(x - x2) <= TRIM_HANDLE_PX:
                    self._drag_type = "trim"
                    self._drag_seg_id = seg.id
                    self._drag_trim_edge = "right"
                    self._drag_trim_new = seg.end
                    self._drag_trim_orig_start = seg.start
                    self._drag_trim_orig_end = seg.end
                    return

            # Check segment body click
            for seg in self._segments:
                x1 = self.time_to_x(seg.start)
                x2 = self.time_to_x(seg.end)
                if x1 <= x <= x2:
                    self.segment_selected.emit(seg.id)
                    self.seek_requested.emit(seg.start)
                    return

            # Empty area → rubber-band drag (new segment by drag)
            self._drag_type = "rubber"
            self._rubber_start = t
            self._rubber_end = t
            return

        # --- Ruler or waveform → seek ---
        if (self._ruler_rect().contains(int(x), int(y))
                or self._waveform_rect().contains(int(x), int(y))):
            self._drag_type = "seek"
            self.seek_requested.emit(max(0.0, min(self._duration, t)))
            return

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._duration <= 0:
            return
        x = int(event.position().x())
        y = int(event.position().y())
        t = max(0.0, min(self._duration, self.x_to_time(x)))

        if self._drag_type == "minimap":
            self._pan_to(self._minimap_x_to_time(x))
            return

        if self._drag_type == "seek":
            self.seek_requested.emit(t)
            return

        if self._drag_type == "trim" and self._drag_seg_id is not None:
            if self._drag_trim_edge == "left":
                # Don't let left edge cross right edge
                self._drag_trim_new = min(t, self._drag_trim_orig_end - 0.1)
            else:
                self._drag_trim_new = max(t, self._drag_trim_orig_start + 0.1)
            self._drag_trim_new = max(0.0, min(self._duration, self._drag_trim_new))
            self.update()
            return

        if self._drag_type == "rubber":
            self._rubber_end = t
            self.update()
            return

        # No drag — update cursor shape
        self._update_cursor(x, y)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_type == "trim" and self._drag_seg_id is not None:
            if self._drag_trim_edge == "left":
                new_start = self._drag_trim_new
                new_end = self._drag_trim_orig_end
            else:
                new_start = self._drag_trim_orig_start
                new_end = self._drag_trim_new
            if new_end - new_start >= 0.1:
                self.segment_trim_requested.emit(self._drag_seg_id, new_start, new_end)

        elif self._drag_type == "rubber":
            if self._rubber_start is not None and self._rubber_end is not None:
                start = min(self._rubber_start, self._rubber_end)
                end = max(self._rubber_start, self._rubber_end)
                if end - start >= 0.1:
                    self.in_point_drag_set.emit(start)
                    self.out_point_drag_set.emit(end)

        self._drag_type = None
        self._drag_seg_id = None
        self._drag_trim_edge = None
        self._rubber_start = None
        self._rubber_end = None
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._duration <= 0:
            return
        x = int(event.position().x())
        t = self.x_to_time(x)
        delta = event.angleDelta().y()
        # One notch up → zoom in (factor < 1), one notch down → zoom out.
        factor = 0.8 if delta > 0 else 1.25
        self._zoom_around(t, factor)
        event.accept()

    def _update_cursor(self, x: int, y: int) -> None:
        """Set cursor shape based on what's under the pointer."""
        if not self._segment_band_rect().contains(x, y):
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        for seg in self._segments:
            x1 = self.time_to_x(seg.start)
            x2 = self.time_to_x(seg.end)
            if abs(x - x1) <= TRIM_HANDLE_PX or abs(x - x2) <= TRIM_HANDLE_PX:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                return
        self.setCursor(Qt.CursorShape.ArrowCursor)
