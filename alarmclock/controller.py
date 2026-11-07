"""Qt coordination between the persistent engine, banners and a single audio queue."""
from __future__ import annotations
import logging
import time
from PySide6.QtCore import QObject, QTimer, Signal
from tzlocal import get_localzone, reload_localzone
from .engine import AlarmEngine
from .media import AudioQueue
from .carousel import Carousel, sync_alarm
from .models import Alarm
from .storage import Repository
from .ui.banner import BannerWindow

class Controller(QObject):
    changed = Signal()
    error = Signal(str)
    activated = Signal(str)
    notice = Signal(str)
    # One emission per processed batch of engine events: a list of (kind, occurrence_id, reason) tuples. This is what
    # drives the corner overlay. Unlike `activated`, restored occurrences are reported with reason "restore" so the
    # overlay can ignore them (a restore is not a fresh alarm activation).
    corner_events = Signal(object)

    def __init__(self, repository: Repository, parent=None):
        super().__init__(parent)
        self.repo = repository
        self.engine = AlarmEngine(repository, get_localzone())
        self.audio = AudioQueue(self)
        self.audio.error.connect(self._audio_error)
        self.audio.finished.connect(self._audio_finished)
        self.audio.changed.connect(self.changed)
        self.audio.progress.connect(self._audio_progress)
        self.banners = {}
        self._notice = None  # single reusable compact message-notification window
        self._notice_owner = None
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.tick)
        self._zone_checked = 0.0
        self._shutting_down = False

    def start(self):
        self.process(self.engine.restore(time.time()))
        self.timer.start()
        self.changed.emit()

    def tick(self):
        try:
            now = time.time()
            if now - self._zone_checked > 60 or now < self._zone_checked:
                self.engine.zone = reload_localzone()
                self._zone_checked = now
            events = self.engine.tick(now)
            self.process(events)
        except Exception as exc:
            self.timer.stop()
            logging.exception("Scheduler stopped")
            self.report(f"Scheduling stopped because of an error: {exc}\nExit and reopen the app after fixing the problem. See the event log and application log.")

    def report(self, message):
        logging.error(message)
        self.repo.log(time.time(), message)
        self.error.emit(message)

    def process(self, events):
        corner_batch = []
        notice_for = None
        for event in events:
            o = self.repo.occurrence(event.occurrence_id)
            if not o:
                continue
            corner_batch.append((event.kind, o.id, event.reason))
            if event.kind == "activate":
                if o.status != "active":
                    continue
                # Live alarms take priority over editor audio previews.
                for key, _, _ in list(self.audio.pending):
                    if key.startswith("preview:"):
                        self.audio.cancel(key, immediate=True)
                if self.audio.current and self.audio.current.startswith("preview:"):
                    self.audio.cancel(self.audio.current, immediate=True)
                a = o.snapshot
                if event.reason != "restore":
                    self.repo.log(time.time(), f"Activated {a.name} ({event.reason}); additional re-alerts used: {o.additional_done}")
                # A popup remains opt-in; once opened manually its re-alert/restore settings apply.
                if self.repo.get("manual_banner:" + o.alarm_id) == o.id and not o.banner_closed:
                    if event.reason == "realert" and a.realert.reopen_banner:
                        self._close_window(o.id)
                    self.show_banner(o.id)
                if not o.sound_stopped and (event.reason != "realert" or a.realert.restart_audio):
                    self.audio.enqueue(o.id, a.audio, restart=True, checkpoint=o.audio_checkpoint if event.reason == "restore" else None)
                if event.reason != "restore" and a.notify.enabled and a.message.strip():
                    notice_for = o  # latest genuine activation in the batch wins
                self.activated.emit(a.name)
            else:
                self.audio.cancel(o.id, immediate=event.reason == "superseded")
                self._close_window(o.id)
                if o.id == self._notice_owner:
                    self._close_notice()
                self.repo.log(time.time(), f"{event.kind.capitalize()}: {o.snapshot.name}")
        if (notice_for is not None and not self._shutting_down
                and self.repo.occurrence(notice_for.id).status == "active"):
            self._show_notice(notice_for)
        if corner_batch and not self._shutting_down:
            self.corner_events.emit(corner_batch)
        if events:
            self.changed.emit()

    def show_banner(self, occurrence_id):
        """Manual command: open the popup banner window for an active occurrence on demand."""
        o = self.repo.occurrence(occurrence_id)
        if not o or o.status != "active":
            self.notice.emit("That occurrence is not active, so there is no banner to show.")
            return
        existing = self.banners.get(occurrence_id)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        self._close_window(occurrence_id)
        o.banner_closed = False
        with self.repo.transaction():
            self.repo.save_occurrence(o)
            self.repo.set("manual_banner:" + o.alarm_id, o.id)
        banner = BannerWindow(o.snapshot)
        self.banners[occurrence_id] = banner
        banner.dismiss_requested.connect(lambda key=occurrence_id: self.dismiss(key))
        banner.snooze_requested.connect(lambda key=occurrence_id: self.snooze(key))
        banner.stop_requested.connect(lambda key=occurrence_id: self.stop_sound(key))
        banner.banner_closed.connect(lambda key=occurrence_id, w=banner: self._banner_closed(key, w))
        banner.error.connect(self.report)
        banner.open_banner()

    def _show_notice(self, o):
        """Compact notification: reuses BannerWindow in notification mode; text is Alarm.message, settings are the alarm's own."""
        self._close_notice()
        window = BannerWindow(o.snapshot, notification=True)
        self._notice, self._notice_owner = window, o.id
        window.banner_closed.connect(lambda w=window: self._notice_closed(w))
        window.error.connect(self.report)
        window.open_banner()

    def _notice_closed(self, window):
        if self._notice is window:
            self._notice = self._notice_owner = None
        window.deleteLater()

    def _close_notice(self):
        window, self._notice, self._notice_owner = self._notice, None, None
        if window:
            window.close()

    def _banner_closed(self, key, window):
        if self.banners.get(key) is window:
            self.banners.pop(key, None)
            self.engine.close_banner(key)
            window.deleteLater()
            self.changed.emit()

    def _close_window(self, key):
        window = self.banners.pop(key, None)
        if window:
            window.close()
            window.deleteLater()

    def close_banner(self, key):
        self.engine.close_banner(key)
        if key not in self.banners:
            self.notice.emit("No banner popup is open for that occurrence.")
        self._close_window(key)
        self.changed.emit()

    def stop_sound(self, key):
        self.engine.stop_sound(key)
        self.audio.cancel(key)
        self.changed.emit()

    def pause(self, key):
        if self.audio.current == key:
            self.audio.playback.pause()

    def resume(self, key):
        if self.audio.current == key:
            self.audio.playback.resume()

    def snooze(self, key, seconds=None):
        self.process(self.engine.snooze(key, time.time(), seconds))

    def dismiss(self, key):
        self.process(self.engine.dismiss(key))

    def dismiss_all(self):
        for occurrence in list(self.repo.active()):
            self.dismiss(occurrence.id)

    def _audio_error(self, key, message):
        o = self.repo.occurrence(key)
        self.report((o.snapshot.name + ":\n" if o else "Audio preview:\n") + message)

    def _audio_progress(self, key, checkpoint):
        o = self.repo.occurrence(key)
        if o and o.status == "active":
            o.audio_checkpoint = checkpoint
            self.repo.save_occurrence(o)

    def _audio_finished(self, key):
        o = self.repo.occurrence(key)
        if o:
            self.engine.stop_sound(key)
            if not o.snapshot.realert.keep_banner:
                self.close_banner(key)
        self.changed.emit()

    def save_alarm(self, alarm: Alarm):
        alarm.validate()
        old = self.repo.alarm(alarm.id)
        saved = Alarm.from_dict(alarm.to_dict())
        saved.revision = old.revision + 1 if old else alarm.revision
        saved.effective_at = time.time()
        events = []
        with self.repo.transaction():
            self.repo.save_alarm(saved)
            if old:
                events = self.engine.cancel_alarm(alarm.id)
                sync_alarm(self.repo, alarm.id, old.corner, saved.corner)
        alarm.revision, alarm.effective_at = saved.revision, saved.effective_at
        self.process(events)
        self.changed.emit()

    def toggle_alarm(self, alarm_id):
        a = self.repo.alarm(alarm_id)
        if a:
            a.enabled = not a.enabled
            events = []
            with self.repo.transaction():
                if not a.enabled:
                    events = self.engine.cancel_alarm(a.id)
                else:
                    a.effective_at = time.time()
                    a.revision += 1
                self.repo.save_alarm(a)
            self.process(events)
            self.changed.emit()

    def delete_alarm(self, alarm_id):
        with self.repo.transaction():
            events = self.engine.cancel_alarm(alarm_id)
            self.repo.delete_alarm(alarm_id)
            Carousel(self.repo, scope=alarm_id).forget()
            self.repo.unset("manual_banner:" + alarm_id)
        self.process(events)
        self.changed.emit()

    def trigger_now(self, alarm_id):
        a = self.repo.alarm(alarm_id)
        if a:
            self.process(self.engine.trigger_now(a, time.time()))

    def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        self.timer.stop()
        self.audio.shutdown()
        for key in list(self.banners):
            self._close_window(key)
        self._close_notice()
        self.repo.set("last_tick", time.time())
