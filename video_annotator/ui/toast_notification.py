"""ToastNotification: transient message overlay inside a parent widget.

Appears at the horizontal centre, 16 px above the bottom of its parent.
Auto-hides after DISPLAY_MS milliseconds.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel, QWidget


DISPLAY_MS = 3000


class ToastNotification(QLabel):
    """Floating label that auto-dismisses after DISPLAY_MS ms."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "background: rgba(30,30,30,210);"
            "color: #F1C40F;"
            "border-radius: 6px;"
            "padding: 6px 14px;"
            "font-size: 13px;"
        )
        self.setWordWrap(False)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DISPLAY_MS)
        self._timer.timeout.connect(self.hide)

        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_message(self, message: str) -> None:
        """Display *message* and restart the auto-hide timer."""
        self.setText(message)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._timer.start()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        x = max(0, (parent.width() - self.width()) // 2)
        y = max(0, parent.height() - self.height() - 16)
        self.move(x, y)

    # Keep position correct if the parent is resized while the toast is visible.
    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._reposition()
