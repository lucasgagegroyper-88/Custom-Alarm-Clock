"""Integrated Qt, persistence and controlled-time acceptance scenarios."""
import base64
from datetime import datetime, timedelta
from pathlib import Path
import random
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QMovie
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QPushButton

from alarmclock.carousel import Carousel
from alarmclock.controller import Controller
from alarmclock.engine import AlarmEngine
from alarmclock.models import Alarm, Corner, Notify, ReAlert, Schedule, UTC
from alarmclock.scheduling import next_occurrence
from alarmclock.storage import Repository
from alarmclock.ui.banner import BannerWindow
from alarmclock.ui.corner_overlay import CornerOverlay
from alarmclock.ui.dashboard import Dashboard
from alarmclock.ui.editor import AlarmEditor
from alarmclock.ui.settings import SettingsDialog
from alarmclock.ui.theme import apply_theme
from test_audit_regressions import GIF
from test_audit_regressions import image as image


def flush(app):
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_three_alarm_edit_cancel_duplicate_delete_and_restart(app, tmp_path, image):
    path = tmp_path / "three.sqlite3"
    repo = Repository(path)
    controller = Controller(repo)
    alarms = [
        Alarm(name="A", message="Message A", schedule=Schedule("daily", "2020-01-01", "07:00:00"),
              corner=Corner(enabled=True, images=[image, "missing A.png"]), notify=Notify(True, 12, 20)),
        Alarm(name="B", message="Message B", schedule=Schedule("weekly", "2020-01-01", "09:00:00", [0, 2, 4]),
              corner=Corner(enabled=True, images=["B1.gif", "B2.png"], order="shuffle"), notify=Notify(True, 8, 18)),
        Alarm(name="C", schedule=Schedule("interval", "2020-01-01", "10:00:00", interval=90, interval_unit="minutes")),
    ]
    for alarm in alarms:
        controller.save_alarm(alarm)
    before_b, before_c = [repo.alarm(a.id).to_dict() for a in alarms[1:]]
    carousel = Carousel(repo, scope=alarms[0].id)
    assert carousel.select(alarms[0].corner.images) == image
    draft = AlarmEditor(repo.alarm(alarms[0].id), controller)
    draft.message.setPlainText("Edited A")
    draft.corner.opacity.setValue(55)
    draft.save()
    assert draft.result_alarm is not None
    controller.save_alarm(draft.result_alarm)
    assert carousel.next_index == 1
    assert repo.alarm(alarms[1].id).to_dict() == before_b
    assert repo.alarm(alarms[2].id).to_dict() == before_c
    draft.deleteLater()
    cancelled = AlarmEditor(repo.alarm(alarms[2].id), controller)
    cancelled.interval.setValue(17)
    cancelled.notify_enabled.setChecked(True)
    cancelled.reject(); cancelled.deleteLater()
    assert repo.alarm(alarms[2].id).to_dict() == before_c
    duplicate = repo.alarm(alarms[0].id).clone()
    controller.save_alarm(duplicate)
    duplicate.corner.images.append("copy only.png")
    assert "copy only.png" not in repo.alarm(alarms[0].id).corner.images
    assert Carousel(repo, scope=duplicate.id).next_index == 0
    cb = Carousel(repo, random.Random(9), alarms[1].id)
    cb.select(alarms[1].corner.images, "shuffle")
    controller.delete_alarm(alarms[1].id)
    assert cb.next_index == 0 and carousel.next_index == 1
    expected = {a.id: a.to_dict() for a in repo.alarms()}
    controller.shutdown(); controller.deleteLater(); flush(app); repo.close()
    reopened = Repository(path)
    assert {a.id: a.to_dict() for a in reopened.alarms()} == expected
    assert Carousel(reopened, scope=alarms[0].id).next_index == 1
    assert reopened.alarm(alarms[1].id) is None
    assert Path(image).is_file()
    reopened.close()


def test_save_rolls_back_alarm_occurrence_and_carousel_as_one_unit(app, repo, monkeypatch):
    import alarmclock.controller as module
    controller = Controller(repo)
    alarm = Alarm()
    controller.save_alarm(alarm); controller.trigger_now(alarm.id)
    original = repo.alarm(alarm.id).to_dict()
    owner = repo.active()[0].id
    draft = repo.alarm(alarm.id)
    draft.corner.images = ["new.png"]
    def failure(repository, *args):
        repository.set("corner_next:" + alarm.id, 99)
        raise OSError("simulated write failure")
    monkeypatch.setattr(module, "sync_alarm", failure)
    with pytest.raises(OSError):
        controller.save_alarm(draft)
    assert repo.alarm(alarm.id).to_dict() == original
    assert repo.occurrence(owner).status == "active"
    assert Carousel(repo, scope=alarm.id).next_index == 0
    controller.shutdown(); controller.deleteLater()


def test_equal_preference_write_does_not_update_sqlite(repo):
    repo.set("same", [1, 2])
    before = repo.db.total_changes
    repo.set("same", [1, 2])
    assert repo.db.total_changes == before
    repo.set("same", [1, 3])
    assert repo.db.total_changes == before + 1


def test_settings_cancel_and_atomic_save(app, repo):
    dialog = SettingsDialog(repo)
    dialog.theme.setCurrentIndex(dialog.theme.findData("light"))
    dialog.reject(); dialog.deleteLater()
    assert repo.get("theme") is None
    dialog = SettingsDialog(repo)
    dialog.theme.setCurrentIndex(dialog.theme.findData("light"))
    dialog.restore.setChecked(False)
    commits = []
    repo.db.set_trace_callback(lambda sql: commits.append(sql) if sql == "COMMIT" else None)
    dialog.save()
    repo.db.set_trace_callback(None)
    assert repo.get("theme") == "light" and repo.get("resume_active") is False
    assert commits == ["COMMIT"]
    dialog.deleteLater()


def test_dashboard_buttons_and_on_checkbox(app, repo, tmp_path, monkeypatch):
    controller = Controller(repo)
    window = Dashboard(controller, tmp_path)
    window.show(); flush(app)
    buttons = {b.text(): b for b in window.findChildren(QPushButton)}
    edits = []
    monkeypatch.setattr(window, "_edit", edits.append)
    QTest.mouseClick(buttons["+ Add Alarm"], Qt.MouseButton.LeftButton)
    assert edits == [None]
    alarm = Alarm(name="Dashboard test")
    controller.save_alarm(alarm)
    QTest.mouseClick(buttons["Edit Alarm"], Qt.MouseButton.LeftButton)
    assert edits[-1].id == alarm.id
    QTest.mouseClick(buttons["Duplicate"], Qt.MouseButton.LeftButton)
    assert len(repo.alarms()) == 2 and not repo.alarms()[1].enabled
    item = window.alarms.item(0, 0)
    item.setCheckState(Qt.CheckState.Unchecked)
    assert not repo.alarm(alarm.id).enabled
    window.alarms.item(0, 0).setCheckState(Qt.CheckState.Checked)
    assert repo.alarm(alarm.id).enabled
    QTest.mouseClick(buttons["Trigger Now"], Qt.MouseButton.LeftButton)
    assert len(repo.active()) == 1
    for _ in range(10):
        window.refresh()
    assert len(repo.active()) == 1 and not controller.banners
    assert not window.corner_overlay.isVisible()
    # Exercise the real confirmation dialog and its Delete action.
    from PySide6.QtCore import QTimer
    def confirm():
        dialog = app.activeModalWidget()
        box = dialog.findChild(QDialogButtonBox)
        next(b for b in box.buttons() if b.text() == "Delete").click()
    QTimer.singleShot(0, confirm)
    QTest.mouseClick(buttons["Delete"], Qt.MouseButton.LeftButton)
    assert repo.alarm(alarm.id) is None and not repo.active()
    window.clock_timer.stop(); window.refresh_timer.stop()
    window.corner_overlay.shutdown(); controller.shutdown()
    window._quitting = True
    window.close(); window.deleteLater(); controller.deleteLater(); flush(app)


def test_notification_ownership_manual_close_timeout_restore_and_banner_independence(app, repo, image, tmp_path):
    controller = Controller(repo)
    gif = tmp_path / "B.gif"
    gif.write_bytes(base64.b64decode(GIF))
    a, b = Alarm(message="Alpha only"), Alarm(message="Beta only")
    for alarm, media in ((a, image), (b, str(gif))):
        alarm.notify.enabled = True
        alarm.banner.path = media
        alarm.banner.text = "Never use this in the notification"
        controller.save_alarm(alarm)
        controller.trigger_now(alarm.id)
    owners = {o.alarm_id: o.id for o in repo.active()}
    notice = controller._notice
    assert notice.alarm.message == "Beta only" and notice.config.path == str(gif)
    assert notice.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert notice.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    QTest.qWait(100)
    assert notice.movie.state() == QMovie.MovieState.Running
    controller.show_banner(owners[b.id])
    popup = controller.banners[owners[b.id]]
    controller.dismiss(owners[a.id])
    assert controller._notice is notice
    notice.buttons.click()
    assert controller._notice is None and popup.isVisible()
    assert repo.occurrence(owners[b.id]).status == "active"
    controller.process(controller.engine.restore(repo.get("last_tick", 0) + 1))
    assert controller._notice is None
    controller._show_notice(repo.occurrence(owners[b.id]))
    assert controller._notice.duration_timer.isActive()
    controller._notice._expired()
    assert controller._notice is None and popup.isVisible()
    controller.shutdown(); controller.deleteLater(); flush(app)


def test_notification_real_autoclose_and_tint(app, image):
    alarm = Alarm(message="Readable message")
    alarm.banner.path = image
    alarm.notify.duration = 1
    notice = BannerWindow(alarm, notification=True)
    notice.open_banner(); flush(app)
    pixel = notice.grab().toImage().pixelColor(35, 25)
    # A red fixture must remain visible beneath the dark translucent tint.
    assert 80 < pixel.red() < 230 and pixel.red() > pixel.blue() * 2
    QTest.qWait(1200)
    assert not notice.isVisible() and not notice.duration_timer.isActive()
    notice.deleteLater()


@pytest.mark.parametrize("font_size", [8, 16, 32, 72])
def test_notification_long_text_wrap_reserves_close_button(app, font_size):
    alarm = Alarm(message="A long message with words that wrap across several lines. " * 3)
    alarm.notify.font_size = font_size
    notice = BannerWindow(alarm, notification=True)
    notice.open_banner(); flush(app)
    assert notice.rect().contains(notice.buttons.geometry())
    assert notice.buttons.geometry().bottom() < 22
    assert notice.buttons.geometry().left() >= notice.width() - 22
    assert notice.config.text == alarm.message
    notice.close(); notice.deleteLater()


def test_editor_tabs_roundtrip_controls_and_no_preview_sql_writes(app, repo, image):
    controller = Controller(repo)
    alarm = Alarm(schedule=Schedule("daily", "2020-01-01", "08:00:00"))
    controller.save_alarm(alarm)
    editor = AlarmEditor(repo.alarm(alarm.id), controller)
    editor.show()
    before = repo.db.total_changes
    for index in range(editor.tabs.count()):
        editor.tabs.setCurrentIndex(index); flush(app)
        assert editor.tabs.currentWidget().isVisible()
    editor.image_path.setText(image)
    editor.fit.setCurrentIndex(editor.fit.findData("fill"))
    editor.position.setCurrentIndex(editor.position.findData("custom"))
    assert editor.x.isEnabled() and editor.y.isEnabled()
    editor.x.setValue(-22); editor.y.setValue(31)
    editor.opacity.setValue(65); editor.banner_text.setPlainText("Custom banner")
    editor.font_size.setValue(30); editor.gif_speed.setValue(150)
    editor.notify_enabled.setChecked(True); editor.notify_duration.setValue(22); editor.notify_font.setValue(25)
    editor.corner.enabled.setChecked(True); editor.corner.add_paths([image])
    editor.corner.order.setCurrentIndex(editor.corner.order.findData("shuffle"))
    editor.corner.loop.setCurrentIndex(editor.corner.loop.findData(False))
    editor.corner.cycle.setChecked(True); editor.corner.cycle_seconds.setValue(3)
    editor.corner.keep.setChecked(False); editor.corner.width_spin.setValue(250)
    editor.corner.height_spin.setValue(200); editor.corner.opacity.setValue(45)
    editor.corner.mode.setCurrentIndex(editor.corner.mode.findData("fill"))
    editor.corner.trim.setChecked(True); editor.corner.on_top.setChecked(False)
    editor.realert_infinite.setChecked(True); assert not editor.realert_count.isEnabled()
    editor.restart_audio.setChecked(False); editor.reopen_banner.setChecked(False)
    for _ in range(5):
        editor.upcoming_times()
    assert repo.db.total_changes == before
    editor.save()
    saved = editor.result_alarm
    assert saved is not None
    controller.save_alarm(saved)
    loaded = repo.alarm(alarm.id)
    assert loaded.to_dict() == saved.to_dict()
    assert loaded.banner.x == -22 and loaded.banner.opacity == .65
    assert loaded.notify == Notify(True, 22, 25)
    assert loaded.corner.order == "shuffle" and not loaded.corner.loop
    assert loaded.corner.width == 250 and loaded.corner.height == 200 and loaded.corner.opacity == 45
    assert loaded.realert.count is None and not loaded.realert.restart_audio
    editor.deleteLater(); controller.shutdown(); controller.deleteLater(); flush(app)


@pytest.mark.parametrize("date,elapsed", [("2025-03-08", 23), ("2025-11-01", 25)])
@pytest.mark.parametrize("unit,amount,expected_hours", [("hours", 24, 24), ("minutes", 1440, 24), ("seconds", 86400, 24), ("days", 1, None), ("weeks", 1, None)])
def test_calendar_and_elapsed_intervals_across_dst(date, elapsed, unit, amount, expected_hours):
    zone = ZoneInfo("America/Toronto")
    anchor = datetime.fromisoformat(date + "T08:00:00").replace(tzinfo=zone).astimezone(UTC)
    schedule = Schedule("interval", date, "08:00:00", interval=amount, interval_unit=unit)
    value = next_occurrence(schedule, anchor, zone)
    hours = expected_hours if expected_hours is not None else elapsed + (144 if unit == "weeks" else 0)
    assert (value-anchor).total_seconds() == hours * 3600
    if expected_hours is None:
        assert value.astimezone(zone).hour == 8


def test_mixed_schedules_realert_snooze_and_restart_controlled_clock(tmp_path):
    path = tmp_path / "simulation.sqlite3"
    repo = Repository(path)
    base = datetime(2025, 6, 2, 8, tzinfo=UTC).timestamp()
    schedules = [Schedule("once", "2025-06-02", "08:00:00"), Schedule("daily", "2025-06-02", "08:00:00"),
                 Schedule("weekly", "2025-06-02", "08:00:00", [0]), Schedule("interval", "2025-06-02", "08:00:00", interval=5, interval_unit="minutes")]
    for i, schedule in enumerate(schedules):
        repo.save_alarm(Alarm(name=str(i), schedule=schedule, effective_at=base-10, realert=ReAlert(2, 60)))
    repo.set("last_tick", base-2)
    engine = AlarmEngine(repo, UTC)
    assert engine.tick(base-1) == []
    initial = engine.tick(base)
    assert len(initial) == 4 and engine.tick(base) == []
    first, second, third, fourth = [event.occurrence_id for event in initial]
    engine.snooze(first, base+10, 120)
    engine.dismiss(second)
    engine.stop_sound(third)
    assert len(engine.tick(base+60)) == 2
    engine.dismiss(first)
    assert len(engine.tick(base+120)) == 2
    engine.snooze(fourth, base+121, 60)
    repo.close()
    repo = Repository(path); engine = AlarmEngine(repo, UTC)
    restored = engine.restore(base+150)
    assert [(event.occurrence_id, event.reason) for event in restored] == [(third, "restore")]
    wake = engine.tick(base+181)
    assert [(event.occurrence_id, event.reason) for event in wake] == [(fourth, "snooze")]
    assert engine.tick(base+181) == []
    assert repo.occurrence(first).status == repo.occurrence(second).status == "dismissed"
    assert len(repo.history()) == 4
    repo.close()


@pytest.mark.parametrize("reopen", [True, False])
def test_manually_opened_banner_respects_realert_reopen_setting(app, repo, reopen):
    controller = Controller(repo)
    alarm = Alarm(realert=ReAlert(1, 10, reopen_banner=reopen))
    controller.save_alarm(alarm)
    events = controller.engine.trigger_now(alarm, 100)
    controller.process(events)
    owner = events[-1].occurrence_id
    assert not controller.banners
    controller.show_banner(owner)
    controller.banners[owner].close()
    assert not controller.banners
    controller.process(controller.engine.tick(110))
    assert bool(controller.banners) == reopen
    controller.shutdown(); controller.deleteLater(); flush(app)


def test_manual_banner_restores_but_notifications_and_corner_stay_hidden(app, tmp_path, image):
    path = tmp_path / "restore.sqlite3"
    repo = Repository(path)
    controller = Controller(repo)
    alarm = Alarm(message="Only on activation", notify=Notify(True), corner=Corner(enabled=True, images=[image]))
    controller.save_alarm(alarm)
    controller.trigger_now(alarm.id)
    owner = repo.active()[0].id
    controller.show_banner(owner)
    controller.shutdown(); controller.deleteLater(); flush(app); repo.close()
    repo = Repository(path)
    controller = Controller(repo)
    overlay = CornerOverlay(repo)
    controller.corner_events.connect(overlay.handle_events)
    controller.process(controller.engine.restore(repo.get("last_tick") + 1))
    assert owner in controller.banners and controller.banners[owner].isVisible()
    assert controller._notice is None and not overlay.isVisible()
    controller.shutdown(); overlay.shutdown(); controller.deleteLater(); flush(app); repo.close()


@pytest.mark.parametrize("kind,unit,amount,days", [
    ("daily", "days", 1, [1, 2, 3]), ("weekly", "days", 1, [2, 4, 7]),
    ("interval", "days", 2, [2, 4, 6]), ("interval", "weeks", 1, [7, 14, 21]),
    ("interval", "hours", 24, [1, 2, 3]),
])
def test_upcoming_preview_exact_three_times_with_frozen_clock(app, repo, monkeypatch, kind, unit, amount, days):
    import alarmclock.ui.editor as module
    frozen = datetime(2025, 6, 2, 8, tzinfo=UTC)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)
    monkeypatch.setattr(module, "datetime", Clock)
    controller = Controller(repo); controller.engine.zone = UTC
    alarm = Alarm(schedule=Schedule(kind, "2025-06-02", "08:00:00", [0, 2, 4], amount, unit))
    editor = AlarmEditor(alarm, controller)
    before = repo.db.total_changes
    problem, upcoming = editor.upcoming_times()
    assert not problem and upcoming == [frozen + timedelta(days=day) for day in days]
    assert repo.db.total_changes == before and not repo.active() and not repo.alarms()
    editor.reject(); editor.deleteLater(); controller.shutdown(); controller.deleteLater(); flush(app)


@pytest.mark.parametrize("width,height", [(850, 770), (680, 540)])
def test_editor_tabs_have_no_horizontal_scroll_and_preserve_dialog_size(app, repo, width, height):
    controller = Controller(repo)
    apply_theme(app, "dark")
    editor = AlarmEditor(None, controller)
    editor.resize(width, height); editor.show(); flush(app)
    size = editor.size()
    for index in range(editor.tabs.count()):
        editor.tabs.setCurrentIndex(index); flush(app)
        assert editor.tabs.currentWidget().horizontalScrollBar().maximum() == 0
        assert editor.size() == size and editor.isVisible()
    editor.reject(); editor.deleteLater(); controller.shutdown(); controller.deleteLater(); flush(app)


def test_opening_editors_releases_widgets_media_and_audio_signals(app, repo):
    import shiboken6
    controller = Controller(repo)
    flush(app)
    initial = len(app.topLevelWidgets())
    closed = []
    for _ in range(20):
        editor = AlarmEditor(None, controller)
        editor.show(); flush(app); editor.reject(); editor.deleteLater(); closed.append(editor); flush(app)
    assert all(not shiboken6.isValid(editor) for editor in closed)
    assert len(app.topLevelWidgets()) == initial
    assert controller.audio.current is None and not controller.audio.pending
    assert not controller.audio.playback.gap_timer.isActive()
    controller.shutdown(); controller.deleteLater(); flush(app)
