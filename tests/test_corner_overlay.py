"""Corner overlay tests for the PER-ALARM architecture: alarm-only visibility, per-alarm config/carousel, hover-to-close,
timed cycling, geometry, exit.

Corner Image settings live on each Alarm (models.Corner); the overlay reads them from the activated occurrence's alarm
snapshot and keeps a carousel per alarm id. There is no global corner configuration. Images are created in pytest's temp
directory for the tests ONLY; the application never generates, bundles or substitutes media. Engine events are driven with
explicit timestamps (no sleeping) through a real Controller, so the production signal path is exercised.
"""
from types import SimpleNamespace
from datetime import datetime
import os
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QEnterEvent, QGuiApplication, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from alarmclock.carousel import Carousel
from alarmclock.controller import Controller
from alarmclock.models import Alarm, Corner, ReAlert, Schedule, UTC
from alarmclock.ui.corner_overlay import CornerConfig, CornerOverlay, alpha_bounds
from alarmclock.ui.corner_settings import CornerImageSettings
from alarmclock.ui.editor import AlarmEditor

BASE = datetime(2025, 6, 1, 8, tzinfo=UTC).timestamp()


def _png(path, width=300, height=200, color=0xFFFF0000):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    for x in range(40, width - 40):
        for y in range(30, height - 30):
            image.setPixel(x, y, color)
    assert image.save(str(path))
    return str(path)


def _images(folder, count=3, prefix="img"):
    folder.mkdir(exist_ok=True)
    return [_png(folder / f"{prefix}{i}.png", 300, 200, 0xFF000000 | (0x40 * (i + 1))) for i in range(count)]


def _corner(paths, **kw):
    values = dict(enabled=True, images=list(paths), width=300, height=300, mode="fit")
    values.update(kw)
    return Corner(**values)


def _alarm(repo, corner, name="User alarm", count=0, snooze=60, scheduled=True):
    a = Alarm(name=name, schedule=Schedule("once", "2025-06-01", "08:00:00") if scheduled else Schedule(), realert=ReAlert(count, 300),
              snooze_seconds=snooze, effective_at=BASE - 60, corner=corner)
    repo.save_alarm(a)
    if scheduled:
        repo.set("last_tick", BASE - 1)
    return a


@pytest.fixture
def rig(app, repo, tmp_path):
    """Controller + the one shared overlay wired exactly like Dashboard wires them. `paths` are 3 imported images."""
    controller = Controller(repo)
    controller.engine.zone = UTC
    overlay = CornerOverlay(repo)
    controller.corner_events.connect(overlay.handle_events)
    overlay.start()
    shown = []
    overlay.shown.connect(shown.append)
    yield SimpleNamespace(controller=controller, overlay=overlay, repo=repo, paths=_images(tmp_path), shown=shown, tmp=tmp_path)
    overlay.shutdown()
    controller.shutdown()


def tick(rig, seconds_after_base):
    rig.controller.process(rig.controller.engine.tick(BASE + seconds_after_base))
    QApplication.processEvents()


def hidden(overlay):
    return not overlay.isVisible() and not overlay.session_open


def next_index(rig, alarm):
    return Carousel(rig.repo, scope=alarm.id).next_index


# ---------------------------------------------------------------------------------------------------
# Per-alarm isolation (replaces the removed global-configuration tests)
# ---------------------------------------------------------------------------------------------------
def test_two_alarms_keep_independent_corner_settings_and_images(rig):
    b_paths = _images(rig.tmp / "b", 2, "b")
    a = _alarm(rig.repo, _corner(rig.paths, width=111, order="sequential"), "A", scheduled=False)
    b = _alarm(rig.repo, _corner(b_paths, width=222, order="shuffle", loop=False, trim=True, monitor="X"), "B", scheduled=False)
    ra, rb = rig.repo.alarm(a.id).corner, rig.repo.alarm(b.id).corner
    assert ra.images == rig.paths and rb.images == b_paths and (ra.width, rb.width) == (111, 222)
    assert (ra.order, rb.order, ra.loop, rb.loop, ra.trim, rb.trim, rb.monitor) == ("sequential", "shuffle", True, False, False, True, "X")


def test_activation_shows_its_own_alarms_image_and_settings(rig):
    b_paths = _images(rig.tmp / "b", 2, "b")
    a = _alarm(rig.repo, _corner(rig.paths, width=111, height=111, opacity=80), "A", scheduled=False)
    b = _alarm(rig.repo, _corner(b_paths, width=222, height=222), "B", scheduled=False)
    rig.controller.trigger_now(b.id)
    QApplication.processEvents()
    o = rig.overlay
    assert o.isVisible() and rig.shown == [b_paths[0]] and o.alarm_id == b.id and o.config.width == 222
    assert next_index(rig, b) == 1 and next_index(rig, a) == 0
    o.close_overlay()
    rig.controller.trigger_now(a.id)
    QApplication.processEvents()
    assert rig.shown == [b_paths[0], rig.paths[0]] and o.alarm_id == a.id and o.config.width == 111 and o.config.opacity == 80
    assert next_index(rig, a) == 1 and next_index(rig, b) == 1


def test_unrelated_alarm_does_not_inherit_or_replace_a_configuration(rig):
    a = _alarm(rig.repo, _corner(rig.paths), "A", scheduled=False)
    plain = Alarm(name="No corner")
    rig.repo.save_alarm(plain)
    rig.controller.trigger_now(plain.id)  # nothing configured: nothing shown
    QApplication.processEvents()
    assert hidden(rig.overlay) and rig.shown == [] and next_index(rig, plain) == 0
    rig.controller.trigger_now(a.id)
    rig.controller.trigger_now(plain.id)  # an unrelated activation leaves A's picture and carousel alone
    QApplication.processEvents()
    assert rig.overlay.isVisible() and rig.overlay.alarm_id == a.id and rig.shown == [rig.paths[0]]
    assert next_index(rig, a) == 1 and next_index(rig, plain) == 0
    assert rig.repo.alarm(plain.id).corner == Corner()


def test_enabled_but_empty_or_disabled_corner_never_shows(rig):
    _alarm(rig.repo, _corner([]), "empty")
    _alarm(rig.repo, _corner(rig.paths, enabled=False), "off")
    tick(rig, 0)
    assert len(rig.repo.active()) == 2 and hidden(rig.overlay) and rig.shown == []


# ---------------------------------------------------------------------------------------------------
# Nothing the user does (start-up, restore, settings UI) may show the overlay
# ---------------------------------------------------------------------------------------------------
def test_enabled_alarm_with_images_stays_hidden_at_startup(app, repo, tmp_path):
    a = _alarm(repo, _corner(_images(tmp_path)), scheduled=False)
    controller = Controller(repo)
    overlay = CornerOverlay(repo)
    controller.corner_events.connect(overlay.handle_events)
    overlay.start()
    controller.start()  # the real start-up path (engine.restore)
    controller.timer.stop()
    QTest.qWait(50)
    assert hidden(overlay) and Carousel(repo, scope=a.id).next_index == 0
    overlay.shutdown()
    controller.shutdown()


def test_restored_occurrence_neither_shows_nor_advances(app, repo, tmp_path):
    a = _alarm(repo, _corner(_images(tmp_path)))
    first = Controller(repo)
    first.engine.zone = UTC
    first.process(first.engine.tick(BASE))  # a previous run activated the alarm (no overlay attached)
    first.shutdown()
    second = Controller(repo)
    overlay = CornerOverlay(repo)
    second.corner_events.connect(overlay.handle_events)
    overlay.start()
    events = []
    second.corner_events.connect(events.append)
    second.start()
    second.timer.stop()
    assert any(reason == "restore" for batch in events for _, _, reason in batch)  # the restore event did arrive
    assert hidden(overlay) and Carousel(repo, scope=a.id).next_index == 0
    overlay.shutdown()
    second.shutdown()


def test_opening_the_editor_and_corner_settings_never_shows_overlay(rig):
    a = _alarm(rig.repo, _corner(rig.paths), scheduled=False)
    settings = CornerImageSettings(rig.repo.alarm(a.id).corner)
    settings.show()
    editor = AlarmEditor(rig.repo.alarm(a.id), rig.controller)
    editor.show()
    QApplication.processEvents()
    settings.enabled.setChecked(False)
    settings.enabled.setChecked(True)
    settings.width_spin.setValue(120)
    settings.opacity.setValue(70)
    settings.on_top.setChecked(False)
    QTest.qWait(200)
    assert hidden(rig.overlay) and rig.shown == []
    assert rig.repo.alarm(a.id).corner.width == 300  # the draft is not saved until the editor's Save
    editor.reject()
    settings.close()
    assert hidden(rig.overlay)


# ---------------------------------------------------------------------------------------------------
# The alarm lifecycle
# ---------------------------------------------------------------------------------------------------
def test_scheduled_activation_shows_first_image_flush_in_bottom_right(rig):
    _alarm(rig.repo, _corner(rig.paths))
    tick(rig, 0)
    o = rig.overlay
    assert o.isVisible() and o.session_open and rig.shown == [rig.paths[0]]
    screen = QGuiApplication.primaryScreen().geometry()
    g = o.geometry()
    assert g.x() + g.width() == screen.x() + screen.width() and g.y() + g.height() == screen.y() + screen.height()


def test_trigger_now_shows_overlay(rig):
    a = _alarm(rig.repo, _corner(rig.paths), "Manual", scheduled=False)
    rig.controller.trigger_now(a.id)
    QApplication.processEvents()
    assert rig.overlay.isVisible() and rig.shown == [rig.paths[0]]


def test_realert_shows_and_advances_exactly_once_per_activation(rig):
    _alarm(rig.repo, _corner(rig.paths), count=2)
    tick(rig, 0)
    tick(rig, 1)  # nothing is due: no extra advance
    assert rig.shown == [rig.paths[0]]
    tick(rig, 300)
    tick(rig, 301)
    tick(rig, 600)
    assert rig.shown == rig.paths  # A, then B on the first re-alert, then C
    assert rig.overlay.isVisible()


def test_snooze_hides_without_advancing_and_expiry_shows_next_image(rig):
    a = _alarm(rig.repo, _corner(rig.paths))
    tick(rig, 0)
    o = rig.repo.active()[0]
    before = next_index(rig, a)
    rig.controller.process(rig.controller.engine.snooze(o.id, BASE + 10, 60))
    assert hidden(rig.overlay) and next_index(rig, a) == before  # pressing Snooze never advances
    tick(rig, 69)
    assert hidden(rig.overlay)
    tick(rig, 71)
    assert rig.overlay.isVisible() and rig.shown == [rig.paths[0], rig.paths[1]]


def test_dismissing_the_owning_occurrence_hides_overlay(rig):
    _alarm(rig.repo, _corner(rig.paths))
    tick(rig, 0)
    rig.controller.dismiss(rig.repo.active()[0].id)
    assert hidden(rig.overlay)


def test_dismissing_an_unrelated_alarm_keeps_overlay_while_owner_remains(rig):
    _alarm(rig.repo, _corner(rig.paths), "one")
    a2 = Alarm(name="two")
    rig.repo.save_alarm(a2)
    tick(rig, 0)
    rig.controller.trigger_now(a2.id)
    o1, o2 = rig.repo.active()
    assert rig.overlay.isVisible()
    rig.controller.dismiss(o2.id)  # the unrelated (no corner image) occurrence does not own the overlay
    assert rig.overlay.isVisible()
    rig.controller.dismiss(o1.id)
    assert hidden(rig.overlay)


def test_simultaneous_alarms_use_one_window_and_advance_only_the_winner(rig):
    other = _images(rig.tmp / "o", 2, "o")
    a = _alarm(rig.repo, _corner(rig.paths), "one")
    b = _alarm(rig.repo, _corner(other), "two")
    tick(rig, 0)
    assert len(rig.repo.active()) == 2 and len(rig.shown) == 1
    winner, loser = (a, b) if rig.overlay.alarm_id == a.id else (b, a)
    assert next_index(rig, winner) == 1 and next_index(rig, loser) == 0
    assert len([w for w in QApplication.topLevelWidgets() if w.windowTitle() == "Custom Alarm Clock corner image" and w.isVisible()]) == 1


def test_missing_file_is_not_replaced_by_another_picture(rig):
    os.remove(rig.paths[0])
    _alarm(rig.repo, _corner(rig.paths))
    tick(rig, 0)
    assert hidden(rig.overlay) and rig.shown == []
    assert "cannot be found" in rig.overlay.load_error or "File not found" in rig.overlay.load_error


# ---------------------------------------------------------------------------------------------------
# Hover x button
# ---------------------------------------------------------------------------------------------------
def _enter(overlay, point=QPoint(10, 10)):
    local = QPointF(point)
    overlay.cursor_position = lambda: overlay.mapToGlobal(point)
    QApplication.sendEvent(overlay, QEnterEvent(local, local, QPointF(overlay.mapToGlobal(point))))


def _leave(overlay, outside=True):
    overlay.cursor_position = (lambda: QPoint(-500, -500)) if outside else (lambda: overlay.mapToGlobal(overlay.close_button.geometry().center()))
    QApplication.sendEvent(overlay, QEvent(QEvent.Type.Leave))


def _ring(rig):
    a = _alarm(rig.repo, _corner(rig.paths), count=3)
    tick(rig, 0)
    assert rig.overlay.isVisible()
    return rig.overlay


def test_close_button_is_hidden_until_hover_and_hides_when_cursor_leaves(rig):
    o = _ring(rig)
    assert not o.close_button.isVisible()  # there is no permanent close button
    _enter(o)
    assert o.close_button.isVisible()
    _leave(o, outside=False)  # Qt sends Leave when the cursor moves onto the child x: must not flicker away
    assert o.close_button.isVisible()
    _leave(o, outside=True)
    assert not o.close_button.isVisible() and not o.hover_guard.isActive()
    assert o.isVisible()  # hovering/leaving never closes the overlay


def test_hover_guard_clears_a_stuck_close_button(rig):
    o = _ring(rig)
    _enter(o)
    o.cursor_position = lambda: QPoint(-500, -500)  # the Leave event was lost
    o._hover_check()
    assert not o.close_button.isVisible()


def test_close_button_is_inside_top_right_and_within_the_monitor(rig):
    o = _ring(rig)
    _enter(o)
    b = o.close_button.geometry()
    assert o.rect().contains(b) and b.right() >= o.width() - 12 and b.top() <= 12
    screen = QGuiApplication.primaryScreen().geometry()
    assert screen.contains(o.mapToGlobal(b.topLeft())) and screen.contains(o.mapToGlobal(b.bottomRight()))


def test_overlay_receives_mouse_input_but_takes_no_focus(rig):
    o = _ring(rig)
    assert not (o.windowFlags() & Qt.WindowType.WindowTransparentForInput)
    assert o.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus and o.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert o.hasMouseTracking()


def test_clicking_x_closes_overlay_only_and_does_not_dismiss_the_alarm(rig):
    o = _ring(rig)
    occurrence = rig.repo.active()[0]
    audio_before = rig.controller.audio.current
    _enter(o)
    QTest.mouseClick(o.close_button, Qt.MouseButton.LeftButton)
    assert hidden(o) and not o.close_button.isVisible()
    after = rig.repo.occurrence(occurrence.id)
    assert after.status == "active" and not after.sound_stopped and not after.banner_closed
    assert rig.controller.audio.current == audio_before
    assert rig.repo.alarm(occurrence.alarm_id).enabled


def test_manually_closed_overlay_stays_closed_for_unrelated_events_then_reopens_for_next_activation(rig):
    o = _ring(rig)
    o.close_overlay()
    o.refresh()
    o._screens_changed()
    o._screen_geometry()
    rig.controller.changed.emit()
    QTest.qWait(400)
    assert hidden(o) and len(rig.shown) == 1
    tick(rig, 300)  # a genuine re-alert of the still-active alarm
    assert o.isVisible() and rig.shown == [rig.paths[0], rig.paths[1]]


def test_monitor_events_reposition_but_never_open(rig):
    o = rig.overlay
    o._screens_changed()
    o._screen_geometry()
    QTest.qWait(300)
    assert hidden(o)
    _ring(rig)
    o._screens_changed()
    QTest.qWait(300)
    screen = QGuiApplication.primaryScreen().geometry()
    g = o.geometry()
    assert o.isVisible() and g.right() == screen.right() and g.bottom() == screen.bottom()


# ---------------------------------------------------------------------------------------------------
# Carousel order (per alarm) and optional timed cycling
# ---------------------------------------------------------------------------------------------------
def test_sequential_loop_and_saved_position_per_alarm(rig):
    a = _alarm(rig.repo, _corner(rig.paths), count=None)
    for step in (0, 300, 600, 900):
        tick(rig, step)
    assert rig.shown == [rig.paths[0], rig.paths[1], rig.paths[2], rig.paths[0]]
    restarted = CornerOverlay(rig.repo)
    restarted.start()
    assert hidden(restarted) and next_index(rig, a) == 1  # remembered for this alarm; restart did not show or advance
    restarted.shutdown()


def test_shuffle_mode_uses_whole_cycle_before_repeating(rig):
    _alarm(rig.repo, _corner(rig.paths, order="shuffle"), count=None)
    for step in range(0, 1500, 300):
        tick(rig, step)
    assert sorted(rig.shown[:3]) == sorted(rig.paths) and len(rig.shown) == 5


def test_finite_mode_stays_on_last_image_without_closing_overlay(rig):
    _alarm(rig.repo, _corner(rig.paths, loop=False), count=None)
    for step in range(0, 1800, 300):
        tick(rig, step)
    assert rig.shown[:3] == rig.paths and set(rig.shown[3:]) == {rig.paths[2]}
    assert rig.overlay.isVisible()


def test_timed_cycling_runs_only_while_an_alarm_overlay_is_visible(rig):
    o = rig.overlay
    _alarm(rig.repo, _corner(rig.paths, cycle=True, cycle_seconds=1), count=0)
    assert hidden(o) and not o.cycle_timer.isActive()  # an armed alarm shows nothing and starts nothing
    QTest.qWait(1300)
    assert rig.shown == []
    tick(rig, 0)
    assert o.cycle_timer.isActive() and rig.shown == [rig.paths[0]]
    QTest.qWait(1300)
    assert len(rig.shown) >= 2 and rig.shown[1] == rig.paths[1]
    o.close_overlay()
    assert not o.cycle_timer.isActive()
    count = len(rig.shown)
    QTest.qWait(1300)
    assert len(rig.shown) == count and hidden(o)


def test_timed_cycling_stops_on_dismiss_and_on_snooze(rig):
    _alarm(rig.repo, _corner(rig.paths, cycle=True, cycle_seconds=5))
    tick(rig, 0)
    o = rig.repo.active()[0]
    assert rig.overlay.cycle_timer.isActive()
    rig.controller.process(rig.controller.engine.snooze(o.id, BASE + 5, 60))
    assert not rig.overlay.cycle_timer.isActive()
    tick(rig, 66)
    assert rig.overlay.cycle_timer.isActive()
    rig.controller.dismiss(o.id)
    assert not rig.overlay.cycle_timer.isActive() and hidden(rig.overlay)


# ---------------------------------------------------------------------------------------------------
# Geometry, trimming, exit
# ---------------------------------------------------------------------------------------------------
def test_overlay_is_flush_with_physical_screen_corner_for_every_mode(rig):
    _alarm(rig.repo, _corner(rig.paths))
    tick(rig, 0)
    o = rig.overlay
    screen = QGuiApplication.primaryScreen().geometry()  # geometry(), not availableGeometry()
    for mode, size in (("fit", 300), ("fit", 150), ("original", 300), ("fill", 120), ("stretch", 200)):
        o.config = CornerConfig.from_corner(_corner(rig.paths, mode=mode, width=size, height=size, keep_aspect=False))
        assert o.place()
        g = o.geometry()
        assert g.x() + g.width() == screen.x() + screen.width() and g.y() + g.height() == screen.y() + screen.height(), mode


def test_application_exit_shuts_the_overlay_down_cleanly(rig):
    _alarm(rig.repo, _corner(rig.paths))
    tick(rig, 0)
    o = rig.overlay
    o.shutdown()
    assert not o.isVisible() and not o.session_open and not o.media
    assert not o.cycle_timer.isActive() and not o.keep_on_top_timer.isActive() and not o.hover_guard.isActive()
    o.shutdown()  # idempotent
    tick(rig, 300)  # events after exit are ignored
    assert not o.isVisible()


def test_no_images_are_bundled_or_generated_by_the_application():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "alarmclock"
    media = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
    assert [p for p in root.rglob("*") if p.suffix.lower() in media] == []


def test_trim_is_in_memory_only(rig):
    path = _png(rig.tmp / "pad.png")
    before = open(path, "rb").read()
    a = _alarm(rig.repo, _corner([path], trim=True), scheduled=False)
    rig.controller.trigger_now(a.id)
    QApplication.processEvents()
    o = rig.overlay
    assert o.original_size().width() == 220 and o.original_size().height() == 140
    assert open(path, "rb").read() == before
    assert alpha_bounds(QImage(path)) == QRect(40, 30, 220, 140)
