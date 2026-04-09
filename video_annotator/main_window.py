"""MainWindow: top-level application window.

Layout:
  ┌──────────────┬──────────────────────────────┬──────────────┐
  │ VideoList    │ PlayerPanel                  │ SidePanel    │
  │ Panel        │ TransportBar                 │              │
  │              │ TimelineWidget               │              │
  └──────────────┴──────────────────────────────┴──────────────┘
  └─────────────────── StatusBar ─────────────────────────────┘

Signal routing rules:
  - UI panels emit intent signals (e.g., video_selected, tag_added).
  - MainWindow connects those signals to the appropriate controller method.
  - Controllers mutate state and emit result signals.
  - MainWindow connects result signals back to UI update methods.
  - No panel talks directly to another panel or to a controller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from video_annotator.controllers.export_engine import ExportEngine, ExportSpec
from video_annotator.controllers.playback_controller import PlaybackController
from video_annotator.controllers.project_manager import ProjectManager
from video_annotator.controllers.waveform_cache import WaveformCache
from video_annotator.models.session_store import SessionCorruptError
from video_annotator.ui.dialogs.relink_dialog import RelinkDialog
from video_annotator.ui.dialogs.restore_session_dialog import RestoreSessionDialog
from video_annotator.ui.export_preview_dialog import ExportPreviewDialog
from video_annotator.ui.player_panel import PlayerPanel, SPEED_LEVELS
from video_annotator.ui.settings_panel import SettingsPanel
from video_annotator.ui.side_panel import SidePanel
from video_annotator.ui.status_bar import StatusBar
from video_annotator.ui.timeline_widget import TimelineWidget
from video_annotator.ui.toast_notification import ToastNotification
from video_annotator.ui.transport_bar import TransportBar
from video_annotator.ui.video_list_panel import VideoListPanel
from video_annotator.undo import UndoStack

if TYPE_CHECKING:
    from video_annotator.settings import SettingsManager


class MainWindow(QMainWindow):

    def __init__(self, settings: "SettingsManager", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("VideoAnnotator")
        self.setMinimumSize(QSize(1200, 700))
        self._settings = settings

        # --- Controllers ---
        self._undo_stack = UndoStack(self)
        self._manager = ProjectManager(settings, self)
        self._manager._undo_stack = self._undo_stack
        self._waveform_cache = WaveformCache(self)
        self._export_engine = ExportEngine(self)
        self._current_video_id: str | None = None
        self._pending_duration: float = 0.0
        self._rubber_drag_start: float | None = None

        # --- UI ---
        self._build_ui()
        self._build_menu()
        self._install_shortcuts()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: video list
        self._video_list = VideoListPanel()
        splitter.addWidget(self._video_list)

        # Center: player + transport + timeline
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        self._player = PlayerPanel(settings=self._settings)
        self._transport = TransportBar()
        self._timeline = TimelineWidget()
        center_layout.addWidget(self._player, stretch=4)
        center_layout.addWidget(self._transport)
        center_layout.addWidget(self._timeline, stretch=2)
        splitter.addWidget(center)

        # Right: side panel
        self._side = SidePanel()
        splitter.addWidget(self._side)

        splitter.setStretchFactor(0, 1)   # video list
        splitter.setStretchFactor(1, 4)   # player area
        splitter.setStretchFactor(2, 1)   # side panel

        root.addWidget(splitter, stretch=1)

        # Status bar
        self._status = StatusBar()
        self.setStatusBar(None)   # disable Qt's built-in status bar
        root.addWidget(self._status)

        # PlaybackController needs references to player and manager
        self._playback = PlaybackController(self._player, self._manager, self._settings, self)

        # Toast overlay sits on top of the player panel
        self._toast = ToastNotification(self._player)

    def _build_menu(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("File")
        open_action = QAction("Open Folder…", self)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        open_action.triggered.connect(self._prompt_open_folder)
        file_menu.addAction(open_action)

        clear_action = QAction("Clear Session", self)
        clear_action.triggered.connect(self._clear_session)
        file_menu.addAction(clear_action)

        edit_menu = menu.addMenu("Edit")
        undo_action = QAction("Undo", self)
        undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        undo_action.triggered.connect(self._undo_stack.undo)
        edit_menu.addAction(undo_action)
        self._undo_stack.stack_changed.connect(undo_action.setEnabled)
        undo_action.setEnabled(False)

        view_menu = menu.addMenu("View")
        settings_action = QAction("Settings…", self)
        settings_action.triggered.connect(self._open_settings)
        view_menu.addAction(settings_action)

    def _install_shortcuts(self) -> None:
        """Install global QShortcuts. These are active unless a text widget has focus."""
        # Shortcuts suppressed when a text-input widget has focus (tagged as text-only).
        text_suppressed: list[tuple[str, object]] = [
            ("Space",         self._playback.play_pause),
            ("I",             self._playback.set_in_point),
            ("O",             self._playback.set_out_point),
            ("Escape",        self._playback.cancel_segment),
            ("Left",          lambda: self._playback.seek_relative(-5)),
            ("Right",         lambda: self._playback.seek_relative(5)),
            ("Shift+Left",    lambda: self._playback.seek_relative(-1)),
            ("Shift+Right",   lambda: self._playback.seek_relative(1)),
            ("Ctrl+Left",     lambda: self._playback.seek_relative(-30)),
            ("Ctrl+Right",    lambda: self._playback.seek_relative(30)),
            ("1",             lambda: self._playback.set_speed(1.0)),
            ("2",             lambda: self._playback.set_speed(1.5)),
            ("3",             lambda: self._playback.set_speed(2.0)),
            ("4",             lambda: self._playback.set_speed(4.0)),
            ("5",             lambda: self._playback.set_speed(8.0)),
            ("N",             self._next_video),
            ("P",             self._prev_video),
            ("D",             self._mark_done),
            ("S",             self._mark_skipped),
            ("T",             self._side.focus_tag_input),
            ("Delete",        self._delete_selected_segment),
            ("+",             self._timeline.zoom_in),
            ("=",             self._timeline.zoom_in),
            ("-",             self._timeline.zoom_out),
        ]
        # Ctrl shortcuts are safe even when text inputs are focused.
        ctrl_shortcuts: list[tuple[str, object]] = [
            ("Ctrl+E",        self._open_export_dialog),
            ("Ctrl+0",        self._timeline.zoom_fit),
        ]

        self._text_suppressed_shortcuts: list[QShortcut] = []
        for key, slot in text_suppressed:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(self._make_guarded_slot(slot))
            self._text_suppressed_shortcuts.append(sc)

        for key, slot in ctrl_shortcuts:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(slot)

    @staticmethod
    def _make_guarded_slot(slot: object):
        """Return a callable that invokes *slot* only when no text-input has focus."""
        def guarded() -> None:
            fw = QApplication.focusWidget()
            if isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit)):
                return
            slot()  # type: ignore[operator]

        return guarded

    def _connect_signals(self) -> None:
        """Wire all inter-component signals. No component talks to another directly."""

        # VideoListPanel → open video
        self._video_list.video_selected.connect(self._load_video)
        self._video_list.relink_requested.connect(self._open_relink_dialog)

        # PlayerPanel → transport + timeline
        self._player.position_changed.connect(self._transport.on_position_changed)
        self._player.position_changed.connect(self._timeline.set_position)
        self._player.duration_known.connect(self._transport.on_duration_known)
        self._player.duration_known.connect(self._on_duration_known)
        self._player.vfr_detected.connect(self._on_vfr_detected)
        self._player.speed_changed.connect(self._transport.on_speed_changed)
        self._player.paused_changed.connect(
            lambda paused: self._transport.set_playing(not paused)
        )

        # TransportBar → playback
        self._transport.play_pause_clicked.connect(self._playback.play_pause)
        self._transport.speed_selected.connect(self._playback.set_speed)
        self._transport.volume_changed.connect(self._player.set_volume)
        self._transport.seek_requested.connect(self._playback.seek_absolute)

        # Timeline → seek + trim + rubber band
        self._timeline.seek_requested.connect(self._playback.seek_absolute)
        self._timeline.segment_trim_requested.connect(self._on_segment_trim)
        self._timeline.segment_selected.connect(self._side.select_segment)
        self._timeline.in_point_drag_set.connect(self._on_timeline_in_point_drag)
        self._timeline.out_point_drag_set.connect(self._on_timeline_rubber_band)

        # PlaybackController → timeline in-point display + status
        self._playback.in_point_set.connect(self._timeline.set_in_point)
        self._playback.segment_cancelled.connect(lambda: self._timeline.set_in_point(None))
        self._playback.validation_failed.connect(self._show_toast)
        self._playback.confirm_long_segment_requested.connect(self._on_confirm_long_segment)

        # ProjectManager → UI refresh
        self._manager.project_changed.connect(self._on_project_changed)
        self._manager.video_changed.connect(self._on_video_changed)
        self._manager.segment_added.connect(self._on_segment_added)
        self._manager.segment_deleted.connect(self._on_segment_deleted)
        self._manager.tag_vocabulary_changed.connect(self._side.update_vocabulary)

        # SidePanel → manager / playback
        self._side.status_done_clicked.connect(self._mark_done)
        self._side.status_skipped_clicked.connect(self._mark_skipped)
        self._side.video_tag_added.connect(self._on_video_tag_added)
        self._side.video_tag_removed.connect(self._on_video_tag_removed)
        self._side.segment_seek_requested.connect(self._playback.seek_absolute)
        self._side.segment_delete_requested.connect(self._on_segment_delete)
        self._side.segment_tag_added.connect(self._on_segment_tag_added)
        self._side.segment_tag_removed.connect(self._on_segment_tag_removed)
        self._side.segment_notes_changed.connect(self._on_segment_notes_changed)
        self._side.export_clicked.connect(self._open_export_dialog)

        # WaveformCache → timeline
        self._waveform_cache.ready.connect(self._on_waveform_ready)
        self._waveform_cache.no_audio.connect(lambda _: self._timeline.set_no_audio())

        # ExportEngine → status bar + UI lock
        self._export_engine.clip_started.connect(self._status.on_clip_started)
        self._export_engine.clip_done.connect(self._status.on_clip_done)
        self._export_engine.clip_failed.connect(self._on_clip_failed)
        self._export_engine.export_complete.connect(self._on_export_complete)
        self._export_engine.export_cancelled.connect(self._on_export_cancelled)
        self._status.cancel_export_clicked.connect(self._export_engine.request_cancel)

    # ------------------------------------------------------------------
    # Folder / session management
    # ------------------------------------------------------------------

    def open_folder(self, path: str) -> None:
        """Open a folder programmatically (e.g., from CLI arg or recent menu)."""
        result = self._manager.open_folder(path)

        if result.has_session:
            dlg = RestoreSessionDialog(
                result.video_files.__len__(),
                result.session_video_count,
                result.session_segment_count,
                self,
            )
            dlg.exec()
            if dlg.should_restore:
                try:
                    self._manager.restore_session(path)
                except SessionCorruptError as exc:
                    QMessageBox.critical(self, "Session Error", str(exc))
                    self._manager.new_project(path, result.video_files)
            else:
                self._manager.new_project(path, result.video_files)
        else:
            self._manager.new_project(path, result.video_files)

        self._waveform_cache.set_source_folder(path)
        self._undo_stack.clear()
        self._settings.last_source_folder = path

    def _prompt_open_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Open Video Folder", self._settings.last_source_folder
        )
        if path:
            self.open_folder(path)

    def _clear_session(self) -> None:
        if self._manager.project is None:
            return
        reply = QMessageBox.question(
            self,
            "Clear Session",
            "Delete the saved session and start fresh for this folder?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        import os
        from video_annotator.models.session_store import SESSION_FILENAME
        session_path = os.path.join(self._manager.project.source_folder, SESSION_FILENAME)
        try:
            os.remove(session_path)
        except OSError:
            pass
        folder = self._manager.project.source_folder
        result = self._manager.open_folder(folder)
        self._manager.new_project(folder, result.video_files)
        self._undo_stack.clear()

    # ------------------------------------------------------------------
    # Video navigation
    # ------------------------------------------------------------------

    def _load_video(self, video_id: str) -> None:
        """Called when user double-clicks a video in the list."""
        if self._manager.project is None:
            return
        video = self._manager.project.get_video(video_id)
        if video is None:
            return

        self._current_video_id = video_id
        self._playback.set_current_video(video_id)
        self._player.load_video(video.abs_path)

        vocab = self._manager.project.tag_vocabulary
        self._side.load_video(video, vocab)

        self._timeline.load_video(video.duration, 30.0, video.segments)

        if video.status == "unseen":
            self._manager.set_video_status(video_id, "in_progress")

        self._update_status_for_video(video)

        self._waveform_cache.request(video_id, video.abs_path)

    def _next_video(self) -> None:
        if self._manager.project is None:
            return
        videos = self._manager.project.videos
        if not videos:
            return
        if self._current_video_id is None:
            self._load_video(videos[0].id)
            return
        ids = [v.id for v in videos]
        try:
            idx = ids.index(self._current_video_id)
        except ValueError:
            return
        if idx + 1 < len(ids):
            self._load_video(ids[idx + 1])
            self._video_list.select_video_id(ids[idx + 1])

    def _prev_video(self) -> None:
        if self._manager.project is None:
            return
        videos = self._manager.project.videos
        if not videos:
            return
        if self._current_video_id is None:
            return
        ids = [v.id for v in videos]
        try:
            idx = ids.index(self._current_video_id)
        except ValueError:
            return
        if idx - 1 >= 0:
            self._load_video(ids[idx - 1])
            self._video_list.select_video_id(ids[idx - 1])

    def _mark_done(self) -> None:
        if self._current_video_id:
            self._manager.set_video_status(self._current_video_id, "done")
            self._next_video()

    def _mark_skipped(self) -> None:
        if self._current_video_id:
            self._manager.set_video_status(self._current_video_id, "skipped")
            self._next_video()

    # ------------------------------------------------------------------
    # ProjectManager signal handlers
    # ------------------------------------------------------------------

    def _on_project_changed(self) -> None:
        if self._manager.project:
            self._video_list.refresh(self._manager.project.videos)
            self._update_status_bar_stats()
        self._side.set_export_enabled(self._manager.project is not None)

    def _on_video_changed(self, video_id: str) -> None:
        if self._manager.project is None:
            return
        # Refresh the full list to update status badge.
        self._video_list.refresh(self._manager.project.videos)
        if video_id == self._current_video_id:
            video = self._manager.project.get_video(video_id)
            if video:
                self._side.update_segments(video.segments)
                self._side.update_video_status(video.status)
                self._update_status_for_video(video)

    def _on_segment_added(self, video_id: str, segment_id: str) -> None:
        if self._manager.project is None:
            return
        if video_id == self._current_video_id:
            video = self._manager.project.get_video(video_id)
            if video:
                self._timeline.set_segments(video.segments)
                self._side.update_segments(video.segments)

    def _on_segment_deleted(self, video_id: str, segment_id: str) -> None:
        if self._manager.project is None:
            return
        if video_id == self._current_video_id:
            video = self._manager.project.get_video(video_id)
            if video:
                self._timeline.set_segments(video.segments)
                self._side.update_segments(video.segments)

    # ------------------------------------------------------------------
    # Tag handlers
    # ------------------------------------------------------------------

    def _on_video_tag_added(self, tag: str) -> None:
        if self._current_video_id:
            self._manager.add_video_tag(self._current_video_id, tag)

    def _on_video_tag_removed(self, tag: str) -> None:
        if self._current_video_id:
            self._manager.remove_video_tag(self._current_video_id, tag)

    def _on_segment_tag_added(self, segment_id: str, tag: str) -> None:
        if self._current_video_id:
            self._manager.add_segment_tag(self._current_video_id, segment_id, tag)

    def _on_segment_tag_removed(self, segment_id: str, tag: str) -> None:
        if self._current_video_id:
            self._manager.remove_segment_tag(self._current_video_id, segment_id, tag)

    def _on_segment_notes_changed(self, segment_id: str, notes: str) -> None:
        if self._current_video_id:
            self._manager.set_segment_notes(self._current_video_id, segment_id, notes)

    def _on_segment_delete(self, segment_id: str) -> None:
        if self._current_video_id:
            self._manager.delete_segment(self._current_video_id, segment_id)

    def _delete_selected_segment(self) -> None:
        segment_id = self._side.selected_segment_id
        if segment_id and self._current_video_id:
            self._manager.delete_segment(self._current_video_id, segment_id)

    def _on_segment_trim(self, segment_id: str, new_start: float, new_end: float) -> None:
        if self._current_video_id:
            self._manager.trim_segment(self._current_video_id, segment_id, new_start, new_end)

    def _on_timeline_in_point_drag(self, t: float) -> None:
        """Timeline rubber-band drag started: store start time and show in-point marker."""
        self._rubber_drag_start = t
        self._timeline.set_in_point(t)

    def _on_timeline_rubber_band(self, end: float) -> None:
        """Timeline rubber-band drag released: create segment if span is valid."""
        start = self._rubber_drag_start
        self._rubber_drag_start = None
        self._timeline.set_in_point(None)
        if start is not None:
            self._playback.set_segment_from_range(start, end)

    # ------------------------------------------------------------------
    # Player info (duration / VFR reported after file load)
    # ------------------------------------------------------------------

    def _on_duration_known(self, duration: float) -> None:
        """Store duration and update timeline; called once per file load."""
        self._pending_duration = duration
        self._timeline.set_duration(duration)
        # Persist into the model so future sessions have a real value.
        if self._current_video_id:
            self._manager.set_video_info(self._current_video_id, duration, False)

    def _on_vfr_detected(self, is_vfr: bool) -> None:
        """Final signal in the load sequence; update VFR flag in model."""
        if self._current_video_id:
            self._manager.set_video_info(
                self._current_video_id, self._pending_duration, is_vfr
            )
            # Refresh the list so the VFR badge can appear if the panel shows it.
            if self._manager.project:
                self._video_list.refresh(self._manager.project.videos)

    # ------------------------------------------------------------------
    # Waveform
    # ------------------------------------------------------------------

    def _on_waveform_ready(self, video_id: str, array: object) -> None:
        if video_id == self._current_video_id:
            import numpy as np
            if isinstance(array, np.ndarray):
                self._timeline.set_waveform(array)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _open_export_dialog(self) -> None:
        if self._manager.project is None:
            return
        dlg = ExportPreviewDialog(self._manager.project, self._settings, self)
        dlg.export_confirmed.connect(self._start_export)
        dlg.exec()

    def _start_export(self, spec: "ExportSpec") -> None:
        self._export_failure_count = 0
        self._export_output_folder = spec.output_folder
        self._set_ui_readonly(True)
        self._status.on_export_started(len(spec.clips))
        self._export_engine.start(spec)

    def _on_clip_failed(self, _clip_name: str, _reason: str) -> None:
        self._export_failure_count = getattr(self, "_export_failure_count", 0) + 1

    def _on_export_complete(self, clips_written: int, output_folder: str) -> None:
        self._set_ui_readonly(False)
        failures = getattr(self, "_export_failure_count", 0)
        if failures:
            log_path = f"{output_folder}/export_log.txt"
            self._status.on_export_error(failures, log_path)
        else:
            self._status.on_export_complete(clips_written, output_folder)

    def _on_export_cancelled(self) -> None:
        self._set_ui_readonly(False)
        self._status.on_export_cancelled()

    def _set_ui_readonly(self, readonly: bool) -> None:
        """Lock/unlock interactive widgets during export."""
        self._video_list.setEnabled(not readonly)
        self._side.setEnabled(not readonly)

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------

    def _open_relink_dialog(self) -> None:
        if self._manager.project is None:
            return
        missing = [v for v in self._manager.project.videos if v.status == "missing"]
        dlg = RelinkDialog(missing, self)
        if dlg.exec() == RelinkDialog.DialogCode.Accepted:
            for video_id, new_path in dlg.get_relinks().items():
                self._manager.relink_video(video_id, new_path)

    def _open_settings(self) -> None:
        SettingsPanel(self._settings, self).exec()

    # ------------------------------------------------------------------
    # Status bar helpers
    # ------------------------------------------------------------------

    def _update_status_bar_stats(self) -> None:
        if self._manager.project is None:
            return
        project = self._manager.project
        total = len(project.videos)
        done = sum(1 for v in project.videos if v.status in ("done", "skipped"))
        skipped = sum(1 for v in project.videos if v.status == "skipped")
        if self._current_video_id:
            video = project.get_video(self._current_video_id)
            if video:
                self._status.set_idle(
                    video.filename, done, skipped, len(video.segments), total
                )

    def _update_status_for_video(self, video: object) -> None:
        from video_annotator.models.project import Video
        if not isinstance(video, Video) or self._manager.project is None:
            return
        project = self._manager.project
        total = len(project.videos)
        done = sum(1 for v in project.videos if v.status in ("done", "skipped"))
        skipped = sum(1 for v in project.videos if v.status == "skipped")
        self._status.set_idle(
            video.filename, done, skipped, len(video.segments), total
        )

    # ------------------------------------------------------------------
    # Toast notifications
    # ------------------------------------------------------------------

    def _show_toast(self, message: str) -> None:
        self._toast.show_message(message)

    def _on_confirm_long_segment(self, duration: float) -> None:
        mins = int(duration) // 60
        secs = int(duration) % 60
        dur_str = f"{mins}m {secs}s" if mins else f"{secs}s"
        reply = QMessageBox.question(
            self,
            "Long Segment",
            f"Segment is unusually long ({dur_str}). Add anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._playback.confirm_pending_segment()
        else:
            self._playback.cancel_pending_segment()

    # ------------------------------------------------------------------
    # Window lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # Flush any pending auto-save synchronously before exit.
        if self._manager.project is not None:
            from video_annotator.models.session_store import SessionStore, SessionSaveError
            try:
                SessionStore.save(self._manager.project)
            except SessionSaveError:
                pass
        self._waveform_cache.cancel_current()
        self._export_engine.request_cancel()
        super().closeEvent(event)
