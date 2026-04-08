"""ProjectManager: owns the in-memory Project and coordinates all mutations.

Rules:
- This is the ONLY place that mutates Project state.
- Every mutating method emits the appropriate Qt signal.
- Every mutation triggers a debounced auto-save (500 ms).
- No UI imports allowed in this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from video_annotator.models.project import Project, Segment, Video
from video_annotator.models.session_store import SessionStore, SessionCorruptError

if TYPE_CHECKING:
    from video_annotator.settings import SettingsManager


@dataclass
class OpenFolderResult:
    """Returned by open_folder(); caller decides whether to restore or start fresh."""
    folder: str
    video_files: list[str]       # relative paths found by scan
    has_session: bool
    session_video_count: int = 0
    session_segment_count: int = 0


# Video file extensions considered valid candidates (ffprobe confirms on first load).
_VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".flv",
}


class ProjectManager(QObject):
    # --- Signals ---
    project_changed = Signal()                         # catch-all for full refresh
    video_changed = Signal(str)                        # video_id
    segment_added = Signal(str, str)                   # video_id, segment_id
    segment_deleted = Signal(str, str)                 # video_id, segment_id
    tag_vocabulary_changed = Signal(list)              # list[str] new vocabulary

    def __init__(self, settings: "SettingsManager", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._project: Project | None = None

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._do_save)

    @property
    def project(self) -> Project | None:
        return self._project

    # ------------------------------------------------------------------
    # Folder / session lifecycle
    # ------------------------------------------------------------------

    def open_folder(self, path: str) -> OpenFolderResult:
        """Scan a directory and return metadata; does NOT commit state yet.

        Caller inspects OpenFolderResult and then calls restore_session() or new_project().
        """
        video_files: list[str] = []
        for entry in sorted(os.listdir(path)):
            ext = os.path.splitext(entry)[1].lower()
            if ext in _VIDEO_EXTENSIONS:
                video_files.append(entry)   # relative filename only

        has_session = SessionStore.exists(path)
        session_video_count = 0
        session_segment_count = 0

        if has_session:
            try:
                tmp = SessionStore.load(path)
                session_video_count = len(tmp.videos)
                session_segment_count = sum(len(v.segments) for v in tmp.videos)
            except SessionCorruptError:
                has_session = False   # treat corrupt session as absent

        return OpenFolderResult(
            folder=path,
            video_files=video_files,
            has_session=has_session,
            session_video_count=session_video_count,
            session_segment_count=session_segment_count,
        )

    def restore_session(self, folder: str) -> None:
        """Load and commit the existing session from folder.

        Raises SessionCorruptError if the session cannot be read.
        """
        project = SessionStore.load(folder)
        self._project = project
        self.project_changed.emit()

    def new_project(self, folder: str, video_files: list[str]) -> None:
        """Create a blank project for the given folder and video file list."""
        output_folder = os.path.join(folder, "output")
        project = Project(
            source_folder=folder,
            output_folder=output_folder,
            created_at=Project.now_iso(),
        )
        for rel_path in video_files:
            filename = os.path.basename(rel_path)
            v = Video.create(filename, rel_path, folder)
            project.videos.append(v)

        self._project = project
        self.project_changed.emit()

    def relink_video(self, video_id: str, new_abs_path: str) -> None:
        """Update a missing video's path and clear its 'missing' status."""
        video = self._get_video(video_id)
        if self._project is None:
            return
        video.relative_path = os.path.relpath(new_abs_path, self._project.source_folder)
        video.filename = os.path.basename(new_abs_path)
        video._source_folder = self._project.source_folder
        video.status = "unseen"
        self.video_changed.emit(video_id)
        self._schedule_save()

    # ------------------------------------------------------------------
    # Video mutations
    # ------------------------------------------------------------------

    def set_video_status(self, video_id: str, status: str) -> None:
        assert status in {"unseen", "in_progress", "done", "skipped", "missing"}
        video = self._get_video(video_id)
        video.status = status
        self.video_changed.emit(video_id)
        self.project_changed.emit()
        self._schedule_save()

    def set_video_info(self, video_id: str, duration: float, is_vfr: bool) -> None:
        """Called by PlaybackController after ffprobe to store duration and VFR flag."""
        video = self._get_video(video_id)
        video.duration = duration
        video.is_vfr = is_vfr
        self._schedule_save()

    def add_video_tag(self, video_id: str, tag: str) -> None:
        video = self._get_video(video_id)
        if tag not in video.tags:
            video.tags.append(tag)
        self._ensure_in_vocabulary(tag)
        self.video_changed.emit(video_id)
        self._schedule_save()

    def remove_video_tag(self, video_id: str, tag: str) -> None:
        video = self._get_video(video_id)
        if tag in video.tags:
            video.tags.remove(tag)
        self.video_changed.emit(video_id)
        self._schedule_save()

    def rename_tag(self, old: str, new: str) -> None:
        """Atomically rename a tag across all videos and segments and the vocabulary."""
        if self._project is None:
            return
        if old in self._project.tag_vocabulary:
            idx = self._project.tag_vocabulary.index(old)
            self._project.tag_vocabulary[idx] = new
        for video in self._project.videos:
            video.tags = [new if t == old else t for t in video.tags]
            for seg in video.segments:
                seg.tags = [new if t == old else t for t in seg.tags]
        self.tag_vocabulary_changed.emit(list(self._project.tag_vocabulary))
        self.project_changed.emit()
        self._do_save()   # flush immediately for a rename (don't debounce)

    # ------------------------------------------------------------------
    # Segment mutations (called by UndoStack commands and PlaybackController)
    # ------------------------------------------------------------------

    def add_segment(self, video_id: str, start: float, end: float) -> Segment:
        """Create and record a segment. Pushes to UndoStack. Returns the new Segment."""
        from video_annotator.undo import AddSegmentCommand  # late import avoids circular
        seg = Segment.create(video_id, start, end)
        self._add_segment_direct(video_id, seg)
        if hasattr(self, "_undo_stack") and self._undo_stack is not None:
            self._undo_stack.push(AddSegmentCommand(self, video_id, seg))
        return seg

    def _add_segment_direct(self, video_id: str, segment: Segment) -> None:
        """Insert a segment without touching the UndoStack (used by undo commands)."""
        video = self._get_video(video_id)
        # Insert sorted by start time.
        inserted = False
        for i, existing in enumerate(video.segments):
            if segment.start < existing.start:
                video.segments.insert(i, segment)
                inserted = True
                break
        if not inserted:
            video.segments.append(segment)
        if video.status == "unseen":
            video.status = "in_progress"
        self.segment_added.emit(video_id, segment.id)
        self.video_changed.emit(video_id)
        self._schedule_save()

    def delete_segment(self, video_id: str, segment_id: str) -> None:
        """Remove a segment. Pushes to UndoStack."""
        from video_annotator.undo import DeleteSegmentCommand
        video = self._get_video(video_id)
        seg = self._get_segment(video, segment_id)
        self._delete_segment_direct(video_id, segment_id)
        if hasattr(self, "_undo_stack") and self._undo_stack is not None:
            self._undo_stack.push(DeleteSegmentCommand(self, video_id, seg))

    def _delete_segment_direct(self, video_id: str, segment_id: str) -> None:
        """Remove segment without touching UndoStack (used by undo commands)."""
        video = self._get_video(video_id)
        video.segments = [s for s in video.segments if s.id != segment_id]
        self.segment_deleted.emit(video_id, segment_id)
        self.video_changed.emit(video_id)
        self._schedule_save()

    def add_segment_tag(self, video_id: str, segment_id: str, tag: str) -> None:
        video = self._get_video(video_id)
        seg = self._get_segment(video, segment_id)
        if tag not in seg.tags:
            seg.tags.append(tag)
        self._ensure_in_vocabulary(tag)
        self.video_changed.emit(video_id)
        self._schedule_save()

    def remove_segment_tag(self, video_id: str, segment_id: str, tag: str) -> None:
        video = self._get_video(video_id)
        seg = self._get_segment(video, segment_id)
        if tag in seg.tags:
            seg.tags.remove(tag)
        self.video_changed.emit(video_id)
        self._schedule_save()

    def set_segment_notes(self, video_id: str, segment_id: str, notes: str) -> None:
        video = self._get_video(video_id)
        seg = self._get_segment(video, segment_id)
        seg.notes = notes
        self.video_changed.emit(video_id)
        self._schedule_save()

    def trim_segment(
        self, video_id: str, segment_id: str, new_start: float, new_end: float
    ) -> None:
        """Update a segment's start/end boundaries. Pushes to UndoStack."""
        from video_annotator.undo import TrimSegmentCommand
        video = self._get_video(video_id)
        seg = self._get_segment(video, segment_id)
        old_start, old_end = seg.start, seg.end
        self._trim_segment_direct(video_id, segment_id, new_start, new_end)
        if hasattr(self, "_undo_stack") and self._undo_stack is not None:
            self._undo_stack.push(
                TrimSegmentCommand(self, video_id, segment_id, old_start, old_end, new_start, new_end)
            )

    def _trim_segment_direct(
        self, video_id: str, segment_id: str, new_start: float, new_end: float
    ) -> None:
        """Apply trim without touching UndoStack (used by undo commands)."""
        video = self._get_video(video_id)
        seg = self._get_segment(video, segment_id)
        seg.start = new_start
        seg.end = new_end
        # Re-sort the list to maintain start-time order.
        video.segments.sort(key=lambda s: s.start)
        self.video_changed.emit(video_id)
        self._schedule_save()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_in_vocabulary(self, tag: str) -> None:
        if self._project is None:
            return
        if tag not in self._project.tag_vocabulary:
            self._project.tag_vocabulary.append(tag)
            self._project.tag_vocabulary.sort()
            self.tag_vocabulary_changed.emit(list(self._project.tag_vocabulary))

    def _schedule_save(self) -> None:
        """Reset the debounce timer; actual save fires 500 ms after the last mutation."""
        self._save_timer.start()

    def _do_save(self) -> None:
        """Called by the debounce timer. Saves synchronously on the main thread."""
        if self._project is None:
            return
        try:
            SessionStore.save(self._project)
        except Exception:
            pass   # TODO: emit save_error signal; show toast in UI

    def _get_video(self, video_id: str) -> Video:
        """Return video by id or raise KeyError."""
        if self._project is None:
            raise RuntimeError("No project open")
        v = self._project.get_video(video_id)
        if v is None:
            raise KeyError(f"Unknown video_id: {video_id}")
        return v

    def _get_segment(self, video: Video, segment_id: str) -> Segment:
        for s in video.segments:
            if s.id == segment_id:
                return s
        raise KeyError(f"Unknown segment_id: {segment_id}")
