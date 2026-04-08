"""TagEditor: chip-based tag input with autocomplete.

Used in two contexts:
  - Video-level tags (in SidePanel, above SegmentList)
  - Per-segment tags (inline in SegmentList rows)

Vocabulary for autocomplete is supplied externally and updated via set_vocabulary().

Chip addition is optimistic: the chip appears immediately when the user confirms
input, and tag_added is emitted so the controller can persist it. set_tags() is
the authoritative reset (called when loading a new video).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QStringListModel, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class _TagLineEdit(QLineEdit):
    """QLineEdit that clears focus and returns control to the main window on Escape."""

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.clear()
            self.clearFocus()
            # Walk up to the top-level window and set focus there so keyboard
            # shortcuts (Space, I, O …) are active again immediately.
            top = self.window()
            if top:
                top.setFocus()
            event.accept()
        else:
            super().keyPressEvent(event)


class TagChip(QWidget):
    """A single tag displayed as a pill label with a remove button."""

    removed = Signal(str)    # tag text

    def __init__(self, tag: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tag = tag
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 4, 2)
        layout.setSpacing(3)

        display = self._tag if len(self._tag) <= 20 else self._tag[:17] + "…"
        label = QLabel(display)
        if len(self._tag) > 20:
            label.setToolTip(self._tag)
        layout.addWidget(label)

        remove_btn = QPushButton("×")
        remove_btn.setFixedSize(16, 16)
        remove_btn.setFlat(True)
        remove_btn.setToolTip(f"Remove tag '{self._tag}'")
        remove_btn.clicked.connect(lambda: self.removed.emit(self._tag))
        layout.addWidget(remove_btn)

        self.setStyleSheet(
            "TagChip { background: #3c3c3c; border-radius: 4px; }"
            "TagChip QPushButton { color: #aaa; }"
            "TagChip QPushButton:hover { color: #fff; }"
        )

    @property
    def tag(self) -> str:
        return self._tag


class TagEditor(QWidget):
    """Chip strip + text input for managing a list of string tags."""

    tag_added = Signal(str)    # emitted so controller can persist
    tag_removed = Signal(str)  # emitted so controller can persist

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tags: list[str] = []
        self._vocabulary: list[str] = []
        self._chips: dict[str, TagChip] = {}
        self._vocab_model = QStringListModel([])
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        # --- Chip strip (horizontal scroll area) ---
        self._chips_widget = QWidget()
        self._chips_layout = QHBoxLayout(self._chips_widget)
        self._chips_layout.setContentsMargins(2, 2, 2, 2)
        self._chips_layout.setSpacing(4)
        self._chips_layout.addStretch()

        chips_scroll = QScrollArea()
        chips_scroll.setWidgetResizable(True)
        chips_scroll.setFixedHeight(34)
        chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        chips_scroll.setWidget(self._chips_widget)
        outer.addWidget(chips_scroll)

        # --- Tag input ---
        self._input = _TagLineEdit()
        self._input.setPlaceholderText("Add tag…")

        completer = QCompleter(self._vocab_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._input.setCompleter(completer)
        self._input.returnPressed.connect(self._on_input_confirmed)
        # Comma also confirms a tag (common UX convention)
        self._input.textChanged.connect(self._on_text_changed)
        outer.addWidget(self._input)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_tags(self, tags: list[str]) -> None:
        """Replace the displayed chips (authoritative reset from controller)."""
        # Remove all existing chips first
        for tag in list(self._chips.keys()):
            self._remove_chip_widget(tag)
        self._tags = []
        for tag in tags:
            self._add_chip_widget(tag)

    def set_vocabulary(self, vocabulary: list[str]) -> None:
        """Update the autocomplete vocabulary."""
        self._vocabulary = vocabulary
        self._vocab_model.setStringList(vocabulary)

    def focus_input(self) -> None:
        """Give keyboard focus to the tag input field (T shortcut)."""
        self._input.setFocus()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_text_changed(self, text: str) -> None:
        """Confirm on comma, stripping it from the input."""
        if text.endswith(","):
            self._input.setText(text[:-1])
            self._on_input_confirmed()

    def _on_input_confirmed(self) -> None:
        """Called when user presses Enter (or comma, handled by _on_text_changed)."""
        text = self._input.text().strip()
        if not text or text in self._tags:
            self._input.clear()
            return
        self._input.clear()
        self._add_chip_widget(text)
        self.tag_added.emit(text)

    def _add_chip_widget(self, tag: str) -> None:
        """Create and insert a chip for the given tag."""
        if tag in self._chips:
            return
        chip = TagChip(tag)
        chip.removed.connect(self._on_chip_removed)
        # Insert before the trailing stretch so chips appear left-aligned.
        self._chips_layout.insertWidget(self._chips_layout.count() - 1, chip)
        self._chips[tag] = chip
        self._tags.append(tag)

    def _remove_chip_widget(self, tag: str) -> None:
        """Remove chip from the layout and clean up references."""
        chip = self._chips.pop(tag, None)
        if chip is None:
            return
        self._chips_layout.removeWidget(chip)
        chip.deleteLater()
        if tag in self._tags:
            self._tags.remove(tag)

    def _on_chip_removed(self, tag: str) -> None:
        self._remove_chip_widget(tag)
        self.tag_removed.emit(tag)
