"""RestoreSessionDialog: shown when a .videoann_session.json is found on folder open."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget


class RestoreSessionDialog(QDialog):
    """Asks the user whether to restore a previous session or start fresh."""

    def __init__(
        self,
        video_count: int,
        done_count: int,
        segment_count: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Previous Session Found")
        self._build_ui(video_count, done_count, segment_count)

    def _build_ui(self, video_count: int, done_count: int, segment_count: int) -> None:
        layout = QVBoxLayout(self)

        summary = (
            f"A previous session was found for this folder:\n\n"
            f"  • {video_count} videos  ({done_count} done)\n"
            f"  • {segment_count} segments marked\n\n"
            f"Resume it or start fresh?"
        )
        layout.addWidget(QLabel(summary))

        buttons = QDialogButtonBox()
        self._restore_btn = buttons.addButton("Resume Session", QDialogButtonBox.ButtonRole.AcceptRole)
        self._fresh_btn = buttons.addButton("Start Fresh", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def should_restore(self) -> bool:
        """True if the user chose to resume the session."""
        return self.result() == QDialog.DialogCode.Accepted
