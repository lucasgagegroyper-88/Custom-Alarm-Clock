from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtMultimedia import QMediaPlayer
import pytest
from alarmclock.models import Audio
from alarmclock.media import AudioPlayback, AudioQueue, validate_file, validate_image

class FakeOutput(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.volume = 0
    def setVolume(self, volume):
        self.volume = volume

class FakePlayer(QObject):
    mediaStatusChanged = Signal(object)
    errorOccurred = Signal(object, str)
    positionChanged = Signal(int)
    playbackStateChanged = Signal(object)
    instances = []
    def __init__(self, parent=None):
        super().__init__(parent)
        self.url = QUrl()
        self.output = None
        self.stopped = False
        self.play_count = 0
        self._duration = 10000
        self.instances.append(self)
    def setAudioOutput(self, output):
        self.output = output
    def setLoops(self, loops):
        self.loops = loops
    def setSource(self, url):
        self.url = url
    def play(self):
        self.play_count += 1
        self.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    def pause(self):
        self.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PausedState)
    def stop(self):
        self.stopped = True
    def errorString(self):
        return "Mock decoder rejected file"
    def duration(self):
        return self._duration
    def end(self):
        self.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)

@pytest.fixture
def playback(app):
    FakePlayer.instances.clear()
    instance = AudioPlayback(player_factory=FakePlayer, output_factory=FakeOutput, validator=lambda _: None)
    yield instance
    instance.stop(immediate=True)
    instance.deleteLater()
    app.processEvents()


def test_silent_start_creates_no_device_or_player(playback):
    done = []
    playback.finished.connect(lambda: done.append(True))
    playback.start(Audio())
    assert done == [True]
    assert playback.player is None and playback.output is None
    assert FakePlayer.instances == []


def test_real_completion_events_drive_exact_two_cycles(playback, app):
    played, done = [], []
    playback.track_changed.connect(lambda i, c: played.append((i, c)))
    playback.finished.connect(lambda: done.append(True))
    playback.start(Audio(tracks=["A.mp3", "B.mp3", "C.mp3"], mode="repeat_count", cycles=2))
    for _ in range(6):
        player = playback.player
        player.end()
        app.processEvents()
    assert played == [(0, 1), (1, 1), (2, 1), (0, 2), (1, 2), (2, 2)]
    assert done == [True]
    assert playback.player is None


def test_duplicate_end_event_not_double_counted(playback, app):
    playback.start(Audio(tracks=["A", "B"], mode="sequential"))
    token = playback._token
    playback._on_status(token, QMediaPlayer.MediaStatus.EndOfMedia)
    playback._on_status(token, QMediaPlayer.MediaStatus.EndOfMedia)
    app.processEvents()
    assert playback.cursor.current.index == 1
    assert playback.cursor.completed_cycles == 0
    playback._on_status(token, QMediaPlayer.MediaStatus.EndOfMedia)  # stale player
    assert playback.cursor.current.index == 1


def test_pause_resume_does_not_advance_track(playback):
    playback.start(Audio(tracks=["A", "B"]))
    player = playback.player
    playback.pause()
    assert playback.state == "paused" and playback.cursor.current.index == 0
    playback.resume()
    assert playback.player is player and player.play_count == 2
    assert playback.cursor.current.index == 0


def test_pause_between_tracks_preserves_remaining_gap(playback, app):
    playback.start(Audio(tracks=["A", "B"], track_gap=10))
    playback.player.end()
    assert playback.state == "gap"
    playback.pause()
    assert not playback.gap_timer.isActive()
    assert playback._gap_remaining > 0
    playback.resume()
    assert playback.state == "gap" and playback.gap_timer.isActive()
    playback.gap_timer.stop()
    playback._after_gap()
    assert playback.cursor.current.index == 1 and playback.state == "playing"


def test_stop_cancels_pending_track_gap(playback, app):
    playback.start(Audio(tracks=["A", "B"], track_gap=10))
    playback.player.end()
    playback.stop(immediate=True)
    playback._after_gap()
    assert playback.state == "stopped" and playback.player is None


def test_media_error_stops_without_substitution(playback, app):
    errors, tracks = [], []
    playback.error.connect(errors.append)
    playback.track_changed.connect(lambda i, _: tracks.append(i))
    playback.start(Audio(tracks=["A.mp3", "B.mp3"]))
    playback.player.errorOccurred.emit(QMediaPlayer.Error.FormatError, "Cannot decode")
    app.processEvents()
    assert playback.state == "error" and playback.player is None
    assert tracks == [0]
    assert "A.mp3" in errors[0] and "Cannot decode" in errors[0]


def test_invalid_media_status_is_reported(playback):
    errors = []
    playback.error.connect(errors.append)
    playback.start(Audio(tracks=["broken.mp3"]))
    playback.player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.InvalidMedia)
    assert len(errors) == 1 and playback.state == "error"


def test_missing_path_error_without_player(app):
    p = AudioPlayback(player_factory=FakePlayer, output_factory=FakeOutput)
    errors = []
    p.error.connect(errors.append)
    p.start(Audio(tracks=["/this/path/does/not/exist/user.mp3"]))
    assert p.state == "error" and p.player is None
    assert "File not found" in errors[0]
    p.deleteLater()


def test_missing_image_error():
    with pytest.raises(ValueError, match="File not found"):
        validate_image("/no/such/user.gif")
    validate_image("")


def test_invalid_file_error(tmp_path):
    # Text data tests read errors; no fake or fallback media is created.
    with pytest.raises(ValueError, match="File not found"):
        validate_file(str(tmp_path / "missing.mp3"))


def test_volume_adjustment(playback):
    playback.start(Audio(tracks=["A"], volume=30))
    assert playback.output.volume == .3
    playback.set_volume(75)
    assert playback.output.volume == .75


def test_natural_fade_does_not_finish_or_advance(playback):
    playback.start(Audio(tracks=["A"], fade_out=2))
    playback.player.positionChanged.emit(8500)
    assert playback._natural_fade
    assert not playback.cursor.finished
    assert playback.cursor.completed_cycles == 0
    playback.player.end()
    assert playback.cursor.completed_cycles == 1


def test_stop_fade_uses_stop_event_not_next_track(playback):
    playback.start(Audio(tracks=["A", "B"], fade_out=2))
    playback.stop()
    assert playback.state == "stopping"
    playback.player.end()
    assert playback.cursor.current.index == 0
    playback.fade.setCurrentTime(playback.fade.duration())
    assert playback.state == "stopped"


def test_audio_queue_serializes_simultaneous_alarms(app, monkeypatch):
    import alarmclock.media as module
    factory = module.AudioPlayback
    monkeypatch.setattr(module, "AudioPlayback", lambda parent: factory(parent, player_factory=FakePlayer, output_factory=FakeOutput, validator=lambda _: None))
    queue = AudioQueue()
    queue.enqueue("one", Audio(tracks=["A"]))
    first = queue.playback.player
    queue.enqueue("two", Audio(tracks=["B"]))
    assert queue.current == "one" and len(queue.pending) == 1
    assert queue.playback.player is first
    first.end()
    app.processEvents()
    assert queue.current == "two"
    assert queue.playback.player.url.toLocalFile() == "B"
    queue.shutdown()


def test_realert_restart_keeps_queue_identity(app, monkeypatch):
    import alarmclock.media as module
    factory = module.AudioPlayback
    monkeypatch.setattr(module, "AudioPlayback", lambda parent: factory(parent, player_factory=FakePlayer, output_factory=FakeOutput, validator=lambda _: None))
    queue = AudioQueue()
    queue.enqueue("one", Audio(tracks=["A"]))
    first = queue.playback.player
    queue.enqueue("one", Audio(tracks=["A"]))
    assert first.stopped
    assert queue.current == "one" and queue.playback.player is not first
    assert queue.pending == []
    queue.shutdown()


def test_queue_cancel_removes_pending_without_stopping_current(app, monkeypatch):
    import alarmclock.media as module
    factory = module.AudioPlayback
    monkeypatch.setattr(module, "AudioPlayback", lambda parent: factory(parent, player_factory=FakePlayer, output_factory=FakeOutput, validator=lambda _: None))
    queue = AudioQueue()
    queue.enqueue("one", Audio(tracks=["A"]))
    queue.enqueue("two", Audio(tracks=["B"]))
    queue.cancel("two")
    assert queue.current == "one" and not queue.pending
    queue.cancel("one", immediate=True)
    assert queue.current is None
    queue.shutdown()


def test_restart_resumes_remaining_cycles(playback, app):
    config = Audio(tracks=["A", "B", "C"], mode="repeat_count", cycles=2)
    snapshots = []
    playback.progress.connect(snapshots.append)
    playback.start(config)
    for _ in range(4):
        playback.player.end()
        app.processEvents()
    checkpoint = snapshots[-1]
    assert checkpoint["completed_cycles"] == 1
    playback.stop(immediate=True)
    tracks = []
    playback.track_changed.connect(lambda i, c: tracks.append((i, c)))
    playback.start(config, checkpoint)
    for _ in range(2):
        playback.player.end()
        app.processEvents()
    assert tracks == [(1, 2), (2, 2)]
    assert playback.cursor.completed_cycles == 2


def test_paused_checkpoint_restores_paused(playback):
    checkpoints = []
    playback.progress.connect(checkpoints.append)
    config = Audio(tracks=["A", "B"])
    playback.start(config)
    playback.pause()
    checkpoint = checkpoints[-1]
    assert checkpoint["paused"]
    playback.start(config, checkpoint)
    assert playback.state == "paused" and playback.cursor.current.index == 0


def test_stale_failure_callback_cannot_clear_restarted_queue(app, monkeypatch):
    import alarmclock.media as module
    factory = module.AudioPlayback
    def validator(path):
        if path == "bad":
            raise ValueError("Missing selected file")
    monkeypatch.setattr(module, "AudioPlayback", lambda parent: factory(parent, player_factory=FakePlayer, output_factory=FakeOutput, validator=validator))
    queue = AudioQueue()
    queue.enqueue("one", Audio(tracks=["bad"]))
    assert queue.playback.state == "error"
    queue.enqueue("one", Audio(tracks=["valid-user-path"]))
    app.processEvents()
    assert queue.current == "one" and queue.playback.state == "playing"
    queue.shutdown()
