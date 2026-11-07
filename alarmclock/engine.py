"""Persistent, deterministic scheduling/re-alert state machine independent of Qt."""
from __future__ import annotations
from datetime import datetime, tzinfo
from uuid import uuid4
from .models import Alarm, EngineEvent, Occurrence, UTC
from .scheduling import latest_due, occurrence_key
from .storage import Repository

class AlarmEngine:
    def __init__(self, repository: Repository, zone: tzinfo):
        self.repo = repository
        self.zone = zone

    def _activate(self, o: Occurrence, now: float, reason: str) -> EngineEvent:
        o.status = "active"
        o.snooze_until = None
        o.last_activation = now
        o.next_alert = now + o.snapshot.realert.interval_seconds if o.has_more_alerts() else None
        if reason != "realert" or o.snapshot.realert.restart_audio:
            o.sound_stopped = False
            o.audio_checkpoint = None
        if reason != "realert" or o.snapshot.realert.reopen_banner:
            o.banner_closed = False
        self.repo.save_occurrence(o)
        return EngineEvent("activate", o.id, reason)

    def _new(self, alarm: Alarm, scheduled: float, key: str, now: float) -> list[EngineEvent]:
        o = Occurrence(uuid4().hex, alarm.id, key, scheduled, Alarm.from_dict(alarm.to_dict()), last_activation=now)
        o.next_alert = now + alarm.realert.interval_seconds if o.has_more_alerts() else None
        with self.repo.transaction():
            if not self.repo.claim_occurrence(o):
                return []
            events = []
            for old in self.repo.active():
                if old.alarm_id == alarm.id and old.id != o.id:
                    events += self.dismiss(old.id, "superseded")
        events.append(EngineEvent("activate", o.id, "scheduled"))
        return events

    def tick(self, now: float) -> list[EngineEvent]:
        events = []
        last = float(self.repo.get("last_tick", now - 1.0))
        if now < last:
            # Rewind the cursor. Occurrence keys, not the cursor, prevent duplicate rings.
            last = now - 1.0
        grace = max(1, int(self.repo.get("missed_grace_seconds", 86400)))
        lower = max(last, now - (grace if self.repo.get("missed_policy", "latest") == "latest" else 2))
        for alarm in self.repo.alarms():
            if not alarm.enabled:
                continue
            after = datetime.fromtimestamp(max(lower, alarm.effective_at - 0.001), UTC)
            due = latest_due(alarm.schedule, after, datetime.fromtimestamp(now, UTC), self.zone)
            if due:
                key = f"{alarm.id}:{alarm.revision}:{occurrence_key(alarm.schedule, due, self.zone)}"
                events += self._new(alarm, due.timestamp(), key, now)
        newly_started = {e.occurrence_id for e in events if e.kind == "activate"}
        for o in self.repo.active():
            if o.id in newly_started:
                continue
            if o.status == "snoozed" and o.snooze_until is not None and o.snooze_until <= now:
                events.append(self._activate(o, now, "snooze"))
            elif o.status == "active" and o.next_alert is not None and o.next_alert <= now:
                # One catch-up re-alert, never a burst after sleep. Counts reflect actual activations.
                o.additional_done += 1
                events.append(self._activate(o, now, "realert"))
        self.repo.set("last_tick", now)
        return events

    def restore(self, now: float) -> list[EngineEvent]:
        events = []
        if not self.repo.get("resume_active", True):
            for o in self.repo.active():
                events += self.dismiss(o.id, "restart preference")
        events += self.tick(now)
        already = {e.occurrence_id for e in events}
        if self.repo.get("resume_active", True):
            for o in self.repo.active():
                if o.id not in already and o.status == "active":
                    events.append(EngineEvent("activate", o.id, "restore"))
        return events

    def trigger_now(self, alarm: Alarm, now: float) -> list[EngineEvent]:
        return self._new(alarm, now, f"manual:{uuid4().hex}", now)

    def snooze(self, occurrence_id: str, now: float, seconds: int | None = None) -> list[EngineEvent]:
        o = self.repo.occurrence(occurrence_id)
        if not o or o.status not in {"active", "snoozed"}:
            return []
        delay = o.snapshot.snooze_seconds if seconds is None else seconds
        if delay < 1:
            raise ValueError("Snooze must be at least one second.")
        o.status = "snoozed"
        o.snooze_until = now + delay
        o.next_alert = None
        self.repo.save_occurrence(o)
        return [EngineEvent("snooze", o.id)]

    def dismiss(self, occurrence_id: str, reason="dismissed") -> list[EngineEvent]:
        o = self.repo.occurrence(occurrence_id)
        if not o or o.status not in {"active", "snoozed"}:
            return []
        o.status = reason if reason == "superseded" else "dismissed"
        o.next_alert = o.snooze_until = None
        self.repo.save_occurrence(o)
        return [EngineEvent("dismiss", o.id, reason)]

    def cancel_alarm(self, alarm_id: str) -> list[EngineEvent]:
        result = []
        for o in self.repo.active():
            if o.alarm_id == alarm_id:
                result += self.dismiss(o.id)
        return result

    def stop_sound(self, occurrence_id: str):
        o = self.repo.occurrence(occurrence_id)
        if o:
            o.sound_stopped = True
            self.repo.save_occurrence(o)

    def close_banner(self, occurrence_id: str):
        o = self.repo.occurrence(occurrence_id)
        if o:
            o.banner_closed = True
            self.repo.save_occurrence(o)
