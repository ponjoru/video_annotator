# VideoAnnotator — Implementation Plan

## Architectural Review Notes

Before the phase breakdown, several design gaps and risks identified during review:

### Gaps Addressed

1. **Inter-component communication** — The design lists components but does not specify how they communicate. This plan mandates a strict signal/slot boundary: backend objects (ProjectManager, SessionStore, ExportEngine) never import UI classes; UI panels emit signals that controller objects consume. Direct coupling is prohibited.

2. **Error handling strategy** — Not specified in DESIGN.md. This plan defines three tiers: (a) recoverable user errors shown as inline toasts, (b) partial-failure states (e.g., one FFmpeg clip fails mid-export) logged and reported in a post-export summary, (c) fatal errors (session file corrupt, mpv crash) shown in a modal with a safe-exit option.

3. **Session file integrity** — The design does not handle a corrupted `.videoann_session.json`. SessionStore will write to a `.tmp` file and atomically rename it on every save, ensuring the previous valid state is never destroyed.

4. **Thread safety** — WaveformCache, ExportEngine, and any ffprobe calls run in worker threads. All mutations to the in-memory Project state must happen on the main thread, dispatched via `QMetaObject.invokeMethod` or Qt signal delivery.

5. **mpv macOS embedding risk** — Flagged in DESIGN.md as deferred. This plan front-loads a spike (Phase 1) to validate the mpv ↔ Qt Metal bridge before any other UI work is committed. The fallback path (QMediaPlayer) is specified but not built unless the spike fails.

6. **Startup sequence** — Not detailed in DESIGN.md. Section "Application Startup Sequence" below specifies the exact initialization order.

7. **Testability** — No testing strategy in DESIGN.md. Backend components (ProjectManager, SessionStore, MetadataWriter, ExportEngine logic) are designed for unit testing without a running Qt event loop. UI-level smoke tests use `pytest-qt`.

### Design Decisions Locked In

- **No global mutable state.** `Project` is owned exclusively by `ProjectManager`; every other component receives read-only snapshots or communicates through signals.
- **Segment IDs are UUID4** generated at creation time, never reused, never reassigned.
- **Waveform extraction is always mono, 4 kHz, f32 normalized** (not s16le as written in DESIGN.md — f32 avoids a normalization step at render time).
- **Export is idempotent and stateless** — ExportEngine receives a snapshot of the project at export time; mutations during export (which are blocked anyway) cannot corrupt the export run.
- **Relative paths in session** — All segment/video references stored relative to `source_folder`; `abs_path` is reconstructed on load and validated, never persisted.

---

## Application Startup Sequence

```
main()
  └─ QApplication created
  └─ SettingsManager loaded from ~/Library/Preferences/
  └─ MainWindow constructed (blank state)
       ├─ VideoListPanel (empty)
       ├─ PlayerPanel (blank canvas, mpv not yet loaded)
       ├─ SidePanel (disabled)
       └─ StatusBar ("No folder open")
  └─ MainWindow shown
  └─ If sys.argv contains a folder path → auto-open that folder
  └─ Otherwise → show "Open Folder…" prompt (non-blocking, via file dialog)

open_folder(path):
  └─ ProjectManager.open_folder(path)
       └─ Scan directory for video files (no extension filter — ffprobe validates)
       └─ Check for existing .videoann_session.json
            ├─ Found → show RestoreSessionDialog (N videos, M segments)
            │    ├─ Restore → SessionStore.load() → ProjectManager populates state
            │    └─ Start Fresh → ProjectManager.new_project(path)
            └─ Not found → ProjectManager.new_project(path)
  └─ VideoListPanel.refresh()
  └─ StatusBar.set_idle_stats()
```

---

## Phase 0 — Project Scaffolding

**Goal:** Runnable skeleton with correct package structure, dependency pinning, and CI baseline. No features.

### Tasks

- [ ] Create top-level package layout:
  ```
  video_annotator/
    __main__.py          # entry point: python -m video_annotator
    app.py               # QApplication bootstrap
    main_window.py
    models/
      project.py         # dataclasses: Project, Video, Segment
      session_store.py
    controllers/
      project_manager.py
      playback_controller.py
      export_engine.py
      waveform_cache.py
    ui/
      video_list_panel.py
      player_panel.py
      transport_bar.py
      timeline_widget.py
      side_panel.py
      tag_editor.py
      segment_list.py
      export_preview_dialog.py
      settings_panel.py
      status_bar.py
      dialogs/
        restore_session_dialog.py
        relink_dialog.py
    utils/
      ffprobe.py         # thin wrapper returning typed dicts
      time_fmt.py        # seconds ↔ HH:MM:SS.mmm
      hash_file.py       # (path, size, mtime) → hex key for cache
    settings.py          # SettingsManager (QSettings wrapper)
    undo.py              # UndoStack
  tests/
    unit/
    integration/
  ```

- [ ] `pyproject.toml` with pinned dependencies:
  - `PySide6 >= 6.7`
  - `python-mpv >= 1.0.6`
  - `numpy >= 1.26`
  - `pytest`, `pytest-qt` (dev)

- [ ] Verify `python-mpv` + PySide6 co-exist on the target macOS version (14+). Document result.

- [ ] Makefile targets: `make run`, `make test`, `make lint`, `make bundle`

- [ ] Pre-commit hooks: `ruff` (lint + format), `mypy --strict` on `models/` and `controllers/`

**Exit criterion:** `python -m video_annotator` opens a blank window without crashing.

---

## Phase 1 — mpv Integration Spike ✅ COMPLETE

**Goal:** Prove that libmpv can render video inside a PySide6 widget on macOS.

### Findings (2026-04-07)

**Embedding strategy: mpv subprocess + Unix socket IPC — CONFIRMED WORKING.**

All in-process python-mpv approaches failed on macOS with Qt 6 Metal:
- `mpv.MPV(wid=winId(), vo='libmpv')` → SIGBUS: Qt6 NSViews are Metal-backed; mpv
  cannot attach its own CAMetalLayer on top of Qt's existing layer.
- `MpvRenderContext` (OpenGL FBO path) → investigated but not needed; subprocess is simpler
  and avoids all shared-address-space conflicts.

**Chosen architecture:**
- `PlayerPanel` launches `/opt/homebrew/bin/mpv` as a child process with
  `--force-window=yes --input-ipc-server=<socket>`.
- All commands (play, pause, seek, speed, get position) go via Unix domain socket JSON IPC.
- Position is polled at 10 Hz by a `QTimer` that calls `get_property time-pos` per tick.
- `python-mpv` dependency is no longer needed at runtime.

### Tasks

- [x] Create `spike/mpv_embed_test.py` — interactive CLI spike (seek/speed/pause validated)
- [x] `wid=` in-process approach investigated → SIGBUS on macOS Qt6 Metal, abandoned
- [x] Subprocess + IPC approach confirmed working (mpv 0.41.0, brew, macOS 14+)
- [x] Position polling at 10 Hz via QTimer + IPC get_property implemented
- [x] `PlayerPanel` fully implemented with subprocess backend
- [x] `ffprobe.probe()` fully implemented
- [x] `check_videotoolbox_available()` returns True on this machine
- [x] `__main__.py` cleaned up (no locale/DYLD workarounds needed)

**Exit criterion:** ✅ PlayerPanel smoke test passes; 36 tests pass.
Manual validation: `python spike/mpv_embed_test.py <video_file>` — type `p` to play.

---

## Phase 2 — Data Model and Session Persistence

**Goal:** The entire data layer works correctly and is fully tested before any UI depends on it.

### 2.1 Data Model (`models/project.py`)

- [ ] Implement `Segment`, `Video`, `Project` dataclasses exactly as specified in DESIGN.md §6
- [ ] Add `Video.relative_path: str` — path relative to `source_folder`; `abs_path` is a computed property, not stored
- [ ] Add `Project.version: int = 1` for future migration support
- [ ] Implement `to_dict()` / `from_dict()` classmethods on each dataclass (no third-party serialization library)
- [ ] Unit tests: round-trip serialization, edge cases (empty segments, unicode filenames, path separators on macOS)

### 2.2 SessionStore (`controllers/session_store.py`)

- [ ] `save(project, folder)`:
  - Serialize to JSON
  - Write to `<folder>/.videoann_session.json.tmp`
  - `os.replace()` → atomic rename to `.videoann_session.json`
  - Raises `SessionSaveError` on permission/disk-full failures (caller shows toast)

- [ ] `load(folder) -> Project | None`:
  - Returns `None` if no session file found
  - Raises `SessionCorruptError` if JSON is malformed or schema version mismatches
  - On load: reconstruct `abs_path` for each video by joining `source_folder + relative_path`; flag as `status="missing"` if the file does not exist on disk

- [ ] `migrate(raw_dict) -> dict` stub for future schema upgrades (v1 → v2)

- [ ] Unit tests: save + load round-trip, atomic write verified (old file survives if tmp write fails), missing-file detection, corrupt JSON handling

### 2.3 ProjectManager (`controllers/project_manager.py`)

- [ ] Owns one `Project` instance at a time
- [ ] `open_folder(path: str) -> OpenFolderResult` — scans directory, returns candidate session info without committing state
- [ ] `restore_session(folder)` / `new_project(folder)` — commits state
- [ ] Mutating methods (all trigger debounced auto-save):
  - `add_segment(video_id, start, end) -> Segment`
  - `delete_segment(video_id, segment_id)`
  - `add_video_tag(video_id, tag)` / `remove_video_tag(video_id, tag)`
  - `add_segment_tag(video_id, segment_id, tag)` / `remove_segment_tag(...)`
  - `rename_tag(old: str, new: str)` — atomic across all videos and segments
  - `set_video_status(video_id, status)`
  - `set_segment_notes(video_id, segment_id, notes)`
- [ ] Auto-save: debounce timer (500 ms), calls `SessionStore.save()`; timer resets on each mutation
- [ ] Qt signals emitted after each mutation:
  - `project_changed` (catch-all for VideoListPanel refresh)
  - `video_changed(video_id)`
  - `segment_added(video_id, segment_id)`
  - `segment_deleted(video_id, segment_id)`
  - `tag_vocabulary_changed(vocabulary: list[str])`

- [ ] Unit tests: all mutating methods, tag rename propagation, debounce timer behavior

---

## Phase 3 — Video List Panel and Main Window Shell

**Goal:** The left panel is functional. User can open a folder, see the video list, and select a video (even though the player does nothing yet).

### 3.1 MainWindow (`main_window.py`)

- [ ] Three-column layout: `VideoListPanel` | `PlayerPanel` | `SidePanel`
- [ ] `StatusBar` docked at bottom
- [ ] Menu bar: File → Open Folder, File → Clear Session, Edit → Undo, View → Settings, Help
- [ ] Keyboard shortcut routing: install global `QShortcut` objects for all shortcuts in DESIGN.md §8; shortcuts are disabled when a text input widget has focus (tag editor, notes field)
- [ ] `closeEvent`: flush pending auto-save synchronously before exit

### 3.2 VideoListPanel (`ui/video_list_panel.py`)

- [ ] `QListWidget` subclass (virtual scrolling via `QAbstractItemModel` if > 500 videos; use simple `QListWidget` for v1)
- [ ] Each item renders: filename, status badge (color dot), VFR badge (yellow), Missing badge (red), progress dots (N segments)
- [ ] Progress bar at the bottom: done + skipped / total
- [ ] Double-click or Enter → emit `video_selected(video_id)` signal
- [ ] Status badge color map: Unseen=grey, In Progress=blue, Done=green, Skipped=amber, Missing=red
- [ ] Connects to `ProjectManager.project_changed` to refresh
- [ ] "Relink missing files…" button appears only when ≥1 video has Missing status

### 3.3 RelinkDialog (`ui/dialogs/relink_dialog.py`)

- [ ] Table: original filename | current status | [Browse…] button
- [ ] Browse opens a native file picker filtered to the original filename
- [ ] On confirm: calls `ProjectManager.relink_video(video_id, new_abs_path)` for each relinked entry

**Exit criterion:** Open a folder, see the video list populate, click a video, see its name reflected in StatusBar.

---

## Phase 4 — Player Panel and Transport Bar ✅ COMPLETE

**Goal:** Video plays, seeks, speed changes work. Position is reported correctly.

### Findings (2026-04-07)

PlayerPanel, TransportBar, and PlaybackController were substantially implemented during
Phases 1–3. Phase 4 completed the remaining wiring and ffprobe parsing.

### Tasks

- [x] `PlayerPanel` — subprocess IPC, load_video, seek, speed, volume (Phase 1)
- [x] `PlayerPanel.paused_changed` signal — polls `pause` property at 10 Hz; emitted only on state change
- [x] `TransportBar` — timestamp, speed buttons, play/pause, volume slider (Phase 3)
- [x] `TransportBar.set_playing()` wired to `PlayerPanel.paused_changed` via MainWindow
- [x] `PlaybackController` — in/out point, validation, segment creation (Phase 3)
- [x] `ffprobe.probe()` — parses streams/format JSON; returns `VideoInfo` dataclass
- [x] `ffprobe.check_videotoolbox_available()` — returns True on this machine (Phase 1)
- [x] `TimelineWidget.set_duration()` — updates duration without resetting segments
- [x] `MainWindow._on_duration_known()` — updates timeline + persists via `set_video_info`
- [x] `MainWindow._on_vfr_detected()` — persists VFR flag, refreshes video list badge
- [x] Unit tests: all 5 ffprobe tests pass (2 previously skipped, now active)

**Exit criterion:** ✅ 67 passed, 7 skipped (export engine integration tests, Phase 8).
Manual: select a video → plays; timestamp updates live; speed/seek shortcuts work.

---

## Phase 5 — Timeline Widget ✅ COMPLETE

**Goal:** Full interactive timeline with ruler, segment band, waveform, and minimap.

### Findings (2026-04-07)

### Tasks

- [x] Coordinate system: `time_to_x`, `x_to_time`, `_minimap_time_to_x`, `_minimap_x_to_time`
- [x] Zoom state `(visible_start, visible_end)` with `_zoom_around(anchor, factor)` clamped to `[1s, duration]`
- [x] Zoom: scroll wheel around cursor, `+`/`-` around playhead, `Ctrl+0` fit full video
- [x] Ruler lane: major tick marks + labels (`_fmt_tick`), minor ticks; `_pick_tick_interval` targets 5–15 major ticks
- [x] Segment band: colored rects, red diagonal-stripe fill for overlapping pairs; semitransparent trim handles at each edge
- [x] Click ruler/waveform → emit `seek_requested`; click segment body → emit `segment_selected` + `seek_requested`
- [x] Trim drag: `SizeHorCursor` on hover over handle; provisional visual during drag; `segment_trim_requested` on release
- [x] Rubber-band drag in empty segment band → `in_point_drag_set(start)` + `out_point_drag_set(end)` → creates segment via `PlaybackController.set_segment_from_range()`
- [x] In-point dashed line marker (set via `PlaybackController.in_point_set` signal)
- [x] Waveform lane: grey hatched placeholder; `_render_waveform_pixmap()` maps numpy array to per-pixel amplitude columns; pixmap cached, invalidated on zoom change
- [x] Waveform lane collapses (`height=0`) when `set_no_audio()` called
- [x] Minimap strip: segment overview + visible-window highlight rect; click/drag pans main view
- [x] `set_duration()` — live update without resetting segments (used after player reports real duration)
- [x] `ProjectManager.trim_segment()` + `_trim_segment_direct()` + `TrimSegmentCommand` with undo
- [x] `PlaybackController.set_segment_from_range()` — create segment from explicit time range
- [x] MainWindow: `_on_segment_trim`, `_on_timeline_in_point_drag`, `_on_timeline_rubber_band` handlers wired
- [x] Unit tests: 3 new trim tests (undo restore, bounds update, re-sort)

**Exit criterion:** ✅ 70 passed, 7 skipped. Timeline renders correctly, segments draw after being added, waveform placeholder shows, minimap panning works, trim + rubber-band create/modify segments.

---

## Phase 6 — Side Panel and Tag System ✅ COMPLETE

**Goal:** Tags, segment list, undo, and status buttons are fully wired and tested.

### Findings (2026-04-07)

Most components were scaffolded in earlier phases. Phase 6 completed the remaining wiring and fixes.

### Tasks

- [x] `TagEditor` — chip strip, autocomplete (contains), comma confirmation, duplicate rejection (Phases 3/6)
- [x] `TagChip._build_ui` bug fixed — `display = tag` was a `NameError`; corrected to `self._tag`
- [x] `TagEditor` Escape key — `_TagLineEdit` subclass clears field, clears focus, restores top-level window focus so shortcuts are live again immediately
- [x] `SegmentList.set_vocabulary()` — forwards vocabulary to all existing `SegmentRow` tag editors; also applied when new rows are created in `set_segments()`
- [x] `SidePanel.update_video_status()` — Done/Skipped buttons are checkable with green/amber `:checked` stylesheet; neither checked for unseen/in_progress
- [x] `SidePanel.load_video()` — calls `update_video_status(video.status)`, `set_vocabulary()` on both video tag editor and segment list
- [x] `SidePanel.update_vocabulary()` — now propagates to `segment_list.set_vocabulary()` as well as video-level tag editor
- [x] `MainWindow` — wired `segment_seek_requested → playback.seek_absolute` (was missing)
- [x] `MainWindow._on_video_changed()` — now calls `side.update_video_status(video.status)` on status change
- [x] `VideoListPanel` — replaced `_make_item_label()` text hack with a `QStyledItemDelegate` that paints a solid colored status dot to the left of the filename; VFR tag and segment count in display text; tooltips for missing files and VFR sources
- [x] `UndoStack`, `AddSegmentCommand`, `DeleteSegmentCommand`, `TrimSegmentCommand` — fully implemented (Phases 3/5)
- [x] 24 new unit tests in `test_ui_phase6.py` — TagEditor, SegmentList, SidePanel, VideoListPanel all covered

**Exit criterion:** ✅ 94 passed, 7 skipped. Tags round-trip through session, segment list updates live, undo cycles work, status buttons reflect video state, segment seek from list works.

---

## Phase 7 — Waveform Cache ✅ COMPLETE

**Goal:** Background audio extraction with disk cache; waveform appears in timeline after load.

### Tasks

- [x] `_ExtractWorker.run()` — ffprobe audio-stream check (fast, no decode); ffmpeg f32le pipe; reads stdout in `_CHUNK_SIZE=65536` byte chunks; checks `_cancelled` between chunks; normalizes peak to ±1.0 (zero-signal safe); saves `.npy`; emits `ready / no_audio / failed`
- [x] `WaveformCache.request()` — cancels prior worker; computes `hash_file()` key; cache hit loads `.npy` synchronously and emits `ready`; corrupt cache file deleted and re-extracted; cache miss spawns `_ExtractWorker`
- [x] `WaveformCache.cancel_current()` — terminates subprocess, waits up to 2 s for thread exit
- [x] `WaveformCache._on_worker_finished()` — clears `_active_worker` reference when thread exits (prevents stale reference)
- [x] Cache key: `sha1(abs_path | file_size | mtime)[:16]`; cache directory `<source_folder>/.cache/` created on `set_source_folder()`
- [x] `MainWindow._load_video()` — removed `NotImplementedError` guard; waveform request is now live
- [x] 10 unit tests in `test_waveform_cache.py` — ready emit, cache file written, no-audio, ffmpeg failure, ffmpeg not found, all-zeros normalization, cache hit, cache miss starts worker, second request cancels first, corrupt cache triggers re-extraction

**Exit criterion:** ✅ 104 passed, 7 skipped. Waveform extraction runs in background; cache hit is instant on second load; video switch correctly cancels in-progress extraction.

---

## Phase 8 — Export Engine and Preview Dialog ✅ COMPLETE

### 8.1 ExportPreviewDialog (`ui/export_preview_dialog.py`)

- [ ] Output folder row: path label + "Change…" button → `QFileDialog.getExistingDirectory`; path saved to `SettingsManager` (not session)
- [ ] Path validation on change: check writable, estimate free space (rough: total segment duration × assumed bitrate); warn if insufficient
- [ ] Summary table rows:
  - Source videos with segments: N
  - Total clips: M
  - Total output duration: HH:MM:SS
  - Estimated size: X–Y GB (range: CRF min/max bitrate)
  - Export mode: Re-encode (h264, CRF 18) / Lossless re-mux
  - Warnings: VFR sources listed by name, overlapping segments count, files that would be overwritten
- [ ] Export mode toggle: Re-encode | Lossless re-mux (overrides Settings for this run)
- [ ] Export / Cancel buttons
- [ ] On Export: closes dialog, passes `ExportSpec` to `ExportEngine.start()`

### 8.2 ExportEngine (`controllers/export_engine.py`)

- [ ] Receives an `ExportSpec` (snapshot of project state) — not a live reference to `ProjectManager`
- [ ] Runs in `QThread`; main thread UI goes read-only for duration
- [ ] Output folder collision resolution:
  - Build a dict of `stem → [video_ids]`
  - If any stem appears more than once: append `_<6-char hash>` to second+ occurrences
- [ ] Per clip: build `ffmpeg` command, run as `subprocess.Popen`, read stderr for progress, send SIGTERM on cancel
- [ ] ffmpeg command — re-encode:
  ```
  ffmpeg -y -i <src> -ss <start> -to <end>
    -c:v h264_videotoolbox (if available) else libx264 -preset fast -crf 18
    -c:a aac -b:a 128k
    <output>
  ```
  Note: `-ss` placed after `-i` for frame-accurate decode (slower but correct); `-ss` before `-i` (fast seek) is not used by default because it can drift by up to a keyframe interval.
- [ ] ffmpeg command — lossless re-mux:
  ```
  ffmpeg -y -ss <start> -to <end> -i <src> -c copy <output>
  ```
  (fast seek before `-i` accepted here since re-mux already implies keyframe boundaries)
- [ ] VideoToolbox availability: checked once at startup via `ffmpeg -encoders | grep videotoolbox`; result stored in `SettingsManager`
- [ ] Progress signals: `clip_started(index, total, clip_name)`, `clip_done(index)`, `export_failed(clip_name, reason)`, `export_complete(total_written, output_folder)`
- [ ] On cancel: SIGTERM active subprocess, delete partial output file, emit `export_cancelled`
- [ ] On clip failure (non-zero exit): log error, continue remaining clips, collect failures for post-export report
- [ ] After all clips: call `MetadataWriter.write(spec, output_folder)`

### 8.3 MetadataWriter (`controllers/export_engine.py` or separate file)

- [ ] `write(spec, output_folder)`:
  - Writes `metadata.json` per schema in DESIGN.md §7.1
  - Writes `metadata.csv` per schema in DESIGN.md §7.2 (semicolon-separated tags within cells)
  - `exported_at` field uses UTC ISO 8601 timestamp
- [x] 23 integration tests (all passing): `ExportSpec.build` (8), `_build_ffmpeg_cmd` (6), `MetadataWriter` (9); 2 real-ffmpeg tests kept as skips for Phase 11

**Exit criterion:** ✅ 127 passed, 2 skipped. Export preview shows accurate stats; spec built with correct paths and collision-free stems; metadata files written with correct structure; partial file cleaned on cancel.

---

## Phase 9 — Settings Panel ✅ COMPLETE

**Goal:** Persistent application preferences with a modal settings dialog.

### Tasks

- [x] `SettingsManager` — typed `QSettings` wrapper; all fields with defaults; `videotoolbox_available` runtime-only (not persisted); `sync()` for explicit flush
- [x] `SettingsPanel` — modal `QDialog`; two `QGroupBox` sections (Segment Validation, Export Defaults); all fields load from `SettingsManager` in `_load_values()`; OK calls `_apply_and_accept()` which writes all fields then calls `sync()`; Cancel calls `reject()` without writing — original values preserved
- [x] 18 unit tests: load (8), apply/persist (6), cancel-reverts (2), spinbox bounds (2)

**Exit criterion:** ✅ 145 passed, 2 skipped. All settings fields load correctly; OK persists; Cancel reverts; spinbox bounds enforced.

---

## Phase 10 — Status Bar ✓ COMPLETE

### StatusBar (`ui/status_bar.py`)

- [x] Idle state: `"{filename}" — {N} done, {M} skipped, {K} segments marked`
- [x] Export state: `Exporting clip {i} / {total} — {clip_name}  [progress bar]  {pct}%  [Cancel]`
- [x] Progress bar: `QProgressBar` in determinate mode; updated by `ExportEngine.clip_done` signal
- [x] Cancel button: calls `ExportEngine.request_cancel()`
- [x] Completion state (5 s auto-clear): `Export complete — {N} clips written to {path}`
- [x] Failure state: `Export complete with {N} errors — see export_log.txt` (log written alongside metadata)
- [x] Tests: `tests/unit/test_status_bar.py` — 33 tests, all passing

---

## Phase 11 — Integration, Polish, and Hardening ✓ COMPLETE

### Integration

- [x] Wire all signals end-to-end: video selection → player load → waveform extraction → timeline render → segment marking → side panel update → session auto-save
- [x] Keyboard shortcuts suppressed when a QLineEdit / QTextEdit / QPlainTextEdit has focus (`_make_guarded_slot` wrapper); Ctrl shortcuts always active

### Validation and Toast System

- [x] `ToastNotification` widget (`ui/toast_notification.py`): overlay at bottom-centre of PlayerPanel, auto-hides after 3 s; `show_message()` restarts timer
- [x] Validation gates in `PlaybackController.set_out_point` and `set_segment_from_range`:
  - `end − start < min_segment_duration` → `validation_failed` signal → toast
  - `end − start > max_segment_duration_warning` → `confirm_long_segment_requested` signal → modal "Add anyway?" in MainWindow; `confirm_pending_segment()` / `cancel_pending_segment()` on result
- [x] Tests: `tests/unit/test_phase11.py` — 22 tests, all passing

### Edge Cases to Exercise

- [ ] Video with no audio track: waveform lane collapses, no extraction attempted
- [ ] Video file deleted while app is open: next access raises Missing badge; no crash
- [ ] Source folder moved mid-session: on next launch, all videos become Missing; Relink dialog offered
- [ ] Two videos with the same filename stem: export collision resolution tested with 6-char hash suffix
- [ ] Export to a read-only folder: validation in ExportPreviewDialog shows error; export button disabled
- [ ] VFR video: badge shown, tooltip displayed; export proceeds without special handling (v1)
- [ ] Very long tag strings: UI chips truncate with ellipsis; full text in tooltip
- [ ] Session JSON manually edited and corrupted: `SessionCorruptError` shown as modal; option to start fresh

### Performance Verification

- [ ] Open a folder with 50+ videos; list renders without delay
- [ ] Load a 1-hour video; timeline renders within 2 s; waveform extraction begins in background
- [ ] Seek at 8x speed: no UI freeze (mpv handles in its own thread)
- [ ] Timeline zoom from full-fit to max zoom: no stutter (pixmap pre-rendered)
- [ ] Export 20 clips: StatusBar updates per clip; cancel terminates within 2 s

---

## Phase 12 — Packaging

- [ ] `PyInstaller` spec file: bundle libmpv, ffmpeg, ffprobe binaries into `.app`
- [ ] `Info.plist`: app name, bundle ID (`com.videoannotator.app`), minimum macOS 14
- [ ] Verify packaged `.app` opens and plays video without any external dependencies
- [ ] Notarization: if distributing outside direct delivery — sign with Developer ID + notarize; defer if internal tool only
- [ ] `make bundle` produces `dist/VideoAnnotator.app` and a `.dmg` via `create-dmg`

---

## Dependency Graph (Phase Ordering)

```
Phase 0 (Scaffolding)
  └─ Phase 1 (mpv Spike) ← must pass before Phase 4
  └─ Phase 2 (Data Model + Session)
       └─ Phase 3 (VideoListPanel + MainWindow shell)
            └─ Phase 4 (PlayerPanel + TransportBar)     ← requires Phase 1 result
                 └─ Phase 5 (TimelineWidget)
                      └─ Phase 6 (SidePanel + Tags + Undo)
                           └─ Phase 7 (WaveformCache)   ← can start after Phase 4
                           └─ Phase 8 (Export)
                                └─ Phase 9 (Settings)
                                     └─ Phase 10 (StatusBar)
                                          └─ Phase 11 (Integration + Polish)
                                               └─ Phase 12 (Packaging)
```

Phases 7 (WaveformCache) and 8 (Export) can be developed in parallel after Phase 6, as they do not depend on each other.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| mpv ↔ Metal/Qt bridge broken on macOS | Medium | High | Phase 1 spike; QMediaPlayer fallback documented |
| Frame-accurate seek slower than expected at high speed | Low | Medium | Benchmark in spike; fast seek (`-ss` before `-i`) available as opt-in |
| WaveformCache extraction stalls UI thread via signal timing | Low | Medium | All numpy work done in QThread; signal delivery is the only cross-thread call |
| Session file corruption on power loss during save | Low | High | Atomic write (tmp + os.replace) mitigates; documented |
| FFmpeg not found in packaged app | Low | High | Bundled via PyInstaller; runtime check on startup with actionable error modal |
| PySide6 6.7 API changes vs 6.6 | Low | Low | Pin exact version in pyproject.toml |
| VFR video produces drift in re-encode output | Medium | Low | Surfaced as badge + tooltip; accepted in v1 |
