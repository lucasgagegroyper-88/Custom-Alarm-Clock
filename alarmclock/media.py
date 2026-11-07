"""Read-only user media validation and event-driven Qt audio playback."""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import QObject, QTimer, QUrl, Signal, QVariantAnimation
from PySide6.QtGui import QImageReader
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtSvg import QSvgRenderer
from .models import Audio
from .playlist import PlaylistCursor, TrackStep

IMAGE_FILTER = "Images and GIFs (*.gif *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.ico *.svg);;All files (*)"
AUDIO_FILTER = "Audio files (*.mp3 *.wav *.flac *.ogg *.opus *.m4a *.aac *.wma *.aiff *.aif);;All files (*)"


def validate_file(path: str) -> None:
    if not Path(path).is_file():
        raise ValueError(f"File not found: {path}\nSelect its current location or remove it from this alarm.")
    try:
        with open(path, "rb") as stream:
            if not stream.read(1):
                raise ValueError(f"File is empty: {path}")
    except OSError as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc


def validate_image(path: str) -> None:
    if not path:
        return
    validate_file(path)
    if Path(path).suffix.lower() == ".svg":
        if not QSvgRenderer(path).isValid():
            raise ValueError(f"Invalid or unsupported SVG: {path}")
    else:
        reader = QImageReader(path)
        if not reader.canRead():
            raise ValueError(f"Cannot decode image: {path}\n{reader.errorString()}")


class AudioPlayback(QObject):
    error = Signal(str)
    finished = Signal()
    stopped = Signal()
    state_changed = Signal(str)
    track_changed = Signal(int, int)
    progress = Signal(object)

    def __init__(self, parent=None, *, player_factory=None, output_factory=None, validator=validate_file):
        super().__init__(parent)
        self._player_factory = player_factory or QMediaPlayer
        self._output_factory = output_factory or QAudioOutput
        self._validator = validator
        self.output = None  # Silent alarms do not initialize an audio device.
        self.player = None
        self.config = Audio()
        self.cursor = None
        self.state = "stopped"
        self._token = 0
        self._accept_end = False
        self._gap_step = None
        self._gap_remaining = 0
        self._paused_from = "playing"
        self._natural_fade = False
        self._fade_callback = None
        self._gain = 1.0
        self._first_track = True
        self.gap_timer = QTimer(self)
        self.gap_timer.setSingleShot(True)
        self.gap_timer.timeout.connect(self._after_gap)
        self.fade = QVariantAnimation(self)
        self.fade.valueChanged.connect(self._set_gain)
        self.fade.finished.connect(self._fade_finished)

    def _set_state(self, state):
        self.state = state
        self.state_changed.emit(state)

    def _set_gain(self, value):
        self._gain = float(value)
        if self.output is not None:
            self.output.setVolume(self.config.volume / 100 * self._gain)

    def set_volume(self, volume):
        self.config.volume = max(0, min(100, int(volume)))
        self._set_gain(self._gain)

    def _fade_to(self, gain, seconds, callback=None):
        self.fade.stop()
        self._fade_callback = callback
        if seconds <= 0:
            self._set_gain(gain)
            self._fade_finished()
            return
        self.fade.setStartValue(self._gain)
        self.fade.setEndValue(float(gain))
        self.fade.setDuration(max(1, int(seconds * 1000)))
        self.fade.start()

    def _fade_finished(self):
        callback, self._fade_callback = self._fade_callback, None
        if callback:
            callback()

    def _dispose_player(self):
        if self.player is not None:
            self.player.blockSignals(True)
            self.player.stop()
            self.player.setAudioOutput(None)
            self.player.setSource(QUrl())
            self.player.deleteLater()
            self.player = None

    def start(self, config: Audio, checkpoint=None):
        self.stop(immediate=True)
        self.config = Audio(**vars(config))
        self.config.tracks = list(config.tracks)
        self.cursor = PlaylistCursor(self.config)
        self._first_track = True
        self._natural_fade = False
        if checkpoint:
            try:
                self.cursor.restore(checkpoint)
            except (ValueError, KeyError, TypeError) as exc:
                self._fail(str(exc))
                return
        if not self.config.tracks or self.cursor.finished:
            self._set_state("finished")
            self.finished.emit()
            return
        # Report the exact invalid track. Never skip, substitute or select another file.
        try:
            for path in self.config.tracks:
                self._validator(path)
        except ValueError as exc:
            self._fail(str(exc))
            return
        self._play(self.cursor.current)
        if checkpoint and checkpoint.get("paused"):
            self.pause()

    def _emit_progress(self):
        if self.cursor:
            checkpoint = self.cursor.checkpoint()
            checkpoint["paused"] = self.state == "paused"
            self.progress.emit(checkpoint)

    def _play(self, step: TrackStep):
        self._dispose_player()
        self._token += 1
        token = self._token
        path = self.config.tracks[step.index]
        try:
            self._validator(path)
        except ValueError as exc:
            self._fail(str(exc))
            return
        if self.output is None:
            self.output = self._output_factory(self)
        if hasattr(self.output, "device") and self.output.device().isNull():
            self._fail("No audio output device is available. Connect or enable an audio device, then trigger the alarm again.")
            return
        self._accept_end = True
        self.player = self._player_factory(self)
        self.player.setAudioOutput(self.output)
        self.player.setLoops(1)
        self.player.mediaStatusChanged.connect(lambda status, t=token: self._on_status(t, status))
        self.player.errorOccurred.connect(lambda error, detail, t=token: self._on_error(t, error, detail))
        self.player.positionChanged.connect(lambda position, t=token: self._on_position(t, position))
        self.player.playbackStateChanged.connect(lambda state, t=token: self._on_playback_state(t, state))
        self._set_state("loading")
        self.track_changed.emit(step.index, step.cycle)
        self._emit_progress()
        if self._first_track:
            self._set_gain(0.0 if self.config.fade_in else 1.0)
        elif self.fade.state() == QVariantAnimation.State.Stopped:
            self._set_gain(1.0)
        self.player.setSource(QUrl.fromLocalFile(path))
        if self.player is not None and self.state != "error":
            self.player.play()

    def _on_playback_state(self, token, state):
        if token != self._token:
            return
        if state == QMediaPlayer.PlaybackState.PlayingState and self.state not in {"paused", "stopping", "error", "stopped"}:
            self._set_state("playing")
            if self._first_track:
                self._first_track = False
                if self.config.fade_in:
                    self._fade_to(1.0, self.config.fade_in)

    def _on_error(self, token, error, detail):
        if token == self._token and error != QMediaPlayer.Error.NoError:
            path = self.config.tracks[self.cursor.current.index] if self.cursor and self.cursor.current else ""
            self._fail(f"Cannot play: {path}\n{detail or str(error)}\nFix or replace this track manually; playback has stopped.")

    def _on_status(self, token, status):
        if token != self._token:
            return
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._on_error(token, QMediaPlayer.Error.FormatError, self.player.errorString() if self.player else "Unsupported or invalid audio")
        elif status == QMediaPlayer.MediaStatus.EndOfMedia and self._accept_end and self.state in {"playing", "paused", "loading"}:
            self._accept_end = False
            was_paused = self.state == "paused"
            step = self.cursor.complete_track()
            self._emit_progress()
            self._dispose_player()
            if step is None:
                self.fade.stop()
                self._fade_callback = None
                self._set_state("finished")
                self.finished.emit()
                return
            self._gap_step = step
            self._gap_remaining = max(0, int(step.delay * 1000))
            if was_paused:
                self._paused_from = "gap"
            else:
                self._set_state("gap")
                self.gap_timer.start(self._gap_remaining)

    def _after_gap(self):
        step, self._gap_step = self._gap_step, None
        if step and self.state == "gap":
            self._play(step)

    def _on_position(self, token, position):
        if token != self._token or self.state != "playing" or not self.player or not self.cursor:
            return
        duration = self.player.duration()
        if self.cursor.is_final_track and self.config.fade_out and duration > 0 and not self._natural_fade:
            remaining = duration - position
            if 0 < remaining <= self.config.fade_out * 1000:
                self._natural_fade = True
                # Duration is used ONLY for volume. Completion always comes from EndOfMedia.
                self._fade_to(0.0, remaining / 1000)

    def pause(self):
        if self.state not in {"playing", "loading", "gap"}:
            return
        self._paused_from = self.state
        if self.state == "gap":
            self._gap_remaining = max(0, self.gap_timer.remainingTime())
            self.gap_timer.stop()
        elif self.player:
            self.player.pause()
        if self.fade.state() == QVariantAnimation.State.Running:
            self.fade.pause()
        self._set_state("paused")
        self._emit_progress()

    def resume(self):
        if self.state != "paused":
            return
        if self.fade.state() == QVariantAnimation.State.Paused:
            self.fade.resume()
        self._set_state(self._paused_from)
        if self._paused_from == "gap":
            self.gap_timer.start(self._gap_remaining)
        elif self.player:
            self.player.play()
        self._emit_progress()

    def stop(self, *, immediate=False):
        self.gap_timer.stop()
        self._gap_step = None
        self._accept_end = False
        if not immediate and self.player and self.state in {"playing", "loading"} and self.config.fade_out:
            self._set_state("stopping")
            self._fade_to(0.0, self.config.fade_out, self._stop_now)
        else:
            self._stop_now()

    def _stop_now(self):
        self._token += 1
        self.fade.stop()
        self._fade_callback = None
        self._dispose_player()
        self._set_state("stopped")
        self.stopped.emit()

    def _fail(self, detail):
        self.gap_timer.stop()
        self.fade.stop()
        self._fade_callback = None
        self._dispose_player()
        self._token += 1
        self._set_state("error")
        self.error.emit(detail)
        self.finished.emit()


class AudioQueue(QObject):
    """One physical player: simultaneous alarms queue instead of overlapping."""
    error = Signal(str, str)
    finished = Signal(str)
    changed = Signal()
    progress = Signal(str, object)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.playback = AudioPlayback(self)
        self.current = None
        self.pending = []
        self._mutating = False
        self.playback.error.connect(lambda message: self.error.emit(self.current or "", message))
        self.playback.finished.connect(self._completed)
        self.playback.stopped.connect(self._stopped)
        self.playback.state_changed.connect(lambda _: self.changed.emit())
        self.playback.track_changed.connect(lambda *_: self.changed.emit())
        self.playback.progress.connect(lambda checkpoint: self.progress.emit(self.current or "", checkpoint))

    def enqueue(self, key: str, config: Audio, *, restart=True, checkpoint=None):
        if not config.tracks:
            self.cancel(key, immediate=True)
            return
        if key == self.current:
            if not restart:
                return
            self._mutating = True
            self.playback.stop(immediate=True)
            self.playback.start(config, checkpoint)
            self._mutating = False
            if self.playback.state in {"finished", "error"}:
                self._schedule_finished_start()
        else:
            if any(k == key for k, _, _ in self.pending):
                if not restart:
                    return
                self.pending = [(k, c, p) for k, c, p in self.pending if k != key]
            self.pending.append((key, config, checkpoint))
            self._pump()
        self.changed.emit()

    def _pump(self):
        if self.current is None and self.pending:
            self.current, config, checkpoint = self.pending.pop(0)
            self._mutating = True
            self.playback.start(config, checkpoint)
            self._mutating = False
            # An invalid file can finish synchronously during start().
            if self.playback.state in {"finished", "error"}:
                self._schedule_finished_start()
        self.changed.emit()

    def _schedule_finished_start(self):
        key, token = self.current, self.playback._token
        def complete_if_current():
            if self.current == key and self.playback._token == token and self.playback.state in {"finished", "error"}:
                self._completed()
        QTimer.singleShot(0, complete_if_current)

    def _completed(self):
        if self._mutating:
            return
        key, self.current = self.current, None
        if key:
            self.finished.emit(key)
        QTimer.singleShot(0, self._pump)
        self.changed.emit()

    def _stopped(self):
        if not self._mutating:
            self.current = None
            QTimer.singleShot(0, self._pump)
            self.changed.emit()

    def cancel(self, key, *, immediate=False):
        self.pending = [(k, c, p) for k, c, p in self.pending if k != key]
        if self.current == key:
            self.playback.stop(immediate=immediate)
        self.changed.emit()

    def shutdown(self):
        self.pending.clear()
        self._mutating = True
        self.playback.stop(immediate=True)
        self.current = None
        self._mutating = False

    def describe(self, key):
        if key == self.current:
            cursor = self.playback.cursor
            step = cursor.current if cursor else None
            suffix = f" · track {step.index + 1}, cycle {step.cycle}" if step else ""
            return self.playback.state.capitalize() + suffix
        if any(k == key for k, _, _ in self.pending):
            return "Audio queued"
        return "Silent / audio stopped"
