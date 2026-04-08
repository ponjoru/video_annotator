"""QApplication bootstrap and top-level initialization."""

from __future__ import annotations

import os
import sys
from typing import Sequence

from PySide6.QtWidgets import QApplication

from video_annotator.main_window import MainWindow
from video_annotator.settings import SettingsManager
from video_annotator.utils.ffprobe import check_videotoolbox_available


def run(argv: Sequence[str]) -> int:
    app = QApplication(list(argv))
    app.setApplicationName("VideoAnnotator")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("VideoAnnotator")
    app.setOrganizationDomain("com.videoannotator.app")

    settings = SettingsManager()

    # Detect VideoToolbox once at startup; result stored in-process only (not persisted).
    if settings.use_videotoolbox == "on":
        settings.videotoolbox_available = True
    elif settings.use_videotoolbox == "off":
        settings.videotoolbox_available = False
    else:
        settings.videotoolbox_available = check_videotoolbox_available()

    window = MainWindow(settings)
    window.show()

    # Accept an optional folder path from the command line.
    if len(argv) > 1:
        folder = argv[1]
        if isinstance(folder, str) and os.path.isdir(folder):
            window.open_folder(folder)

    return app.exec()
