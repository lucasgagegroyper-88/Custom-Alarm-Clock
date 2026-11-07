from datetime import datetime, timedelta
from PySide6.QtCore import Qt, QObject, Signal, QSize, QByteArray
from PySide6.QtGui import QMovie as RealMovie, QPixmap
from PySide6.QtTest import QTest
from alarmclock.models import Alarm, Schedule, ReAlert, UTC
from alarmclock.controller import Controller
from alarmclock.ui.banner import BannerWindow
from alarmclock.ui.dashboard import Dashboard
from alarmclock.ui.editor import AlarmEditor
from alarmclock.ui.theme import apply_theme


def test_editor_saves_all_panels(app, repo):
    controller = Controller(repo)
    editor = AlarmEditor(None, controller)
    editor.show()
    editor.name.setText("My alarm")
    editor.kind.setCurrentIndex(editor.kind.findData("weekly"))
    for i, day in enumerate(editor.weekdays):
        day.setChecked(i in [1, 3])
    editor.width.setValue(720)
    editor.hide_text.setChecked(True)
    editor.hide_buttons.setChecked(True)
    editor.gif_speed.setValue(175)
    editor.audio_mode.setCurrentIndex(editor.audio_mode.findData("repeat_count"))
    editor.cycles.setValue(3)
    editor.realert_count.setValue(2)
    editor.realert_interval.amount.setValue(5)
    editor.realert_interval.unit.setCurrentIndex(editor.realert_interval.unit.findData(60))
    editor.save()
    saved = editor.result_alarm
    assert saved.name == "My alarm" and saved.schedule.weekdays == [1, 3]
    assert saved.banner.width == 720 and saved.banner.hide_text and saved.banner.hide_buttons
    assert saved.banner.gif_speed == 175
    assert saved.audio.mode == "repeat_count" and saved.audio.cycles == 3
    assert saved.realert.count == 2 and saved.realert.interval_seconds == 300
    assert not saved.audio.tracks and not saved.banner.path
    controller.save_alarm(saved)
    assert repo.alarm(saved.id).to_dict() == saved.to_dict()
    editor.deleteLater()
    controller.shutdown()


def test_editor_invalid_media_stays_open(app, repo):
    controller = Controller(repo)
    editor = AlarmEditor(None, controller)
    editor.image_path.setText("/missing/user.gif")
    editor.save()
    assert editor.result_alarm is None
    assert "File not found" in editor.error_label.text()
    editor.reject()
    editor.deleteLater()
    controller.shutdown()


def test_playlist_editor_reorders_and_removes(app, repo):
    controller = Controller(repo)
    editor = AlarmEditor(None, controller)
    editor.tracks.addItems(["A.mp3", "B.mp3", "C.mp3"])
    editor.tracks.setCurrentRow(2)
    editor._move_track(-1)
    assert editor.gather().audio.tracks == ["A.mp3", "C.mp3", "B.mp3"]
    editor._remove_tracks()
    assert editor.gather().audio.tracks == ["A.mp3", "B.mp3"]
    editor.reject()
    editor.deleteLater()
    controller.shutdown()


def test_empty_banner_has_no_image(app):
    alarm = Alarm()
    alarm.banner.hide_text = True
    alarm.banner.hide_buttons = True
    banner = BannerWindow(alarm)
    banner.open_banner()
    app.processEvents()
    assert banner.movie is None and banner.pixmap is None and banner.svg is None
    assert not banner.buttons.isVisible()
    banner.close()
    banner.deleteLater()


def test_banner_close_different_from_dismiss(app):
    banner = BannerWindow(Alarm())
    closed, dismissed = [], []
    banner.banner_closed.connect(lambda: closed.append(True))
    banner.dismiss_requested.connect(lambda: dismissed.append(True))
    banner.open_banner()
    banner.close()
    assert closed == [True] and dismissed == []
    banner.deleteLater()


def test_duration_expiry_actions(app):
    a = Alarm()
    a.banner.auto_action = "dismiss"
    banner = BannerWindow(a)
    dismissed = []
    banner.dismiss_requested.connect(lambda: dismissed.append(True))
    banner._expired()
    assert dismissed == [True]
    banner.close()
    banner.deleteLater()


def test_gif_uses_qmovie_and_frame_updates(app, monkeypatch):
    import alarmclock.ui.banner as module
    class Reader:
        def __init__(self, _): pass
        def setAutoTransform(self, _): pass
        def format(self): return QByteArray(b"gif")
    class Movie(QObject):
        CacheMode = RealMovie.CacheMode
        frameChanged = Signal(int)
        error = Signal(object)
        finished = Signal()
        def __init__(self, path, parent=None):
            super().__init__(parent)
            self.path, self.speed, self.started, self.stopped = path, None, 0, False
        def setCacheMode(self, _): pass
        def setSpeed(self, speed): self.speed = speed
        def isValid(self): return True
        def start(self): self.started += 1
        def stop(self): self.stopped = True
        def currentPixmap(self): return QPixmap()
        def lastErrorString(self): return "test error"
    monkeypatch.setattr(module, "validate_image", lambda _: None)
    monkeypatch.setattr(module, "QImageReader", Reader)
    monkeypatch.setattr(module, "QMovie", Movie)
    a = Alarm()
    a.banner.path = "user-selected.gif"
    a.banner.gif_speed = 150
    b = BannerWindow(a)
    frames = []
    b.update = lambda: frames.append(True)
    b.open_banner()
    b.movie.frameChanged.emit(0)
    b.movie.frameChanged.emit(1)
    assert len(frames) == 2 and b.movie.speed == 150 and b.movie.started == 1
    b.movie.finished.emit()
    assert b.movie.started == 2
    b.close()
    assert b.movie.stopped
    b.deleteLater()


def test_live_silent_alarm_controls_end_to_end(app, repo, tmp_path):
    controller = Controller(repo)
    window = Dashboard(controller, tmp_path)
    window.show()
    a = Alarm(name="QA silent alarm", realert=ReAlert(2, 30))
    a.banner.hide_text = a.banner.hide_buttons = True
    controller.save_alarm(a)
    controller.trigger_now(a.id)
    app.processEvents()
    assert len(repo.active()) == 1 and not controller.banners  # activation opens no popup
    o = repo.active()[0]
    controller.stop_sound(o.id)
    assert repo.occurrence(o.id).status == "active"
    controller.close_banner(o.id)
    assert not controller.banners and repo.active()
    controller.show_banner(o.id)
    assert len(controller.banners) == 1
    controller.snooze(o.id, 60)
    assert repo.occurrence(o.id).status == "snoozed" and not controller.banners
    controller.dismiss(o.id)
    assert repo.active() == [] and repo.alarm(a.id).enabled
    window._quitting = True
    controller.shutdown()
    window.close()
    window.clock_timer.stop()
    window.refresh_timer.stop()
    window.deleteLater()


def test_disable_and_edit_cancel_active_occurrence(app, repo):
    controller = Controller(repo)
    a = Alarm()
    controller.save_alarm(a)
    controller.trigger_now(a.id)
    controller.toggle_alarm(a.id)
    assert repo.active() == [] and not repo.alarm(a.id).enabled
    controller.trigger_now(a.id)
    a.name = "Edited"
    controller.save_alarm(a)
    assert repo.active() == []
    assert repo.alarm(a.id).revision == 2
    controller.shutdown()


def test_preview_uses_actual_banner_window_without_media(app, repo):
    controller = Controller(repo)
    editor = AlarmEditor(None, controller)
    editor.preview_banner()
    assert len(editor.previews) == 1
    assert isinstance(editor.previews[0], BannerWindow)
    assert editor.previews[0].movie is None
    editor.reject()
    assert editor.previews == []
    controller.shutdown()
    editor.deleteLater()


def test_light_and_dark_themes(app):
    apply_theme(app, "light")
    assert app.palette().window().color().name() == "#f2f5fb"
    apply_theme(app, "dark")
    assert app.palette().window().color().name() == "#101522"


def test_qt_timer_automatically_triggers_scheduled_alarm(app, repo):
    import time
    controller = Controller(repo)
    due = int(time.time()) + 2
    local = datetime.fromtimestamp(due, controller.engine.zone)
    a = Alarm(name="Automatic timer test", schedule=Schedule("once", local.date().isoformat(), local.strftime("%H:%M:%S")))
    controller.save_alarm(a)
    activations = []
    controller.activated.connect(activations.append)
    controller.start()
    QTest.qWait(2700)
    assert activations == ["Automatic timer test"]
    assert len(repo.active()) == 1
    assert not controller.banners  # banner is the dashboard background, not a popup
    controller.shutdown()
    assert not controller.timer.isActive() and not controller.banners


def test_dashboard_background_follows_selected_alarm_and_reports_missing_file(app, repo, tmp_path):
    controller = Controller(repo)
    window = Dashboard(controller, tmp_path)
    plain = Alarm(name="No image")
    broken = Alarm(name="Missing file")
    broken.banner.path = str(tmp_path / "does-not-exist.gif")
    controller.save_alarm(plain)
    controller.save_alarm(broken)
    errors = []
    controller.error.connect(errors.append)
    window.refresh()
    for row in range(window.alarms.rowCount()):
        window.alarms.selectRow(row)
        if window.alarms.item(row, 0).data(Qt.ItemDataRole.UserRole) == broken.id:
            break
    app.processEvents()
    assert not window.background.has_image  # nothing is substituted for a missing file
    assert errors and "does-not-exist.gif" in errors[0]
    window._quitting = True
    controller.shutdown()
    window.clock_timer.stop()
    window.refresh_timer.stop()
    window.deleteLater()
