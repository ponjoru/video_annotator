"""Unit tests for UndoStack and Command implementations."""

import pytest

from video_annotator.undo import UndoStack


class _CounterCommand:
    """Minimal test command that increments/decrements a counter."""

    def __init__(self, counter: list):
        self._counter = counter

    def execute(self):
        self._counter[0] += 1

    def undo(self):
        self._counter[0] -= 1


class TestUndoStack:
    def test_push_executes_command(self, qtbot):
        stack = UndoStack()
        counter = [0]
        stack.push(_CounterCommand(counter))
        assert counter[0] == 1

    def test_undo_reverses_command(self, qtbot):
        stack = UndoStack()
        counter = [0]
        stack.push(_CounterCommand(counter))
        stack.undo()
        assert counter[0] == 0

    def test_undo_on_empty_stack_is_noop(self, qtbot):
        stack = UndoStack()
        stack.undo()   # should not raise

    def test_can_undo_reflects_stack_state(self, qtbot):
        stack = UndoStack()
        assert not stack.can_undo
        counter = [0]
        stack.push(_CounterCommand(counter))
        assert stack.can_undo
        stack.undo()
        assert not stack.can_undo

    def test_clear_empties_stack(self, qtbot):
        stack = UndoStack()
        counter = [0]
        stack.push(_CounterCommand(counter))
        stack.clear()
        assert not stack.can_undo

    def test_multiple_pushes_and_undos(self, qtbot):
        stack = UndoStack()
        counter = [0]
        stack.push(_CounterCommand(counter))
        stack.push(_CounterCommand(counter))
        stack.push(_CounterCommand(counter))
        assert counter[0] == 3
        stack.undo()
        assert counter[0] == 2
        stack.undo()
        assert counter[0] == 1
