"""Undo stack for segment add/delete operations.

Scope: segment additions and deletions only.
Tag changes and video status changes are NOT undoable (v1).
No redo in v1; stack grows unbounded within a session.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from video_annotator.controllers.project_manager import ProjectManager
    from video_annotator.models.project import Segment


class Command(ABC):
    """Abstract base for all undoable commands."""

    @abstractmethod
    def execute(self) -> None:
        """Apply the command. Called once at push time."""
        ...

    @abstractmethod
    def undo(self) -> None:
        """Reverse the command's effect."""
        ...


class AddSegmentCommand(Command):
    """Records the addition of a segment so it can be deleted on undo."""

    def __init__(self, manager: "ProjectManager", video_id: str, segment: "Segment") -> None:
        self._manager = manager
        self._video_id = video_id
        self._segment = segment

    def execute(self) -> None:
        # No-op: segment was already inserted by ProjectManager.add_segment()
        # before the command was pushed. execute() is only called on redo (not v1).
        pass

    def undo(self) -> None:
        self._manager._delete_segment_direct(self._video_id, self._segment.id)


class DeleteSegmentCommand(Command):
    """Records the deletion of a segment so it can be restored on undo."""

    def __init__(self, manager: "ProjectManager", video_id: str, segment: "Segment") -> None:
        self._manager = manager
        self._video_id = video_id
        self._segment = segment  # full snapshot at deletion time

    def execute(self) -> None:
        # No-op: segment was already removed by ProjectManager.delete_segment()
        # before the command was pushed.
        pass

    def undo(self) -> None:
        self._manager._add_segment_direct(self._video_id, self._segment)


class TrimSegmentCommand(Command):
    """Records a segment trim so the original boundaries can be restored on undo."""

    def __init__(
        self,
        manager: "ProjectManager",
        video_id: str,
        segment_id: str,
        old_start: float,
        old_end: float,
        new_start: float,
        new_end: float,
    ) -> None:
        self._manager = manager
        self._video_id = video_id
        self._segment_id = segment_id
        self._old_start = old_start
        self._old_end = old_end
        self._new_start = new_start
        self._new_end = new_end

    def execute(self) -> None:
        # Already applied before push.
        pass

    def undo(self) -> None:
        self._manager._trim_segment_direct(
            self._video_id, self._segment_id, self._old_start, self._old_end
        )


class UndoStack(QObject):
    """Owns the command history for one session."""

    undo_performed = Signal()          # emitted after a successful undo
    stack_changed = Signal(bool)       # bool: can_undo — for menu item enable/disable

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stack: list[Command] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._stack)

    def push(self, command: Command) -> None:
        """Execute command and add it to the stack."""
        command.execute()
        self._stack.append(command)
        self.stack_changed.emit(self.can_undo)

    def undo(self) -> None:
        """Reverse the most recent command. No-op if stack is empty."""
        if not self._stack:
            return
        command = self._stack.pop()
        command.undo()
        self.undo_performed.emit()
        self.stack_changed.emit(self.can_undo)

    def clear(self) -> None:
        """Clear all history. Called when a new project/folder is opened."""
        self._stack.clear()
        self.stack_changed.emit(False)
