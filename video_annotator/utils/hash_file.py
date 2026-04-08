"""File identity hash used as a cache key.

Key = sha1(abs_path + str(file_size) + str(mtime))[:16]
This invalidates when the file is modified or replaced, without reading its contents.
"""

from __future__ import annotations

import hashlib
import os


def hash_file(abs_path: str) -> str:
    """Return a 16-character hex string uniquely identifying this file version.

    Raises:
        FileNotFoundError: if the path does not exist.
        OSError: on permission or I/O error.
    """
    stat = os.stat(abs_path)
    raw = f"{abs_path}|{stat.st_size}|{stat.st_mtime}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]
