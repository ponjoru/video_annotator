# DEV_UPD1 — Embedded Video Player

**Status:** Research complete · Architecture decided · Ready for implementation  
**Priority:** High — confirmed UX problem  
**Scope:** Replace separate mpv window with in-app video rendering; lay cross-platform foundation

---

## Context and Decision Record

### Problem

The current architecture launches mpv as a child subprocess with `--force-window=yes`, producing a separate OS window. This was a deliberate workaround — the Phase 1 spike found that in-process `python-mpv` caused `SIGBUS` on macOS with Qt6/Metal because libmpv could not attach its own `CAMetalLayer` to a Qt-managed `NSView`. The workaround is functional but ruins UX.

### Interview Findings (2026-04-08)

All open architectural questions were resolved through a product interview:

| Question | Answer | Architectural impact |
|---|---|---|
| Frame-accurate seeking required? | No — tool cuts segments, not frame-level annotation | Qt Multimedia's keyframe-boundary seek is acceptable |
| Video formats in scope? | Standard H.264 / HEVC only | AVFoundation (macOS) and GStreamer (Linux) both handle this natively |
| Frame-stepping needed? | No — another app handles per-frame annotation | `step_frame` workaround quality is irrelevant |
| UX pain level of separate window? | High — genuinely ruins UX | Embedding is the right priority |
| Linux port planned? | Yes, if UX is positive | Cross-platform abstraction must be designed now |

### Decision

**Primary backend: Qt Multimedia (`QMediaPlayer + QVideoWidget`).**

This is not a compromise — it is the correct choice for this use case. The only meaningful regression vs mpv (frame-accurate seeking) does not apply here. Qt Multimedia uses AVFoundation on macOS and GStreamer on Linux, both Metal-native, zero rendering conflicts, zero extra dependencies beyond PySide6.

mpv subprocess is retained as an optional fallback behind a settings flag, but is no longer the default.

---

## Architecture: Backend Abstraction

The core architectural decision is to introduce a thin `AbstractPlayerBackend` interface between `PlayerPanel` and the underlying media engine. This costs one extra file now and means the Linux port is a new backend, not a rewrite.

```
MainWindow
    └── PlayerPanel  (public API, never changes)
            └── AbstractPlayerBackend  (interface)
                    ├── QtMultimediaBackend   ← macOS + Linux, implement now
                    └── MpvSubprocessBackend  ← current code, keep as fallback
```

Everything above `PlayerPanel` — `PlaybackController`, `TimelineWidget`, `MainWindow`, all other panels, export engine, waveform cache — is already platform-agnostic and requires **zero changes** for the Linux port.

### Why not VLC?

VLC was considered for cross-platform embedding. On Linux it works fine, but on macOS it uses OpenGL internally and hits the same Metal conflict. It also adds `libvlc` + `python-vlc` as external dependencies with independent versioning. Qt Multimedia is already bundled with PySide6 and uses the right native backend per platform automatically. VLC offers no advantage over Qt Multimedia for standard H.264/HEVC footage.

### Cross-platform backend selection matrix

| Platform | Primary backend | Notes |
|---|---|---|
| macOS | `QtMultimediaBackend` | AVFoundation, Metal-native, hardware acceleration via VideoToolbox |
| Ubuntu / Debian | `QtMultimediaBackend` | GStreamer, needs `gstreamer1.0-libav` package for H.264 |
| Any (fallback) | `MpvSubprocessBackend` | Opt-in via settings; requires mpv binary on PATH |

---

## AbstractPlayerBackend Interface

```python
class AbstractPlayerBackend(QObject):
    # Signals — identical on all backends
    position_changed = Signal(float)   # seconds
    duration_known   = Signal(float)   # seconds, once per file load
    vfr_detected     = Signal(bool)
    speed_changed    = Signal(float)
    paused_changed   = Signal(bool)
    playback_ended   = Signal()

    # Methods — all backends must implement these
    def load(self, abs_path: str) -> None: ...
    def play_pause(self) -> None: ...
    def seek_absolute(self, seconds: float) -> None: ...
    def seek_relative(self, delta: float) -> None: ...
    def set_speed(self, speed: float) -> None: ...
    def set_volume(self, volume: int) -> None: ...
    def stop(self) -> None: ...

    @property
    def current_position(self) -> float: ...
```

`PlayerPanel` owns the active backend instance and delegates all calls to it. It never references `QMediaPlayer` or mpv directly — only the interface.

---

## Implementation Plan

### Step 1 — Introduce `AbstractPlayerBackend`

Create `video_annotator/ui/backends/__init__.py` and `abstract_backend.py`. Define the interface above as an abstract `QObject` subclass. No logic, no implementation — just the contract.

**Files created:** `video_annotator/ui/backends/abstract_backend.py`

---

### Step 2 — Extract `MpvSubprocessBackend`

Move the existing mpv subprocess + IPC code out of `PlayerPanel` into `video_annotator/ui/backends/mpv_subprocess_backend.py`. It implements `AbstractPlayerBackend` exactly. No behaviour changes — just a relocation.

`PlayerPanel` instantiates `MpvSubprocessBackend` temporarily during this step so the app still runs identically.

**Files created:** `video_annotator/ui/backends/mpv_subprocess_backend.py`  
**Files modified:** `video_annotator/ui/player_panel.py` (delegate to backend)

---

### Step 3 — Implement `QtMultimediaBackend`

Create `video_annotator/ui/backends/qt_multimedia_backend.py`. Implements `AbstractPlayerBackend` using `QMediaPlayer` + `QAudioOutput` + `QVideoWidget`.

Key implementation notes:

**Position:** `QMediaPlayer.positionChanged` emits milliseconds. Convert to seconds and cache:
```python
self._player.positionChanged.connect(lambda ms: self._emit_position(ms / 1000.0))
```
`current_position` property returns the cached value — no blocking IPC.

**Duration:** `QMediaPlayer.durationChanged` emits milliseconds. Emit `duration_known` once on first non-zero value.

**Pause state:** `QMediaPlayer.playbackStateChanged` → map `PlayingState`/`PausedState`/`StoppedState` to `paused_changed(bool)`.

**Speed:** `QMediaPlayer.setPlaybackRate(float)` — direct mapping, no changes.

**Volume:** `QAudioOutput.setVolume(volume / 100.0)` — `QMediaPlayer` delegates audio to a separate `QAudioOutput` object.

**VFR detection:** `QMediaPlayer` cannot detect VFR. Use the existing `ffprobe.py` utility synchronously on `load()` — probe runs in ~50 ms, acceptable at load time. Emit `vfr_detected(True/False)` from ffprobe result.

**`step_frame`:** Not implemented in this backend (not needed per product decision). Method exists on interface for compatibility; raises `NotImplementedError` or no-ops.

**Video surface:** The backend owns a `QVideoWidget` instance. `PlayerPanel` asks for it via `backend.video_widget` and embeds it into its layout.

**Files created:** `video_annotator/ui/backends/qt_multimedia_backend.py`

---

### Step 4 — Refactor `PlayerPanel`

`PlayerPanel` becomes a thin shell:
- Owns one `AbstractPlayerBackend` instance
- Embeds `backend.video_widget` (or a placeholder if the backend has no widget, e.g. subprocess backend showing a separate window)
- All public methods delegate to backend
- All signals are forwarded from backend to `PlayerPanel`'s own identically-named signals

The public API of `PlayerPanel` does not change. `MainWindow`, `PlaybackController`, and all tests remain untouched.

**Files modified:** `video_annotator/ui/player_panel.py`

---

### Step 5 — Backend selection via settings

Add `player_backend: str` to `SettingsManager` with default `"qt_multimedia"`. Values: `"qt_multimedia"` or `"mpv_subprocess"`.

`PlayerPanel.__init__` reads this setting and instantiates the appropriate backend:

```python
backend_name = settings.player_backend
if backend_name == "mpv_subprocess":
    from video_annotator.ui.backends.mpv_subprocess_backend import MpvSubprocessBackend
    self._backend = MpvSubprocessBackend(self)
else:
    from video_annotator.ui.backends.qt_multimedia_backend import QtMultimediaBackend
    self._backend = QtMultimediaBackend(self)
```

Expose this in `SettingsPanel` as a dropdown: "Embedded player (recommended)" / "External mpv window (legacy)".

**Files modified:** `video_annotator/settings.py`, `video_annotator/ui/settings_panel.py`

---

### Step 6 — Remove `--geometry` workaround

The `--geometry=1280x720-0+0` flag in `MpvSubprocessBackend` stays (it's still the correct behaviour for the mpv fallback), but the comment is updated to reflect it's now a fallback, not the primary path.

---

### Step 7 — Linux compatibility additions

No code changes needed at this step — the `QtMultimediaBackend` is already cross-platform. Two things to document and gate:

1. `pyproject.toml`: note that Linux requires `gstreamer1.0-plugins-base gstreamer1.0-libav` system packages for H.264 support
2. `README.md`: add Linux installation section:
   ```bash
   sudo apt install gstreamer1.0-plugins-base gstreamer1.0-libav
   pip install -e .
   ```

**Files modified:** `README.md`, `pyproject.toml` comments

---

### Step 8 — Tests

`PlaybackController` tests: **no changes** — they mock `PlayerPanel` at the interface level.

New test file `tests/unit/test_qt_multimedia_backend.py`:
- `load()` sets `QMediaPlayer.source()` to correct URL
- `position_changed` emitted when `positionChanged` fires
- `duration_known` emitted once on `durationChanged` with non-zero value
- `paused_changed(True)` emitted when state is `PausedState`
- `paused_changed(False)` emitted when state is `PlayingState`
- `set_speed()` calls `setPlaybackRate()` with correct value
- `vfr_detected` emitted from ffprobe result (mocked)

New test file `tests/unit/test_player_panel_backend_selection.py`:
- Setting `player_backend = "qt_multimedia"` → `QtMultimediaBackend` instantiated
- Setting `player_backend = "mpv_subprocess"` → `MpvSubprocessBackend` instantiated

---

## File Change Summary

| File | Change | Risk |
|---|---|---|
| `video_annotator/ui/backends/abstract_backend.py` | New — interface definition | None |
| `video_annotator/ui/backends/mpv_subprocess_backend.py` | New — extracted from `player_panel.py` | Low (pure relocation) |
| `video_annotator/ui/backends/qt_multimedia_backend.py` | New — Qt Multimedia implementation | Medium (new code) |
| `video_annotator/ui/player_panel.py` | Refactor — delegate to backend | Low (API unchanged) |
| `video_annotator/settings.py` | Add `player_backend` setting | Low |
| `video_annotator/ui/settings_panel.py` | Add backend selector dropdown | Low |
| `tests/unit/test_qt_multimedia_backend.py` | New tests | None |
| `tests/unit/test_player_panel_backend_selection.py` | New tests | None |
| `README.md` | Linux install section | None |

**Files that do not change:** `PlaybackController`, `TimelineWidget`, `MainWindow`, `SidePanel`, `VideoListPanel`, `ExportEngine`, `WaveformCache`, `TransportBar`, `StatusBar`, all existing tests.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `QVideoWidget` black frame on first load | Medium | Low | Call `show()` before `setSource()`; tested in unit tests |
| GStreamer missing on Ubuntu → silent failure | Medium | High | Detect at startup; show actionable error modal with install command |
| `AVFoundation` error on unusual macOS version | Low | Medium | Catch `QMediaPlayer.errorOccurred`; surface to user; offer fallback to mpv backend |
| Seek drift noticeable to users | Low | Low | Already accepted per product decision; document in README |
| `MpvSubprocessBackend` extraction introduces regression | Low | High | Existing test suite covers all mpv behaviour; run before and after |

---

## Sequencing Relative to Phase 12 (Packaging)

This update is independent of Phase 12 and can be developed in parallel. However:

- If Phase 12 (PyInstaller) is done first, the `.app` bundle will include the mpv subprocess backend. The Qt Multimedia backend does not require bundling any extra binaries — it uses OS-provided frameworks.
- If this update is done first, Phase 12 packaging becomes simpler: no mpv binary to bundle for the default path.

**Recommendation:** Implement UPD1 before Phase 12.

---

## Definition of Done

- [ ] `AbstractPlayerBackend` interface defined
- [ ] `MpvSubprocessBackend` extracted; all existing tests pass unchanged
- [ ] `QtMultimediaBackend` implemented; video plays in-app on macOS
- [ ] Backend selection works via `SettingsPanel` dropdown
- [ ] Default backend is `qt_multimedia`; separate mpv window gone
- [ ] VFR detection works via ffprobe in Qt Multimedia backend
- [ ] Linux install instructions in `README.md`
- [ ] New backend tests pass
- [ ] Full test suite green (200+ passing)
