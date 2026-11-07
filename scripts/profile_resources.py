"""Isolated audit harness. Requires the project's dependencies and psutil."""
import argparse
import base64
import gc
import json
from pathlib import Path
import sys
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument("project", type=Path)
args = parser.parse_args()
sys.path.insert(0, str(args.project.resolve()))
import psutil
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from alarmclock.controller import Controller
from alarmclock.models import Alarm
from alarmclock.storage import Repository
from alarmclock.ui.dashboard import Dashboard
from alarmclock.ui.editor import AlarmEditor
from alarmclock.ui.theme import apply_theme
from alarmclock.ui.banner import BannerWindow

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
process = psutil.Process()
def settle():
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
def rss():
    settle()
    return round(process.memory_info().rss / 1048576, 3)
with tempfile.TemporaryDirectory() as temp:
    started = time.perf_counter()
    repo = Repository(Path(temp) / "isolated.sqlite3")
    controller = Controller(repo)
    apply_theme(app, "dark")
    dashboard = Dashboard(controller, Path(temp))
    dashboard.show()
    app.processEvents()
    result = {"dashboard_construct_show_ms": round((time.perf_counter()-started)*1000, 3)}
    controller.start()
    QTest.qWait(350)
    start_cpu = sum(process.cpu_times()[:2])
    start_wall = time.perf_counter()
    QTest.qWait(4000)
    result["idle_seconds"] = round(time.perf_counter()-start_wall, 3)
    result["idle_one_core_cpu_percent"] = round((sum(process.cpu_times()[:2])-start_cpu)/result["idle_seconds"]*100, 3)
    # Warm fonts, widget styles and dialogs before measuring retained memory.
    for _ in range(3):
        editor = AlarmEditor(None, controller, dashboard)
        editor.show(); app.processEvents(); editor.reject(); editor.deleteLater(); settle()
    result["rss_after_warmup_mib"] = rss()
    start = time.perf_counter()
    for _ in range(40):
        editor = AlarmEditor(None, controller, dashboard)
        editor.show(); app.processEvents(); editor.reject(); editor.deleteLater(); settle()
    result["editor_40_cycles_ms"] = round((time.perf_counter()-start)*1000, 3)
    result["rss_after_40_editors_mib"] = rss()
    result["remaining_editors"] = len(dashboard.findChildren(AlarmEditor))
    path = Path(temp) / "large.png"
    image = QImage(1600, 1200, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.blue); assert image.save(str(path))
    alarm = Alarm()
    alarm.corner.enabled = True
    alarm.corner.images = [str(path)]
    controller.save_alarm(alarm)
    controller.trigger_now(alarm.id)
    overlay = dashboard.corner_overlay
    cache_keys = set()
    start = time.perf_counter()
    for _ in range(100):
        overlay.place()
        cache_keys.add(overlay._scaled.cacheKey())
    result["placement_100_ms"] = round((time.perf_counter()-start)*1000, 3)
    result["placement_distinct_pixmaps"] = len(cache_keys)
    controller.dismiss(repo.active()[0].id)
    result["rss_before_100_activations_mib"] = rss()
    for _ in range(100):
        controller.trigger_now(alarm.id)
        controller.dismiss(repo.active()[0].id)
        settle()
    result["rss_after_100_activations_mib"] = rss()
    result["overlay_media_after_close"] = overlay.media is not None
    result["overlay_timers_after_close"] = sum(t.isActive() for t in (overlay.cycle_timer, overlay.hover_guard, overlay.keep_on_top_timer))
    gif = Path(temp) / "test-only.gif"
    gif.write_bytes(base64.b64decode("R0lGODlhGAAQAIEAAP8AAAAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQACAAAACwAAAAAGAAQAAAIIgABCBxIsKDBgwgTKlzIsKHDhxAjSpxIsaLFixgzatxoMCAAIfkEAQgAAQAsAAAAABgAEACBAAD/AAAAAAAAAAAACCIAAQgcSLCgwYMIEypcyLChw4cQI0qcSLGixYsYM2rcaDAgADs="))
    gif_alarm = Alarm()
    gif_alarm.banner.path = str(gif)
    banner = BannerWindow(gif_alarm)
    banner.open_banner(); QTest.qWait(200); banner.close(); banner.deleteLater(); settle()
    result["rss_before_100_gif_windows_mib"] = rss()
    for _ in range(100):
        banner = BannerWindow(gif_alarm)
        banner.open_banner(); QTest.qWait(10); banner.close(); banner.deleteLater(); settle()
    result["rss_after_100_gif_windows_mib"] = rss()
    result["remaining_banner_windows"] = sum(isinstance(w, BannerWindow) for w in app.topLevelWidgets())
    dashboard._quitting = True
    dashboard.clock_timer.stop(); dashboard.refresh_timer.stop()
    dashboard.background.clear(); overlay.shutdown(); controller.shutdown()
    dashboard.deleteLater(); controller.deleteLater(); settle(); repo.close()
    print(json.dumps(result, indent=2))
