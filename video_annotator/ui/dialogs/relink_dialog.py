"""RelinkDialog: lets the user re-map missing video files to their new locations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from video_annotator.models.project import Video


class _RelinkRow(QWidget):
    """One row: original filename | resolved path (or 'Missing') | Browse button."""

    def __init__(self, video: "Video", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._video_id = video.id
        self._original_filename = video.filename
        self._new_path: str | None = None
        self._build_ui(video)

    def _build_ui(self, video: "Video") -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel(video.filename))
        self._path_label = QLabel("Missing")
        self._path_label.setStyleSheet("color: #E74C3C;")
        layout.addWidget(self._path_label, stretch=1)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        layout.addWidget(browse_btn)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Locate: {self._original_filename}",
            "",
            "Video Files (*)",
        )
        if path:
            self._new_path = path
            self._path_label.setText(path)
            self._path_label.setStyleSheet("color: #27AE60;")

    @property
    def video_id(self) -> str:
        return self._video_id

    @property
    def new_path(self) -> str | None:
        """None if the user did not pick a file for this row."""
        return self._new_path


class RelinkDialog(QDialog):
    """Shows all missing videos and collects new paths from the user."""

    def __init__(self, missing_videos: list["Video"], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Relink Missing Files")
        self.setMinimumWidth(600)
        self._rows: list[_RelinkRow] = []
        self._build_ui(missing_videos)

    def _build_ui(self, missing_videos: list["Video"]) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("The following video files could not be found. Click Browse to locate them."))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        for video in missing_videos:
            row = _RelinkRow(video)
            self._rows.append(row)
            inner_layout.addWidget(row)
        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_relinks(self) -> dict[str, str]:
        """Return {video_id: new_abs_path} for all rows where the user picked a file."""
        return {
            row.video_id: row.new_path
            for row in self._rows
            if row.new_path is not None
        }
