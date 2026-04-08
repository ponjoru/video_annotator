"""Session persistence: read/write Project state to .videoann_session.json.

Write strategy: serialize to a .tmp file, then os.replace() for atomicity.
The previous valid session is never destroyed by a failed write.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from video_annotator.models.project import Project


SESSION_FILENAME = ".videoann_session.json"
_TMP_SUFFIX = ".tmp"
_SUPPORTED_VERSIONS = {1}


class SessionSaveError(Exception):
    """Raised when the session file cannot be written (permissions, disk full, etc.)."""


class SessionCorruptError(Exception):
    """Raised when the session file exists but cannot be parsed or has an unsupported version."""


class SessionStore:
    """Stateless helper; all methods are class/static methods."""

    @staticmethod
    def session_path(folder: str) -> Path:
        return Path(folder) / SESSION_FILENAME

    @staticmethod
    def exists(folder: str) -> bool:
        return SessionStore.session_path(folder).exists()

    @staticmethod
    def save(project: Project) -> None:
        """Serialize project to disk atomically.

        Raises:
            SessionSaveError: if the write fails for any reason.
        """
        session_path = SessionStore.session_path(project.source_folder)
        tmp_path = session_path.with_suffix(_TMP_SUFFIX)

        try:
            payload = json.dumps(project.to_dict(), ensure_ascii=False, indent=2)
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, session_path)
        except OSError as exc:
            raise SessionSaveError(f"Could not save session: {exc}") from exc

    @staticmethod
    def load(folder: str) -> Project:
        """Deserialize project from disk.

        Returns:
            A fully populated Project with _source_folder set on each video.
            Videos whose file no longer exists are flagged status='missing'.

        Raises:
            SessionCorruptError: if the file is missing, malformed, or wrong version.
        """
        path = SessionStore.session_path(folder)

        if not path.exists():
            raise SessionCorruptError(f"No session file at {path}")

        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionCorruptError(f"Session file unreadable: {exc}") from exc

        version = data.get("version", 1)
        if version not in _SUPPORTED_VERSIONS:
            raise SessionCorruptError(f"Unsupported session version: {version}")

        data = SessionStore.migrate(data)

        try:
            project = Project.from_dict(data)
        except (KeyError, TypeError, ValueError) as exc:
            raise SessionCorruptError(f"Session file malformed: {exc}") from exc

        # Validate each video file exists on disk; flag missing ones.
        for video in project.videos:
            if video.status != "missing" and not os.path.isfile(video.abs_path):
                video.status = "missing"

        return project

    @staticmethod
    def migrate(data: dict) -> dict:  # type: ignore[type-arg]
        """Apply schema migrations in sequence until data reaches the current version.

        Stub for v1; extend as new versions are introduced.
        """
        # version 1→2, 2→3, etc. would go here
        return data
