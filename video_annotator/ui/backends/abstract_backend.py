"""AbstractPlayerBackend: interface all video backends must implement.

PlayerPanel owns one backend instance and delegates all calls to it.
Nothing above PlayerPanel in the stack ever references a concrete backend.

Signals are identical across all backends so PlayerPanel can simply
forward them without knowing which backend is active.
"""

from __future__ import annotations

from abc import abstractmethod

from PySide6.QtCore import QObject, Signal


class AbstractPlayerBackend(QObject):
    # ------------------------------------------------------------------ #
    # Signals — identical on every backend                                #
    # ------------------------------------------------------------------ #
    position_changed = Signal(float)   # current position in seconds
    duration_known   = Signal(float)   # total duration in seconds (once per load)
    vfr_detected     = Signal(bool)    # True if source has variable frame rate
    speed_changed    = Signal(float)   # new playback speed
    paused_changed   = Signal(bool)    # True = paused, False = playing
    playback_ended   = Signal()        # reached end of file

    # ------------------------------------------------------------------ #
    # Abstract interface                                                  #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def load(self, abs_path: str) -> None:
        """Load and start (paused) the video at *abs_path*."""

    @abstractmethod
    def play_pause(self) -> None:
        """Toggle between playing and paused."""

    @abstractmethod
    def seek_absolute(self, seconds: float) -> None:
        """Seek to an absolute position in seconds."""

    @abstractmethod
    def seek_relative(self, delta: float) -> None:
        """Seek forward (positive) or backward (negative) by *delta* seconds."""

    @abstractmethod
    def set_speed(self, speed: float) -> None:
        """Set playback speed (1.0 = normal)."""

    @abstractmethod
    def set_volume(self, volume: int) -> None:
        """Set volume 0–100."""

    @abstractmethod
    def stop(self) -> None:
        """Stop playback and release resources."""

    @property
    @abstractmethod
    def current_position(self) -> float:
        """Return the current playback position in seconds."""
