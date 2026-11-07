from datetime import datetime
import json
import sqlite3
from uuid import uuid4
import pytest
from alarmclock.models import Alarm, Schedule, ReAlert, UTC
from alarmclock.engine import AlarmEngine
from alarmclock.storage import Repository, SCHEMA_VERSION

BASE = datetime(2025, 6, 1, 8, tzinfo=UTC).timestamp()

def alarm(repo, *, count=0, kind="once"):
    a = Alarm(name="User alarm", schedule=Schedule(kind, "2025-06-01", "08:00:00"), realert=ReAlert(count, 300), effective_at=BASE-60)
    repo.save_alarm(a)
    repo.set("last_tick", BASE-1)
    return a


def test_alarm_roundtrip_all_settings(tmp_path):
    path = tmp_path / "settings.sqlite3"
    r = Repository(path)
    a = alarm(r, count=2)
    a.banner.path = "C:/User/image.gif"
    a.banner.transparent = True
    a.banner.hide_text = True
    a.audio.tracks = ["C:/User/A.mp3", "C:/User/B.mp3"]
    a.audio.cycles = 999
    r.save_alarm(a)
    r.set("theme", "light")
    r.close()
    r = Repository(path)
    assert r.alarm(a.id).to_dict() == a.to_dict()
    assert r.get("theme") == "light"
    r.close()


def test_duplicate_copy_has_independent_identity(repo):
    a = alarm(repo)
    b = a.clone()
    repo.save_alarm(b)
    b.audio.tracks.append("user-only.mp3")
    assert a.id != b.id and not b.enabled
    assert a.audio.tracks == []
    assert len(repo.alarms()) == 2


def test_create_update_delete(repo):
    a = alarm(repo)
    a.name = "Changed"
    repo.save_alarm(a)
    assert len(repo.alarms()) == 1
    assert repo.alarm(a.id).name == "Changed"
    repo.delete_alarm(a.id)
    assert repo.alarm(a.id) is None


def test_schema_migration_from_v1(tmp_path):
    path = tmp_path / "old.sqlite3"
    c = sqlite3.connect(path)
    c.executescript("CREATE TABLE alarms(id TEXT PRIMARY KEY,data TEXT NOT NULL); CREATE TABLE preferences(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE occurrences(id TEXT PRIMARY KEY,alarm_id TEXT NOT NULL,occurrence_key TEXT NOT NULL UNIQUE,status TEXT NOT NULL,data TEXT NOT NULL); PRAGMA user_version=1;")
    a = Alarm()
    c.execute("INSERT INTO alarms VALUES (?,?)", (a.id, json.dumps(a.to_dict())))
    c.commit()
    c.close()
    r = Repository(path)
    assert r.alarm(a.id).id == a.id
    assert r.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    r.log(1, "migration works")
    assert len(r.events()) == 1
    r.close()


def test_future_schema_rejected(tmp_path):
    path = tmp_path / "future.sqlite3"
    c = sqlite3.connect(path)
    c.execute("PRAGMA user_version=999")
    c.close()
    with pytest.raises(RuntimeError, match="newer"):
        Repository(path)


def test_duplicate_trigger_prevention_clock_rewind(repo):
    alarm(repo)
    engine = AlarmEngine(repo, UTC)
    assert len(engine.tick(BASE)) == 1
    assert engine.tick(BASE+1) == []
    assert engine.tick(BASE-1) == []
    assert engine.tick(BASE) == []
    assert len(repo.history()) == 1


def test_restart_restore_not_new_occurrence(tmp_path):
    path = tmp_path / "restart.sqlite3"
    r = Repository(path)
    alarm(r)
    e = AlarmEngine(r, UTC)
    original = e.tick(BASE)[0].occurrence_id
    r.close()
    r = Repository(path)
    events = AlarmEngine(r, UTC).restore(BASE+5)
    assert [(x.occurrence_id, x.reason) for x in events] == [(original, "restore")]
    assert len(r.history()) == 1
    r.close()


def test_restart_preference_cancels_old_realerts(repo):
    alarm(repo, count=2)
    engine = AlarmEngine(repo, UTC)
    engine.tick(BASE)
    repo.set("resume_active", False)
    events = engine.restore(BASE+600)
    assert not any(e.kind == "activate" for e in events)
    assert repo.active() == []


def test_realerts_exact_additional_count(repo):
    alarm(repo, count=2)
    e = AlarmEngine(repo, UTC)
    key = e.tick(BASE)[0].occurrence_id
    assert e.tick(BASE+299) == []
    assert len(e.tick(BASE+300)) == 1
    assert len(e.tick(BASE+600)) == 1
    assert e.tick(BASE+900) == []
    o = repo.occurrence(key)
    assert o.additional_done == 2 and o.next_alert is None


def test_infinite_realerts(repo):
    alarm(repo, count=None)
    e = AlarmEngine(repo, UTC)
    key = e.tick(BASE)[0].occurrence_id
    for i in range(1, 12):
        assert len(e.tick(BASE+i*300)) == 1
    assert repo.occurrence(key).additional_done == 11
    assert repo.occurrence(key).next_alert is not None


def test_snooze_does_not_consume_realert_count(repo):
    alarm(repo, count=2)
    e = AlarmEngine(repo, UTC)
    key = e.tick(BASE)[0].occurrence_id
    e.tick(BASE+300)
    e.snooze(key, BASE+350, 120)
    assert e.tick(BASE+469) == []
    event = e.tick(BASE+470)[0]
    assert event.reason == "snooze"
    o = repo.occurrence(key)
    assert o.additional_done == 1 and o.next_alert == BASE+770
    e.tick(BASE+770)
    assert repo.occurrence(key).additional_done == 2


def test_dismiss_preserves_future_recurring_schedule(repo):
    a = alarm(repo, count=2, kind="daily")
    e = AlarmEngine(repo, UTC)
    key = e.tick(BASE)[0].occurrence_id
    e.dismiss(key)
    assert e.tick(BASE+300) == []
    assert repo.alarm(a.id).enabled
    assert len(e.tick(BASE+86400)) == 1


def test_stop_sound_and_close_do_not_dismiss(repo):
    alarm(repo, count=1)
    e = AlarmEngine(repo, UTC)
    key = e.tick(BASE)[0].occurrence_id
    e.stop_sound(key)
    e.close_banner(key)
    o = repo.occurrence(key)
    assert o.status == "active" and o.next_alert is not None
    assert o.sound_stopped and o.banner_closed
    e.tick(BASE+300)
    o = repo.occurrence(key)
    assert not o.sound_stopped and not o.banner_closed


def test_sleep_resume_coalesces_realerts(repo):
    alarm(repo, count=10)
    e = AlarmEngine(repo, UTC)
    key = e.tick(BASE)[0].occurrence_id
    events = e.tick(BASE+3000)
    assert len(events) == 1
    o = repo.occurrence(key)
    assert o.additional_done == 1 and o.next_alert == BASE+3300


def test_missed_schedule_latest_only(repo):
    alarm(repo, kind="daily")
    repo.set("last_tick", BASE-86400*7)
    repo.set("missed_grace_seconds", 86400*10)
    e = AlarmEngine(repo, UTC)
    assert len(e.tick(BASE+86400*5+60)) == 1
    assert repo.active()[0].scheduled_at == BASE+86400*5


def test_skip_missed_alarm(repo):
    alarm(repo)
    repo.set("missed_policy", "skip")
    assert AlarmEngine(repo, UTC).tick(BASE+60) == []


def test_expired_catchup_grace(repo):
    alarm(repo)
    repo.set("missed_grace_seconds", 30)
    assert AlarmEngine(repo, UTC).tick(BASE+60) == []


def test_disabled_alarm_not_triggered(repo):
    a = alarm(repo)
    a.enabled = False
    repo.save_alarm(a)
    assert AlarmEngine(repo, UTC).tick(BASE) == []


def test_new_alarm_does_not_fire_retroactively(repo):
    a = alarm(repo)
    a.effective_at = BASE+30
    repo.save_alarm(a)
    assert AlarmEngine(repo, UTC).tick(BASE+60) == []


def test_simultaneous_alarms(repo):
    alarm(repo)
    alarm(repo)
    events = AlarmEngine(repo, UTC).tick(BASE)
    assert len(events) == 2 and len(repo.active()) == 2


def test_new_occurrence_supersedes_same_alarm(repo):
    a = alarm(repo, kind="daily")
    e = AlarmEngine(repo, UTC)
    key = e.tick(BASE)[0].occurrence_id
    events = e.tick(BASE+86400)
    assert len(repo.active()) == 1
    assert repo.occurrence(key).status == "superseded"
    assert {x.kind for x in events} == {"activate", "dismiss"}


def test_manual_trigger_unique_and_does_not_change_schedule(repo):
    a = alarm(repo)
    e = AlarmEngine(repo, UTC)
    first = e.trigger_now(a, BASE-20)[-1].occurrence_id
    second = e.trigger_now(a, BASE-20)[-1].occurrence_id
    assert first != second
    assert len(e.tick(BASE)) == 2  # dismiss previous manual + scheduled activation


def test_snooze_persisted_across_restart(repo):
    alarm(repo, count=1)
    e = AlarmEngine(repo, UTC)
    key = e.tick(BASE)[0].occurrence_id
    e.snooze(key, BASE, 600)
    e2 = AlarmEngine(repo, UTC)
    assert e2.restore(BASE+300) == []
    assert e2.tick(BASE+600)[0].reason == "snooze"


@pytest.mark.parametrize("field,value", [("name", ""), ("snooze_seconds", 0)])
def test_invalid_settings_rejected(repo, field, value):
    a = Alarm()
    setattr(a, field, value)
    with pytest.raises(ValueError):
        repo.save_alarm(a)


def test_realert_no_restart_preserves_closed_stopped_state(repo):
    a = alarm(repo, count=1)
    a.realert.restart_audio = False
    a.realert.reopen_banner = False
    repo.save_alarm(a)
    e = AlarmEngine(repo, UTC)
    key = e.tick(BASE)[0].occurrence_id
    e.stop_sound(key)
    e.close_banner(key)
    e.tick(BASE+300)
    o = repo.occurrence(key)
    assert o.sound_stopped and o.banner_closed
