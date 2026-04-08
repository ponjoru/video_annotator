# VideoAnnotator

A desktop tool for fast video segment annotation, aimed at building ML datasets from raw video footage.

Mark in/out points while watching, tag segments and whole videos, then batch-export all clips as re-encoded or losslessly re-muxed MP4 files alongside a structured metadata CSV/JSON.

## Features

- Frame-accurate segment marking (I/O keys) with undo support
- Per-video and per-segment tag vocabulary with autocomplete
- Timeline with waveform overlay, zoom, and drag-to-create segments
- Batch export via ffmpeg: re-encode (h264/VideoToolbox) or lossless re-mux
- Session auto-save — resume exactly where you left off
- Keyboard-driven workflow; mouse optional

## Requirements

| Dependency | Version | Notes |
|---|---|---|
| Python | ≥ 3.11 | |
| PySide6 | ≥ 6.7 | Qt6 bindings |
| numpy | ≥ 1.26 | Waveform rendering |
| mpv | any recent | Must be on `$PATH` or at `/opt/homebrew/bin/mpv` |
| ffmpeg + ffprobe | any recent | Must be on `$PATH` |

## Installation

### 1. Clone

```bash
git clone <repo-url>
cd video_manager
```

### 2. Create and activate a virtual environment

```bash
python3.11 -m venv venv
source venv/bin/activate
```

### 3. Install Python dependencies

```bash
pip install -e .                  # runtime only
pip install -r requirements-dev.txt  # + dev tools (pytest, ruff, mypy)
```

### 4. Install system dependencies

**macOS:**
```bash
brew install ffmpeg
# mpv is optional — only needed if you switch to the legacy external-window backend
brew install mpv
```

**Ubuntu / Debian:**
```bash
sudo apt install ffmpeg gstreamer1.0-plugins-base gstreamer1.0-libav
# GStreamer plugins are required for H.264/HEVC playback via Qt Multimedia
```

### 5. Run

```bash
python -m video_annotator
# or, after pip install -e .:
video-annotator
```

## Usage

1. **File → Open Folder…** (`Ctrl+O`) — select a folder containing `.mp4` / `.mov` / `.mkv` files
2. Double-click a video in the left panel to load it
3. Press **I** to set the in-point, **O** to set the out-point — a segment is created
4. Add tags in the right panel; press **T** to focus the tag input
5. Press **D** to mark the video done, **S** to skip it, **N/P** to navigate
6. **Ctrl+E** to open the Export dialog — choose output folder and export mode

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Space` | Play / pause |
| `I` | Set in-point |
| `O` | Set out-point (creates segment) |
| `Escape` | Cancel pending in-point |
| `←` / `→` | Seek ±5 s |
| `Shift+←/→` | Seek ±1 s |
| `Ctrl+←/→` | Seek ±30 s |
| `1`–`5` | Set speed (1×, 1.5×, 2×, 4×, 8×) |
| `N` / `P` | Next / previous video |
| `D` / `S` | Mark done / skipped |
| `T` | Focus tag input |
| `+` / `-` | Zoom timeline in / out |
| `Ctrl+0` | Fit timeline to full duration |
| `Ctrl+E` | Open export dialog |
| `Ctrl+Z` | Undo last segment operation |

> All shortcuts are suppressed automatically when a text input has focus.

## Development

```bash
make test          # run all tests
make test-unit     # unit tests only
make lint          # ruff check + format check
make format        # auto-format with ruff
make typecheck     # mypy strict
```

## Project structure

```
video_annotator/
  __main__.py               entry point
  settings.py               persistent settings (QSettings)
  undo.py                   undo stack + commands
  controllers/
    playback_controller.py  keyboard → mpv bridge + segment validation
    project_manager.py      project state mutations + session auto-save
    export_engine.py        ffmpeg export worker + metadata writer
    waveform_cache.py       background waveform extraction + disk cache
  models/
    project.py              Project / Video / Segment dataclasses
    session_store.py        JSON session persistence
  ui/
    main_window.py          top-level window + signal routing
    player_panel.py         mpv subprocess + IPC polling
    timeline_widget.py      ruler, segment band, waveform, minimap
    side_panel.py           status buttons + tag editors + segment list
    video_list_panel.py     left video list with status badges
    transport_bar.py        play/pause, speed, volume controls
    status_bar.py           idle stats + export progress
    toast_notification.py   transient overlay messages
    settings_panel.py       settings dialog
    export_preview_dialog.py export summary + output folder picker
  utils/
    ffprobe.py              video metadata parsing
    time_fmt.py             seconds → HH:MM:SS
tests/
  unit/                     fast, no ffmpeg required
  integration/              ExportEngine tests (require ffmpeg)
```
