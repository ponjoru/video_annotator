# VideoAnnotator — Design Document

## 1. Problem Statement

ML practitioners working with raw video data spend disproportionate time manually scrubbing through long recordings (up to 1 hour) to identify usable segments. Existing video editors are designed for content creation, not data curation: they are slow to navigate, lack workflow shortcuts, and produce no machine-readable metadata. No lightweight tool exists that combines fast video navigation, segment selection, tag-based labeling, and structured export — all tuned for the ML dataset preparation workflow.

**VideoAnnotator** fills this gap. It is a macOS desktop tool optimized for one job: open a folder of videos, watch them fast, mark the segments you want, export them with structured metadata, and move on.

---

## 2. Goals

- Minimize total time from raw folder → labeled dataset of cropped clips
- Support videos up to 1 hour with no performance degradation
- Keyboard-first workflow — mouse is optional
- Produce structured, portable metadata (JSON + CSV) suitable for downstream ML pipelines
- Auto-save all progress so the session can be interrupted and resumed

### Non-Goals

- Bounding box / mask / keypoint annotation — out of scope
- Cloud sync or multi-user collaboration
- Automatic activity detection or scene classification (though motion heatmap aids manual scanning)

---

## 3. User Flow

```
1. Launch app → Open folder
2. App loads video list, restores previous session if one exists
3. For each video (in any order):
   a. Video loads in player
   b. User scrubs through using keyboard shortcuts / timeline
   c. User marks one or more segments (In / Out points)
   d. User optionally adds tags at video level or per-segment
   e. User marks video as Done or Skipped and moves to next
4. At any point: Export All Segments
   → Clips saved as per-source subfolders
   → metadata.json and metadata.csv written to output folder
```

---

## 4. Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | Native to ML toolchains; rich ecosystem |
| UI framework | PySide6 (Qt 6) | Mature, native macOS look, custom widget support, no Electron overhead |
| Video playback | `python-mpv` (libmpv binding) | Hardware-accelerated, supports all formats, fine-grained speed/seek control |
| Video processing | FFmpeg (subprocess) | Frame-accurate re-encode (default), lossless re-mux option, waveform extraction |
| Audio waveform | FFmpeg + NumPy | Extract PCM, downsample to pixel-level resolution |
| Motion analysis | Deferred to v2 | Raw frame-diff heatmap removed; global camera motion makes it unreliable without stabilization |
| Metadata | JSON (stdlib) + CSV (stdlib) | Zero extra dependencies |
| Session persistence | JSON (auto-saved to source folder) | Human-readable, no DB setup required |
| Packaging | PyInstaller → `.app` bundle | Single-click launch on macOS |

---

## 5. Application Architecture

### 5.1 High-Level Component Map

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           MainWindow (PySide6)                          │
│                                                                         │
│  ┌──────────────┐  ┌──────────────────────────────┐  ┌──────────────┐  │
│  │  VideoList   │  │        PlayerPanel            │  │  SidePanel   │  │
│  │  Panel       │  │  ┌────────────────────────┐   │  │              │  │
│  │              │  │  │   mpv embed canvas     │   │  │  TagEditor   │  │
│  │  - badges    │  │  └────────────────────────┘   │  │  (video)     │  │
│  │    (status,  │  │  ┌────────────────────────┐   │  │              │  │
│  │    VFR,      │  │  │   TransportBar          │   │  │  SegmentList │  │
│  │    missing)  │  │  │   speed / time / pos   │   │  │  per-segment │  │
│  │  - progress  │  │  └────────────────────────┘   │  │  tags        │  │
│  │    bar       │  │  ┌────────────────────────┐   │  │              │  │
│  └──────────────┘  │  │   TimelineWidget        │   │  │  Export btn  │  │
│                    │  │   waveform lane (opt.)  │   │  └──────────────┘  │
│                    │  │   segment band          │   │                    │
│                    │  │   minimap strip         │   │                    │
│                    │  └────────────────────────┘   │                    │
│                    └──────────────────────────────┘                    │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  StatusBar — idle stats / export progress + cancel              │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
         │                      │                        │
         ▼                      ▼                        ▼
  ProjectManager          PlaybackController       ExportEngine
  SessionStore            WaveformCache            MetadataWriter
  UndoStack               SettingsPanel            ExportPreviewDialog
```

### 5.2 Component Descriptions

#### `ProjectManager`
- Owns the in-memory project state (all videos, segments, tags)
- Monitors the source folder for new video files
- Triggers auto-save after every mutating action (debounced 500 ms)
- Exposes: `open_folder()`, `get_video_list()`, `mark_done()`, `mark_skipped()`
- **Tag rename**: `rename_tag(old, new)` atomically updates the tag vocabulary and rewrites every reference across all videos and segments in memory; auto-save fires immediately after

#### `SessionStore`
- Serializes/deserializes the project state to `<source_folder>/.videoann_session.json`
- **Paths stored as relative** to the session file's directory so the folder can be moved or copied to another machine without breaking the session
- On open: detects existing session and prompts to restore or start fresh
- **Relink dialog**: on session restore, any video file that cannot be resolved at its stored relative path gets a `missing` badge in the VideoListPanel; a "Relink missing files…" button opens a dialog where the user manually points each missing entry to its new location; re-linked paths are saved back to the session
- Format mirrors the data model (Section 6)

#### `VideoListPanel`
- Sorted list of all video files in the folder; accepts any format that FFmpeg and mpv can open (no extension whitelist)
- Status badge per video: Unseen / In Progress / Done / Skipped / Missing (file not found)
- **VFR badge**: on video load, the container's `r_frame_rate` vs `avg_frame_rate` is checked via `ffprobe`; if they differ the video gets a yellow `VFR` badge and a tooltip explaining that CFR input is assumed
- Progress bar: `done_count / total_count`
- Double-click or Enter → load video

#### `PlayerPanel` (mpv canvas)
- Embeds libmpv into a Qt widget via `python-mpv` XID/HWND embedding
- Variable speed: 1x, 1.5x, 2x, 4x, 8x — set via `mpv.speed` property
- Seek: frame-step, ±1s, ±5s, ±30s, absolute seek to clicked position on timeline
- Reports current playback position to `TimelineWidget` at ~10 Hz

#### `TransportBar`
- Displays: current timestamp / total duration
- Speed selector (button group or dropdown)
- Play/Pause button (redundant with Space key)
- Volume control

#### `TimelineWidget` (custom `QWidget`)
- Renders at pixel resolution mapped to the current visible time window
- Two visual layers stacked vertically:
  1. **Waveform lane** — audio amplitude as filled curve; hidden entirely (lane collapses) when the video has no audio track; waveform pixels are visually interpolated (bilinear stretch) at any zoom level — no re-extraction required
  2. **Segment band lane** — colored rectangles for each marked segment; drag left/right edge to trim; **overlapping segments** are shown with a red striped fill and a tooltip warning — they are not prevented, just surfaced
- Playhead: draggable vertical line; click anywhere to seek
- Rubber-band selection: drag to define a candidate in/out region, confirmed with `O` (Out) or Enter
- **Segment validation on confirm**:
  - If `end − start < 0.5 s` → toast warning "Segment too short (minimum 0.5 s)" and discard
  - If `end − start > max_segment_duration` (configurable in Settings, default 5 min) → toast warning "Segment unusually long — confirm?" with Accept / Cancel buttons
- **Timeline zoom**: scroll wheel, `+`/`-` keys, and macOS trackpad pinch gesture all zoom around the playhead or cursor position; visible window represents a sub-range of full duration; at max zoom, one pixel ≈ one frame; at min zoom, full video fits the widget; `Ctrl+0` resets to fit
- **Minimap strip** below the main timeline: renders the full duration at fixed height; a highlight rectangle shows the current visible window; clicking or dragging on the minimap pans the view

#### `SidePanel`
- **TagEditor (video-level)**: free-text tag input with autocomplete from global tag vocabulary; chips display; click to remove; renaming a tag via the vocabulary manager propagates to all videos/segments
- **SegmentList**: scrollable list of all segments for the current video, each row shows `[start → end] [duration] [tags] [delete]`; overlapping pairs are highlighted; click to seek to segment start; per-segment tag editor inline
- **Export button**: opens `ExportPreviewDialog`; disabled (grayed out) during an active export

#### `PlaybackController`
- Thin bridge between UI events and mpv API
- Translates keyboard shortcuts into mpv commands
- Owns In/Out point state for the segment currently being defined
- Emits signals: `position_changed`, `duration_known`, `playback_ended`
- On `playback_ended`: pauses at the last frame and does nothing — no auto-advance; user drives navigation

#### `UndoStack`
- Covers segment add and segment delete only (tag changes and video status changes are not undoable)
- Unlimited depth (bounded only by session memory)
- Implemented as a simple command list with `execute` / `undo` interface
- `Ctrl+Z` pops and reverses the last command; no redo in v1

#### `SettingsPanel`
- Accessible from the menu bar; stores preferences in the standard macOS app preferences location (`~/Library/Preferences/`)
- Configurable values (with defaults):
  - `max_segment_duration_warning`: absolute duration in seconds (default: 300 s / 5 min); segment confirmation shows a warning toast if exceeded
  - `min_segment_duration`: absolute duration in seconds (default: 0.5 s); shorter segments are rejected with a toast
  - `default_export_mode`: `reencode` | `remux` (default: `reencode`)
  - `reencode_crf`: CRF value for libx264 (default: 18)
  - `reencode_preset`: FFmpeg preset (default: `fast`)
  - `use_videotoolbox`: auto-detect or force on/off (default: auto-detect)

#### `WaveformCache`
- On video load: checks whether the video has an audio track via `ffprobe`; if none, skips extraction and signals `TimelineWidget` to hide the waveform lane
- If audio exists: runs FFmpeg in a background thread to extract PCM (`-f s16le -ac 1 -ar 4000`) for the full video duration
- Stores one amplitude value per ~10 ms of audio (i.e. ~400 samples/second for a 4000 Hz downsampled signal) in a NumPy array
- At render time, the array is mapped to the visible pixel columns via linear interpolation — no re-extraction at any zoom level
- Saves result to `<source_folder>/.cache/<video_hash>_waveform.npy`
- Cache keyed by (absolute path + file size + mtime) to invalidate on file change

#### `ExportPreviewDialog`
- Shown when the user triggers `Ctrl+E`, before any files are written
- **Output folder selector**: shown at the top of the dialog; defaults to the last-used path (stored in app preferences, not in the session); user can change it via a native folder picker each time; path is validated (writable, sufficient disk space estimated)
- Displays a summary table:
  - Number of source videos with segments
  - Total number of clips to export
  - Total output duration (sum of all segment durations)
  - Estimated output size (bitrate × duration, shown as a rough range)
  - Selected export mode (re-encode / lossless re-mux)
  - Any warnings: VFR-flagged source videos, overlapping segments, clips that would overwrite existing files
- Two action buttons: **Export** (proceeds) and **Cancel** (returns to session)
- Export mode can be switched in the dialog without navigating to settings
- **Always re-exports everything**: no skip-existing logic; every export run is a full fresh write, overwriting any prior output at the same paths

#### `ExportEngine`
- Runs in a `QThread`; the main UI becomes read-only for the duration (no segment add/remove, no tag edits, no video status changes)
- Iterates all videos with at least one segment
- **Output folder naming**: subfolder name = video filename stem; if a collision is detected (two source videos with the same stem), a short 6-character hash suffix is appended to the second name: `recording/` and `recording_a3f2c1/`
- **Default mode — frame-accurate re-encode:**
  ```
  ffmpeg -i <source> -ss <start> -to <end> \
    -c:v libx264 -preset fast -crf 18 \
    -c:a aac -b:a 128k \
    <output_path>
  ```
  On macOS, `h264_videotoolbox` is used automatically when available for hardware-accelerated encoding.
- **Alternative mode — lossless re-mux** (user opt-in via ExportPreviewDialog or Settings):
  ```
  ffmpeg -i <source> -ss <start> -to <end> -c copy <output_path>
  ```
  Re-mux is faster but cuts snap to the nearest keyframe (boundary drift up to ~2 s). Surfaced in the UI with a warning label.
- **Always re-exports**: no skip-existing logic; prior output files are overwritten unconditionally
- Progress is reported via Qt signals to the **StatusBar** (per-clip counter + overall progress bar) and supports cancel: sends SIGTERM to the active FFmpeg subprocess and deletes the partial output file before stopping
- After all clips are written, calls `MetadataWriter`

#### `StatusBar`
- Persistent strip at the very bottom of the `MainWindow`; always visible
- At rest: shows current video filename and session stats (N videos done, M segments marked)
- During export: shows `Exporting clip 12 / 34 — session_01 [████████░░] 67% — Cancel` with a cancel button; progress updates via signal from `ExportEngine`
- On export complete: shows `Export complete — 34 clips written to /path/to/output` for 5 seconds, then returns to idle state

#### `MetadataWriter`
- Writes `metadata.json` and `metadata.csv` to the output folder root
- JSON is the authoritative record; CSV is a flat denormalized summary for spreadsheet use

---

## 6. Data Model

```python
@dataclass
class Segment:
    id: str                  # UUID4
    video_id: str            # parent video UUID
    start: float             # seconds (float, 3 decimal places)
    end: float               # seconds
    tags: list[str]          # segment-level tags
    notes: str               # free-text annotation (optional)
    exported: bool           # True once clip file has been written
    output_path: str         # relative path from output_folder root

@dataclass
class Video:
    id: str                  # UUID4
    filename: str            # original filename
    abs_path: str            # absolute path on disk
    duration: float          # seconds (populated on first load)
    status: str              # "unseen" | "in_progress" | "done" | "skipped"
    tags: list[str]          # video-level tags
    segments: list[Segment]

@dataclass
class Project:
    source_folder: str
    output_folder: str
    created_at: str          # ISO 8601
    tag_vocabulary: list[str]  # union of all tags ever used (for autocomplete)
    videos: list[Video]
```

---

## 7. Metadata Output

### 7.1 `metadata.json`

```json
{
  "project": {
    "source_folder": "/data/raw_videos",
    "output_folder": "/data/clips",
    "created_at": "2026-04-06T10:00:00Z",
    "exported_at": "2026-04-06T14:32:11Z",
    "tag_vocabulary": ["outdoor", "indoor", "action", "static", "train", "val"]
  },
  "videos": [
    {
      "id": "a1b2c3d4-...",
      "filename": "session_01.mp4",
      "duration": 3612.4,
      "status": "done",
      "tags": ["outdoor", "daytime"],
      "segments": [
        {
          "id": "e5f6a7b8-...",
          "start": 120.500,
          "end": 145.200,
          "duration": 24.700,
          "tags": ["action", "train"],
          "notes": "",
          "output_path": "session_01/session_01_0120.500-0145.200.mp4"
        }
      ]
    },
    {
      "id": "...",
      "filename": "session_02.mp4",
      "status": "skipped",
      "tags": [],
      "segments": []
    }
  ]
}
```

### 7.2 `metadata.csv` (flat, one row per segment)

| segment_id | video_id | source_filename | start | end | duration | video_tags | segment_tags | output_path |
|---|---|---|---|---|---|---|---|---|
| e5f6... | a1b2... | session_01.mp4 | 120.5 | 145.2 | 24.7 | outdoor;daytime | action;train | session_01/session_01_... |

### 7.3 Output Folder Structure

```
output_folder/
├── metadata.json
├── metadata.csv
├── session_01/
│   ├── session_01_0120.500-0145.200.mp4
│   └── session_01_0310.000-0400.000.mp4
├── session_02/           ← (skipped, folder not created)
└── session_03/
    └── session_03_0045.000-0112.300.mp4
```

---

## 8. Keyboard Shortcuts

| Key | Action |
|---|---|
| `Space` | Play / Pause |
| `I` | Set In point (segment start) at current position |
| `O` | Set Out point (segment end) + confirm segment |
| `Escape` | Cancel current in-progress segment |
| `←` / `→` | Seek ±5 seconds |
| `Shift+←` / `Shift+→` | Seek ±1 second |
| `Ctrl+←` / `Ctrl+→` | Seek ±30 seconds |
| `1` | Speed 1x |
| `2` | Speed 1.5x |
| `3` | Speed 2x |
| `4` | Speed 4x |
| `5` | Speed 8x |
| `[` / `]` | Jump to previous / next segment boundary |
| `Delete` / `Backspace` | Delete selected segment |
| `N` | Next video in list |
| `P` | Previous video in list |
| `D` | Mark current video as Done |
| `S` | Mark current video as Skipped |
| `T` | Focus tag input for current video |
| `Ctrl+E` | Open Export Preview dialog |
| `Ctrl+Z` | Undo last segment add/delete |
| `+` / `=` | Zoom timeline in (around playhead) |
| `-` | Zoom timeline out |
| `Ctrl+0` | Reset timeline zoom to fit full video |
| Scroll wheel on timeline | Zoom in / out at cursor position |

---

## 9. Session Auto-Save

- State is saved to `<source_folder>/.videoann_session.json` after every mutation (add/remove segment, add tag, change video status)
- Save is debounced to 500 ms to avoid I/O on every keyframe
- On app launch with a folder that contains a session file: prompt "Resume previous session?" with summary (N videos done, M segments marked)
- Session file is never deleted automatically — user must explicitly "Clear Session" from the menu

---

## 10. Performance Considerations

| Concern | Mitigation |
|---|---|
| 1-hour video seek latency | mpv handles arbitrary seek in < 200 ms for most formats via keyframe index |
| Waveform computation for long videos | Background thread, progress shown in timeline placeholder; cached to `.cache/` so it runs once per video |
| Waveform zoom re-rendering | NumPy array linearly interpolated to pixel columns at render time; no FFmpeg re-extraction at any zoom level |
| UI freeze during export | FFmpeg subprocesses run in `QThread`; status bar shows progress; cancel is graceful (SIGTERM + partial file cleanup) |
| Large folder (100+ videos) | Video list is virtual (only loaded video is held in memory); others are metadata entries only |
| Timeline rendering at 60 fps | Waveform rendered to a `QPixmap` once on load; minimap rendered once per zoom change; only playhead and segment overlays are re-drawn per frame |
| mpv + Qt rendering conflict on macOS | Risk documented; deferred to implementation phase; fallback to `QMediaPlayer` if Metal/Qt bridge proves unstable |

---

## 11. Future Extensions (Out of Scope for v1)

### Review Stage (v2)
After all videos have been processed, a dedicated **Review Mode** lets the user audit their selections before committing to export:
- Enters a gallery view: each marked segment is shown as a thumbnail card with its duration and tags
- Cards can be played inline (looping), deleted, re-tagged, or merged with adjacent segments
- Segments can be reordered (drag-and-drop) to preview the final dataset composition
- A "coverage" panel shows tag distribution across all segments (useful for spotting class imbalance before export)
- Only after reviewing does the user trigger the Export Preview dialog

### Other v2+ Extensions
- **Motion heatmap on timeline**: per-frame pixel-difference signal displayed as a color lane; deferred because raw frame diff is dominated by camera shake/panning — needs global motion compensation (optical flow stabilization) to be useful; stabilized diff requires OpenCV or similar
- **VFR → CFR conversion**: in-app option to re-encode VFR source videos to a target frame rate before annotating; deferred because CFR input is assumed in v1
- Automatic scene-change detection to pre-populate candidate segments
- Thumbnail strip along timeline (keyframe thumbnails at variable density based on zoom level)
- Multi-video batch tag operations (apply a tag to all Done videos at once)
- Export preset profiles (codec, resolution, fps, output naming template)
- Integration with annotation tools (CVAT, Label Studio) via metadata compatibility
