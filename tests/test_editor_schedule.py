"""Schedule tab: per-kind row visibility and the read-only 'Upcoming Alarm Times' preview (Qt)."""
from datetime import datetime
from alarmclock.controller import Controller
from alarmclock.models import Alarm, Schedule, UTC
from alarmclock.ui.editor import AlarmEditor


def test_rows_follow_kind_and_preview_is_read_only_and_per_alarm(app, repo):
    controller = Controller(repo)
    controller.engine.zone = UTC
    a = Alarm(name="A", schedule=Schedule("daily", "2020-01-01", "07:00:00"))
    b = Alarm(name="B", schedule=Schedule("interval", "2020-01-01", "00:00:00", interval=90, interval_unit="minutes"))
    repo.save_alarm(a); repo.save_alarm(b)
    editor = AlarmEditor(repo.alarm(a.id), controller)
    f = editor.sched_form
    hidden = lambda w: f.labelForField(w).isHidden() and w.isHidden()
    for kind, days, interval in (("once", True, True), ("daily", True, True), ("weekly", False, True), ("interval", True, False)):
        editor.kind.setCurrentIndex(editor.kind.findData(kind))
        assert hidden(editor.days_row) == days and hidden(editor.interval_row) == interval, kind
    editor.kind.setCurrentIndex(editor.kind.findData("daily"))
    problem, found = editor.upcoming_times()
    assert problem == "" and len(found) == 3 and all(v.hour == 7 and v.minute == 0 for v in found) and found[0] < found[1] < found[2]
    assert len(editor.upcoming.text().splitlines()) == 3
    editor.kind.setCurrentIndex(editor.kind.findData("weekly"))
    for w in editor.weekdays:
        w.setChecked(False)
    assert editor.upcoming_times()[0] and "weekday" in editor.upcoming.text()
    editor.kind.setCurrentIndex(editor.kind.findData("once"))  # 2020-01-01 is in the past
    assert editor.upcoming.text() == "No future occurrences"
    # nothing was saved: both stored alarms and the engine checkpoint are untouched
    assert repo.alarm(a.id).schedule == a.schedule and repo.alarm(b.id).schedule == b.schedule and repo.get("last_tick") is None
    editor.enabled.setChecked(True)
    editor.save()
    assert editor.result_alarm is None and "past" in editor.error_label.text()
    editor.deleteLater()
    controller.shutdown()
