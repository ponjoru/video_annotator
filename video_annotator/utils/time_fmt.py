"""Time formatting utilities: seconds ↔ human-readable strings."""

from __future__ import annotations

import math


def seconds_to_hms(seconds: float) -> str:
    """Convert a float seconds value to HH:MM:SS.mmm format.

    Examples:
        0.0        → "00:00:00.000"
        61.5       → "00:01:01.500"
        3661.123   → "01:01:01.123"
    """
    if seconds < 0:
        seconds = 0.0
    # Truncate to milliseconds to avoid rounding up to the next second.
    total_ms = int(seconds * 1000)
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def hms_to_seconds(hms: str) -> float:
    """Parse a HH:MM:SS.mmm string back to float seconds.

    Raises:
        ValueError: if the string does not match expected format.
    """
    try:
        # Split off milliseconds first.
        if "." in hms:
            time_part, ms_part = hms.rsplit(".", 1)
        else:
            time_part, ms_part = hms, "0"

        parts = time_part.split(":")
        if len(parts) != 3:
            raise ValueError
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        ms = int(ms_part.ljust(3, "0")[:3])
        return h * 3600 + m * 60 + s + ms / 1000.0
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Cannot parse time string: {hms!r}") from exc


def format_duration(seconds: float) -> str:
    """Human-friendly compact duration: '1h 23m', '45m 12s', '8.3s'."""
    if seconds < 0:
        seconds = 0.0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {int(s):02d}s"
    return f"{s:.1f}s"
