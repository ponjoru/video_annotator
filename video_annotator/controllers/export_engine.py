"""ExportEngine: runs FFmpeg clips in a background QThread.

Design constraints:
- Receives an ExportSpec (snapshot), never a live Project reference.
- Main UI becomes read-only for the duration of export.
- Cancel: SIGTERM active subprocess + delete partial output file.
- Per-clip failures are collected and reported; export continues.
- After all clips: calls MetadataWriter.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, Signal

if TYPE_CHECKING:
    from video_annotator.models.project import Project, Segment, Video
    from video_annotator.settings import SettingsManager


# ---------------------------------------------------------------------------
# Export specification (immutable snapshot passed to ExportEngine)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClipSpec:
    video_id: str
    segment_id: str
    source_path: str        # absolute path to source video
    start: float
    end: float
    output_path: str        # absolute path to destination clip
    video_tags: list[str] = field(default_factory=list)
    segment_tags: list[str] = field(default_factory=list)
    source_filename: str = ""


@dataclass
class ExportSpec:
    output_folder: str
    export_mode: str        # 'reencode' | 'remux'
    reencode_crf: int
    reencode_preset: str
    use_videotoolbox: bool
    clips: list[ClipSpec] = field(default_factory=list)
    project_snapshot: dict = field(default_factory=dict)   # for MetadataWriter

    @classmethod
    def build(
        cls,
        project: "Project",
        settings: "SettingsManager",
        output_folder: str,
        export_mode: str,
    ) -> "ExportSpec":
        """Build an ExportSpec from the current project state.

        Output path layout:
          <output_folder>/<stem>/clip_<N>.mp4    (one sub-folder per source video)

        Collision resolution: if two source video stems are identical, the second
        (and subsequent) get a ``_<6-char-hash>`` suffix derived from their UUID.
        """
        # Determine effective VideoToolbox usage.
        vtb_setting = settings.use_videotoolbox
        use_vtb = (
            settings.videotoolbox_available
            if vtb_setting == "auto"
            else vtb_setting == "on"
        )

        # Collect videos that have at least one segment.
        videos_with_segs = [v for v in project.videos if v.segments]

        # Build stem → [video] map; resolve collisions.
        stem_count: dict[str, int] = {}
        stem_for_video: dict[str, str] = {}     # video_id → output sub-folder stem

        for video in videos_with_segs:
            raw_stem = Path(video.filename).stem
            count = stem_count.get(raw_stem, 0)
            stem_count[raw_stem] = count + 1
            if count == 0:
                stem_for_video[video.id] = raw_stem
            else:
                # Suffix with first 6 chars of the video's UUID.
                suffix = video.id.replace("-", "")[:6]
                stem_for_video[video.id] = f"{raw_stem}_{suffix}"

        # Build ClipSpec list (one per segment, sorted by video order then start).
        clips: list[ClipSpec] = []
        for video in videos_with_segs:
            stem = stem_for_video[video.id]
            for idx, seg in enumerate(sorted(video.segments, key=lambda s: s.start), start=1):
                ext = ".mp4"
                clip_filename = f"clip_{idx:03d}{ext}"
                out_path = os.path.join(output_folder, stem, clip_filename)
                clips.append(ClipSpec(
                    video_id=video.id,
                    segment_id=seg.id,
                    source_path=video.abs_path,
                    start=seg.start,
                    end=seg.end,
                    output_path=out_path,
                    video_tags=list(video.tags),
                    segment_tags=list(seg.tags),
                    source_filename=video.filename,
                ))

        return cls(
            output_folder=output_folder,
            export_mode=export_mode,
            reencode_crf=settings.reencode_crf,
            reencode_preset=settings.reencode_preset,
            use_videotoolbox=use_vtb,
            clips=clips,
            project_snapshot=project.to_dict(),
        )


# ---------------------------------------------------------------------------
# Worker thread
# ---------------------------------------------------------------------------

class _ExportWorker(QThread):

    clip_started  = Signal(int, int, str)   # index (1-based), total, clip_name
    clip_done     = Signal(int)              # index
    clip_failed   = Signal(str, str)         # clip_name, stderr
    export_complete  = Signal(int, str)      # clips_written, output_folder
    export_cancelled = Signal()

    def __init__(self, spec: ExportSpec, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._spec = spec
        self._cancel_requested = False
        self._active_proc: subprocess.Popen | None = None  # type: ignore[type-arg]

    def request_cancel(self) -> None:
        self._cancel_requested = True
        if self._active_proc and self._active_proc.poll() is None:
            self._active_proc.terminate()

    def run(self) -> None:
        failures: list[tuple[str, str]] = []
        written = 0

        for i, clip in enumerate(self._spec.clips, start=1):
            if self._cancel_requested:
                break

            clip_name = Path(clip.output_path).name
            self.clip_started.emit(i, len(self._spec.clips), clip_name)

            # Ensure output sub-directory exists.
            os.makedirs(os.path.dirname(clip.output_path), exist_ok=True)

            cmd = self._build_ffmpeg_cmd(clip)

            try:
                self._active_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except FileNotFoundError:
                failures.append((clip_name, "ffmpeg not found on PATH"))
                self.clip_failed.emit(clip_name, "ffmpeg not found on PATH")
                continue

            # Poll until done, checking for cancellation.
            assert self._active_proc is not None
            while self._active_proc.poll() is None:
                if self._cancel_requested:
                    self._active_proc.terminate()
                    break
                self.msleep(100)

            _, stderr_bytes = self._active_proc.communicate()
            stderr_text = stderr_bytes.decode(errors="replace").strip()

            if self._cancel_requested:
                # Delete partial output file.
                try:
                    if os.path.exists(clip.output_path):
                        os.remove(clip.output_path)
                except OSError:
                    pass
                break

            if self._active_proc.returncode != 0:
                msg = f"exit {self._active_proc.returncode}: {stderr_text}"
                failures.append((clip_name, msg))
                self.clip_failed.emit(clip_name, msg)
                # Delete partial output file.
                try:
                    if os.path.exists(clip.output_path):
                        os.remove(clip.output_path)
                except OSError:
                    pass
            else:
                written += 1
                self.clip_done.emit(i)

        self._active_proc = None

        if self._cancel_requested:
            self.export_cancelled.emit()
            return

        # Write metadata files.
        try:
            MetadataWriter.write(self._spec, self._spec.output_folder, failures)
        except Exception:
            pass

        # Write export_log.txt if there were failures.
        if failures:
            log_path = os.path.join(self._spec.output_folder, "export_log.txt")
            try:
                with open(log_path, "w", encoding="utf-8") as f:
                    for clip_name, reason in failures:
                        f.write(f"{clip_name}: {reason}\n")
            except OSError:
                pass

        self.export_complete.emit(written, self._spec.output_folder)

    def _build_ffmpeg_cmd(self, clip: ClipSpec) -> list[str]:
        """Construct the ffmpeg argument list for a single clip."""
        if self._spec.export_mode == "remux":
            # Lossless re-mux: fast input seek, stream copy.
            # -ss before -i is intentional for keyframe-accurate fast seek.
            return [
                "ffmpeg", "-y",
                "-ss", str(clip.start),
                "-to", str(clip.end),
                "-i", clip.source_path,
                "-c", "copy",
                clip.output_path,
            ]
        else:
            # Re-encode: input seek after -i for frame accuracy.
            video_codec = (
                "h264_videotoolbox"
                if self._spec.use_videotoolbox
                else "libx264"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", clip.source_path,
                "-ss", str(clip.start),
                "-to", str(clip.end),
                "-c:v", video_codec,
            ]
            if not self._spec.use_videotoolbox:
                cmd += ["-preset", self._spec.reencode_preset,
                        "-crf", str(self._spec.reencode_crf)]
            else:
                # VideoToolbox uses -q:v instead of -crf (0=best, 100=worst; ~50 ≈ CRF 18)
                cmd += ["-q:v", "50"]
            cmd += ["-c:a", "aac", "-b:a", "128k", clip.output_path]
            return cmd


# ---------------------------------------------------------------------------
# Public controller
# ---------------------------------------------------------------------------

class ExportEngine(QObject):
    """Thin wrapper around _ExportWorker; exposes a stable public API."""

    clip_started     = Signal(int, int, str)
    clip_done        = Signal(int)
    clip_failed      = Signal(str, str)
    export_complete  = Signal(int, str)
    export_cancelled = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker: _ExportWorker | None = None

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def start(self, spec: ExportSpec) -> None:
        """Begin export. Caller must set UI read-only before calling."""
        if self.is_running:
            raise RuntimeError("Export already in progress")
        worker = _ExportWorker(spec, self)
        worker.clip_started.connect(self.clip_started)
        worker.clip_done.connect(self.clip_done)
        worker.clip_failed.connect(self.clip_failed)
        worker.export_complete.connect(self.export_complete)
        worker.export_cancelled.connect(self.export_cancelled)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def request_cancel(self) -> None:
        if self._worker:
            self._worker.request_cancel()

    def _on_worker_finished(self) -> None:
        self._worker = None


# ---------------------------------------------------------------------------
# Metadata writer
# ---------------------------------------------------------------------------

class MetadataWriter:

    @staticmethod
    def write(
        spec: ExportSpec,
        output_folder: str,
        failures: list[tuple[str, str]] | None = None,
    ) -> None:
        """Write metadata.json and metadata.csv to output_folder."""
        failures = failures or []
        exported_at = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        # Build failed-clip set for marking.
        failed_names = {name for name, _ in failures}

        # --- JSON ---
        json_data: dict = {
            "exported_at": exported_at,
            "export_mode": spec.export_mode,
            "output_folder": output_folder,
            "clips": [],
        }
        for clip in spec.clips:
            clip_name = Path(clip.output_path).name
            json_data["clips"].append({
                "segment_id": clip.segment_id,
                "video_id": clip.video_id,
                "source_filename": clip.source_filename,
                "start": clip.start,
                "end": clip.end,
                "duration": round(clip.end - clip.start, 3),
                "video_tags": clip.video_tags,
                "segment_tags": clip.segment_tags,
                "output_path": clip.output_path,
                "exported": clip_name not in failed_names,
            })

        os.makedirs(output_folder, exist_ok=True)
        json_path = os.path.join(output_folder, "metadata.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        # --- CSV ---
        csv_path = os.path.join(output_folder, "metadata.csv")
        headers = [
            "segment_id", "video_id", "source_filename",
            "start", "end", "duration",
            "video_tags", "segment_tags", "output_path",
        ]
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            for clip in spec.clips:
                writer.writerow([
                    clip.segment_id,
                    clip.video_id,
                    clip.source_filename,
                    clip.start,
                    clip.end,
                    round(clip.end - clip.start, 3),
                    ";".join(clip.video_tags),
                    ";".join(clip.segment_tags),
                    clip.output_path,
                ])
