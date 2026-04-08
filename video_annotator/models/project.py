"""Core data model: Project, Video, Segment.

Rules:
- All fields are plain Python types (no Qt, no numpy).
- abs_path on Video is a computed property; never serialized.
- Segment IDs are UUID4, generated at creation, never reused.
"""

from __future__ import annotations

import datetime
import os
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Segment:
    id: str                   # UUID4, set at creation
    video_id: str             # parent Video.id
    start: float              # seconds, 3 decimal places
    end: float                # seconds
    tags: list[str]
    notes: str = ""
    exported: bool = False
    output_path: str = ""     # relative from output_folder root; set after export

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)

    @classmethod
    def create(cls, video_id: str, start: float, end: float) -> "Segment":
        """Factory: generate a new Segment with a fresh UUID."""
        return cls(
            id=str(uuid.uuid4()),
            video_id=video_id,
            start=round(start, 3),
            end=round(end, 3),
            tags=[],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "video_id": self.video_id,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,      # convenience; not read back on load
            "tags": list(self.tags),
            "notes": self.notes,
            "exported": self.exported,
            "output_path": self.output_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Segment":
        return cls(
            id=data["id"],
            video_id=data["video_id"],
            start=float(data["start"]),
            end=float(data["end"]),
            tags=list(data.get("tags", [])),
            notes=data.get("notes", ""),
            exported=bool(data.get("exported", False)),
            output_path=data.get("output_path", ""),
        )


@dataclass
class Video:
    id: str                   # UUID4
    filename: str             # original filename (basename only)
    relative_path: str        # relative to source_folder; persisted in session
    duration: float = 0.0    # populated on first load via ffprobe
    status: str = "unseen"   # "unseen" | "in_progress" | "done" | "skipped" | "missing"
    tags: list[str] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    is_vfr: bool = False      # set after ffprobe on first load

    # Reconstructed at session load; never persisted; excluded from __init__.
    _source_folder: str = field(default="", repr=False, compare=False, init=False)

    @property
    def abs_path(self) -> str:
        """Absolute path, derived from source_folder + relative_path at runtime."""
        if not self._source_folder:
            return self.relative_path
        return os.path.join(self._source_folder, self.relative_path)

    @classmethod
    def create(cls, filename: str, relative_path: str, source_folder: str) -> "Video":
        """Factory: generate a new Video entry with a fresh UUID."""
        v = cls(id=str(uuid.uuid4()), filename=filename, relative_path=relative_path)
        v._source_folder = source_folder
        return v

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "relative_path": self.relative_path,
            "duration": self.duration,
            "status": self.status,
            "tags": list(self.tags),
            "segments": [s.to_dict() for s in self.segments],
            "is_vfr": self.is_vfr,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], source_folder: str) -> "Video":
        v = cls(
            id=data["id"],
            filename=data["filename"],
            relative_path=data["relative_path"],
            duration=float(data.get("duration", 0.0)),
            status=data.get("status", "unseen"),
            tags=list(data.get("tags", [])),
            segments=[Segment.from_dict(s) for s in data.get("segments", [])],
            is_vfr=bool(data.get("is_vfr", False)),
        )
        v._source_folder = source_folder
        return v


@dataclass
class Project:
    source_folder: str
    output_folder: str
    created_at: str            # ISO 8601 UTC
    tag_vocabulary: list[str] = field(default_factory=list)
    videos: list[Video] = field(default_factory=list)
    version: int = 1           # schema version for future migrations

    def get_video(self, video_id: str) -> Video | None:
        for v in self.videos:
            if v.id == video_id:
                return v
        return None

    def all_segments(self) -> list[Segment]:
        result: list[Segment] = []
        for v in self.videos:
            result.extend(v.segments)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "source_folder": self.source_folder,
            "output_folder": self.output_folder,
            "created_at": self.created_at,
            "tag_vocabulary": list(self.tag_vocabulary),
            "videos": [v.to_dict() for v in self.videos],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        source_folder = data["source_folder"]
        p = cls(
            source_folder=source_folder,
            output_folder=data.get("output_folder", ""),
            created_at=data.get("created_at", ""),
            tag_vocabulary=list(data.get("tag_vocabulary", [])),
            version=int(data.get("version", 1)),
        )
        p.videos = [
            Video.from_dict(v, source_folder) for v in data.get("videos", [])
        ]
        return p

    @staticmethod
    def now_iso() -> str:
        return datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
