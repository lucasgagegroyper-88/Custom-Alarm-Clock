"""Regression cases found during the final audit; all state and media are disposable."""
import base64
import sqlite3

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QImage, QMovie
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest

from alarmclock.controller import Controller
from alarmclock.models import Alarm, Audio
from alarmclock.ui.banner import BannerWindow
from alarmclock.ui.corner_overlay import CornerOverlay
from test_media import FakeOutput, FakePlayer
from alarmclock.media import AudioPlayback


GIF = "R0lGODlhGAAQAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACAAAACwAAAAAGAAQAAAIIgABCBxIsKDBgwgTKlzIsKHDhxAjSpxIsaLFixgzatxoMCAAIfkEAQgAAQAsAAAAABgAEACBAAD/AAAAAAAAAAAACCIAAQgcSLCgwYMIEypcyLChw4cQI0qcSLGixYsYM2rcaDAgADs="


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "画像 with spaces.png"
    image = QImage(160, 100, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.red)
    assert image.save(str(path))
    return str(path)


@pytest.fixture
def rig(app, repo):
    controller = Controller(repo)
    overlay = CornerOverlay(repo)
    controller.corner_events.connect(overlay.handle_events)
    yield controller, overlay
    controller.shutdown()
    overlay.shutdown()
    controller.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("field", ["schedule", "banner", "audio", "realert", "corner", "notify"])
@pytest.mark.parametrize("value", [None, "invalid", []])
def test_invalid_optional_sections_load_defaults(field, value):
    raw = Alarm().to_dict()
    raw[field] = value
    loaded = Alarm.from_dict(raw)
    assert getattr(loaded, field) == getattr(Alarm(), field)


@pytest.mark.parametrize("database_failure", [False, True])
def test_failed_save_keeps_active_occurrence_and_saved_config(app, repo, monkeypatch, database_failure):
    controller = Controller(repo)
    alarm = Alarm()
    controller.save_alarm(alarm)
    controller.trigger_now(alarm.id)
    occurrence = repo.active()[0]
    before = repo.alarm(alarm.id).to_dict()
    draft = repo.alarm(alarm.id)
    if database_failure:
        def fail(_):
            raise sqlite3.OperationalError("disk full")
        monkeypatch.setattr(repo, "save_alarm", fail)
        error = sqlite3.OperationalError
    else:
        draft.name = ""
        error = ValueError
    try:
        with pytest.raises(error):
            controller.save_alarm(draft)
        assert repo.occurrence(occurrence.id).status == "active"
        assert repo.alarm(alarm.id).to_dict() == before
    finally:
        controller.shutdown()
        controller.deleteLater()


def test_native_overlay_close_stops_session_and_cannot_resurrect(rig, image):
    controller, overlay = rig
    alarm = Alarm()
    alarm.corner.enabled = alarm.corner.cycle = True
    alarm.corner.images = [image]
    controller.save_alarm(alarm)
    controller.trigger_now(alarm.id)
    overlay.close()
    assert not overlay.session_open
    assert not overlay.cycle_timer.isActive()
    assert not overlay.keep_on_top_timer.isActive()
    overlay.refresh()
    assert not overlay.isVisible()
    assert controller.repo.active()[0].status == "active"


@pytest.mark.parametrize("size", [1, 5, 11, 12, 24])
def test_close_button_fits_small_overlay(rig, image, size):
    controller, overlay = rig
    alarm = Alarm()
    alarm.corner.enabled = True
    alarm.corner.images = [image]
    alarm.corner.mode = "stretch"
    alarm.corner.keep_aspect = False
    alarm.corner.width = alarm.corner.height = size
    controller.save_alarm(alarm)
    controller.trigger_now(alarm.id)
    assert overlay.rect().contains(overlay.close_button.geometry())


def test_unchanged_placement_reuses_scaled_pixmap(rig, image):
    controller, overlay = rig
    alarm = Alarm()
    alarm.corner.enabled = True
    alarm.corner.images = [image]
    controller.save_alarm(alarm)
    controller.trigger_now(alarm.id)
    key = overlay._scaled.cacheKey()
    overlay.place()
    assert overlay._scaled.cacheKey() == key


def test_finished_finite_carousel_stops_idle_timer(rig, image):
    controller, overlay = rig
    alarm = Alarm()
    alarm.corner.enabled = alarm.corner.cycle = True
    alarm.corner.loop = False
    alarm.corner.images = [image]
    controller.save_alarm(alarm)
    controller.trigger_now(alarm.id)
    assert not overlay.cycle_timer.isActive()
    assert overlay.isVisible()


def test_late_playing_signal_does_not_unpause_playback(app):
    player = AudioPlayback(player_factory=FakePlayer, output_factory=FakeOutput, validator=lambda _: None)
    try:
        player.start(Audio(tracks=["test-only.mp3"]))
        player.pause()
        player._on_playback_state(player._token, QMediaPlayer.PlaybackState.PlayingState)
        assert player.state == "paused"
    finally:
        player.stop(immediate=True)
        player.deleteLater()


def test_superseded_activation_in_same_batch_cannot_show_old_notice(app, repo):
    controller = Controller(repo)
    alarm = Alarm(message="Old message")
    alarm.notify.enabled = True
    controller.save_alarm(alarm)
    events = controller.engine.trigger_now(alarm, 100)
    events += controller.engine.dismiss(events[-1].occurrence_id)
    try:
        controller.process(events)
        assert controller._notice is None
    finally:
        controller.shutdown()
        controller.deleteLater()


def test_notification_reports_missing_banner(app, repo, tmp_path):
    controller = Controller(repo)
    errors = []
    controller.error.connect(errors.append)
    alarm = Alarm(message="Message")
    alarm.notify.enabled = True
    alarm.banner.path = str(tmp_path / "missing.gif")
    try:
        controller.save_alarm(alarm)
        controller.trigger_now(alarm.id)
        assert errors and "missing.gif" in errors[0]
    finally:
        controller.shutdown()
        controller.deleteLater()


def test_nonlooping_banner_stops_intrinsically_looping_gif(app, tmp_path):
    path = tmp_path / "loop.gif"
    path.write_bytes(base64.b64decode(GIF))
    alarm = Alarm()
    alarm.banner.path = str(path)
    alarm.banner.loop_gif = False
    banner = BannerWindow(alarm)
    try:
        banner.open_banner()
        QTest.qWait(450)
        assert banner.movie.state() == QMovie.MovieState.NotRunning
        assert banner.movie.currentFrameNumber() == 1
    finally:
        banner.close()
        banner.deleteLater()


def test_gif_close_stops_movie_and_duration_timer(app, tmp_path):
    path = tmp_path / "animated.gif"
    path.write_bytes(base64.b64decode(GIF))
    alarm = Alarm(message="Notification")
    alarm.banner.path = str(path)
    alarm.notify.enabled = True
    notice = BannerWindow(alarm, notification=True)
    notice.open_banner()
    QTest.qWait(100)
    assert notice.movie.state() == QMovie.MovieState.Running
    notice.close()
    assert notice.movie.state() == QMovie.MovieState.NotRunning
    assert not notice.duration_timer.isActive()
    notice.deleteLater()


def test_failed_supersession_does_not_leave_two_active_occurrences(repo, monkeypatch):
    from alarmclock.engine import AlarmEngine
    from alarmclock.models import UTC
    engine = AlarmEngine(repo, UTC)
    alarm = Alarm()
    repo.save_alarm(alarm)
    original = engine.trigger_now(alarm, 100)[-1].occurrence_id
    def fail(_):
        raise OSError("simulated occurrence write failure")
    monkeypatch.setattr(repo, "save_occurrence", fail)
    with pytest.raises(OSError):
        engine.trigger_now(alarm, 101)
    assert [o.id for o in repo.active()] == [original]
    assert len(repo.history()) == 1
