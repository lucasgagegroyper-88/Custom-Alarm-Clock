"""Native presentation contract and real Qt pixel/activation-path regressions."""
import base64
from datetime import datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QMovie
from PySide6.QtTest import QTest

from alarmclock import platform_windows
from alarmclock.controller import Controller
from alarmclock.models import Alarm, Corner, ReAlert, Schedule, UTC
from alarmclock.ui.dashboard import Dashboard
from test_audit_regressions import GIF


@pytest.mark.parametrize("on_top,expected", [(True, -1), (False, -2)])
def test_win32_presents_without_focus_or_geometry_change(monkeypatch, on_top, expected):
    import ctypes
    calls = []
    class Function:
        def __init__(self, result): self.result = result
        def __call__(self, *args):
            calls.append(args)
            return self.result
    api = SimpleNamespace(SetWindowPos=Function(1), IsWindowVisible=Function(1))
    monkeypatch.setattr(platform_windows.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: api, raising=False)
    platform_windows.show_corner_window(0x123456789, on_top)
    assert calls[0] == (0x123456789, expected, 0, 0, 0, 0, 0x253)
    assert calls[1] == (0x123456789,)
    assert len(api.SetWindowPos.argtypes) == 7  # HWND is pointer-sized on Win64.


def test_native_hidden_window_is_reported(monkeypatch):
    import ctypes
    class Function:
        def __init__(self, result): self.result = result
        def __call__(self, *args): return self.result
    api = SimpleNamespace(SetWindowPos=Function(1), IsWindowVisible=Function(0))
    monkeypatch.setattr(platform_windows.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: api, raising=False)
    with pytest.raises(OSError, match="visible"):
        platform_windows.show_corner_window(123, True)


def test_offscreen_backend_does_not_pass_synthetic_hwnd_to_win32(app, repo, tmp_path, monkeypatch):
    import alarmclock.ui.corner_overlay as module
    calls = []
    monkeypatch.setattr(module.QGuiApplication, "platformName", lambda: "offscreen")
    monkeypatch.setattr(module, "show_corner_window", lambda *args: calls.append(args))
    path = tmp_path / "green.png"
    image = QImage(80, 80, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.green)
    assert image.save(str(path))
    controller = Controller(repo)
    overlay = module.CornerOverlay(repo)
    controller.corner_events.connect(overlay.handle_events)
    try:
        alarm = Alarm(corner=Corner(enabled=True, images=[str(path)]))
        repo.save_alarm(alarm)
        controller.trigger_now(alarm.id)
        app.processEvents()
        assert overlay.isVisible() and not calls
        assert overlay.grab().toImage().pixelColor(40, 40).green() > 200
    finally:
        overlay.shutdown()
        controller.shutdown()
        controller.deleteLater()


def test_delayed_placement_reshows_surface_hidden_during_reposition(app, repo, tmp_path, monkeypatch):
    from alarmclock.ui.corner_overlay import CornerOverlay
    path = tmp_path / "surface.png"
    image = QImage(80, 80, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.red)
    assert image.save(str(path))
    controller = Controller(repo)
    overlay = CornerOverlay(repo)
    controller.corner_events.connect(overlay.handle_events)
    try:
        alarm = Alarm(corner=Corner(enabled=True, images=[str(path)]))
        repo.save_alarm(alarm)
        controller.trigger_now(alarm.id)
        app.processEvents()
        place = overlay.place
        def recreate_surface():
            result = place()
            overlay.hide()  # simulate a platform surface recreation during placement
            return result
        monkeypatch.setattr(overlay, "place", recreate_surface)
        overlay._replace_if_visible()
        assert overlay.session_open and overlay.isVisible()
        assert overlay.windowHandle().isVisible()
        assert overlay.grab().toImage().pixelColor(40, 40).red() > 200
        overlay.close_overlay()
        overlay._replace_if_visible()
        assert not overlay.isVisible()
    finally:
        overlay.shutdown()
        controller.shutdown()
        controller.deleteLater()


@pytest.mark.parametrize("reason", ["manual", "scheduled", "realert", "snooze"])
@pytest.mark.parametrize("media_kind", ["png", "gif"])
def test_dashboard_activation_presents_and_paints_own_media(app, repo, tmp_path, monkeypatch, reason, media_kind):
    import alarmclock.ui.corner_overlay as module
    native_calls = []
    monkeypatch.setattr(module, "show_corner_window", lambda hwnd, top: native_calls.append((hwnd, top)))
    monkeypatch.setattr(module.QGuiApplication, "platformName", lambda: "windows")
    path = tmp_path / ("selected." + media_kind)
    if media_kind == "gif":
        path.write_bytes(base64.b64decode(GIF))
    else:
        image = QImage(80, 80, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.red)
        assert image.save(str(path))
    base = datetime(2025, 6, 1, 8, tzinfo=UTC).timestamp()
    a = Alarm(name="A", effective_at=base-10, schedule=Schedule("once", "2025-06-01", "08:00:00"),
              realert=ReAlert(1, 10), corner=Corner(enabled=True, images=[str(path)], width=80, height=80))
    repo.save_alarm(a)
    repo.set("last_tick", base-1)
    controller = Controller(repo)
    controller.engine.zone = UTC
    window = Dashboard(controller, tmp_path)
    overlay = window.corner_overlay
    try:
        # Dashboard stays hidden, just as it does when running in the tray.
        assert not window.isVisible()
        if reason == "manual":
            controller.trigger_now(a.id)
        else:
            controller.process(controller.engine.tick(base))
            owner = repo.active()[0].id
            if reason == "realert":
                controller.process(controller.engine.tick(base+10))
            elif reason == "snooze":
                controller.process(controller.engine.snooze(owner, base, 5))
                assert not overlay.isVisible()
                controller.process(controller.engine.tick(base+5))
        app.processEvents()
        assert overlay.current_path == str(path) and native_calls
        assert native_calls[-1] == (int(overlay.winId()), True)
        assert overlay.windowHandle().isVisible()
        pixel = overlay.grab().toImage().pixelColor(40, 40)
        assert pixel.alpha() > 200
        if media_kind == "gif":
            assert max(pixel.red(), pixel.blue()) > 200
            assert overlay.media.movie.state() == QMovie.MovieState.Running
            for _ in range(20):
                QTest.qWait(20)
                next_pixel = overlay.grab().toImage().pixelColor(40, 40)
                if next_pixel != pixel:
                    break
            assert next_pixel != pixel and next_pixel.alpha() > 200
        else:
            assert pixel.red() > 200
        # Closed sessions cannot be resurrected by delayed placement/presentation.
        overlay.close_button.click()
        native_calls.clear()
        overlay._replace_if_visible()
        overlay.refresh()
        assert not overlay.isVisible() and not native_calls
        assert repo.active()[0].status == "active"
    finally:
        window.clock_timer.stop()
        window.refresh_timer.stop()
        overlay.shutdown()
        controller.shutdown()
        window._quitting = True
        window.close()
        window.deleteLater()
        controller.deleteLater()
