"""Local calendar schedules, UTC comparisons, explicit DST resolution."""
from __future__ import annotations
from datetime import date, datetime, time, timedelta, tzinfo
from math import floor
from .models import Schedule, UTC


def resolve_local(wall: datetime, zone: tzinfo) -> datetime:
    """First instance of a repeated time; move nonexistent time forward by its gap."""
    wall = wall.replace(tzinfo=None)
    candidates = [wall.replace(tzinfo=zone, fold=f).astimezone(UTC) for f in (0, 1)]
    valid = [v for v in candidates if v.astimezone(zone).replace(tzinfo=None) == wall]
    if valid:
        return min(valid)
    forward = [v for v in candidates if v.astimezone(zone).replace(tzinfo=None) > wall]
    if not forward:
        raise ValueError(f"Cannot resolve local time {wall} in {zone}")
    return min(forward)


def _at(s: Schedule, day: date, zone: tzinfo) -> datetime:
    return resolve_local(datetime.combine(day, time.fromisoformat(s.time)), zone)


def _elapsed_step(s: Schedule) -> int | None:
    if s.kind == "interval" and s.interval_unit in {"seconds", "minutes", "hours"}:
        return s.interval * {"seconds": 1, "minutes": 60, "hours": 3600}[s.interval_unit]
    return None


def occurrence_near(s: Schedule, reference: datetime, zone: tzinfo, *, forward: bool) -> datetime | None:
    """Next strictly after reference, or most recent at/before reference. O(1)/O(7)."""
    reference = reference.astimezone(UTC)
    start = date.fromisoformat(s.start_date)
    anchor = _at(s, start, zone)
    if s.kind == "once":
        return anchor if ((anchor > reference) if forward else (anchor <= reference)) else None
    step = _elapsed_step(s)
    if step is not None:
        n = floor((reference - anchor).total_seconds() / step) + (1 if forward else 0)
        if not forward and n < 0:
            return None
        return anchor + timedelta(seconds=max(0, n) * step)
    local_day = reference.astimezone(zone).date()
    if forward:
        local_day = max(start, local_day)
    elif local_day < start:
        return None
    day_step = (s.interval * (7 if s.interval_unit == "weeks" else 1)) if s.kind == "interval" else 1
    if s.kind == "interval":
        index = (local_day - start).days // day_step
        day = start + timedelta(days=index * day_step)
        value = _at(s, day, zone)
        if forward and value <= reference:
            day += timedelta(days=day_step)
        elif not forward and value > reference:
            day -= timedelta(days=day_step)
        return _at(s, day, zone) if day >= start else None
    direction = 1 if forward else -1
    for offset in range(9):
        day = local_day + timedelta(days=direction * offset)
        if day < start:
            return None
        if s.kind == "weekly" and day.weekday() not in s.weekdays:
            continue
        value = _at(s, day, zone)
        if (value > reference) if forward else (value <= reference):
            return value
    return None


def next_occurrence(s: Schedule, after: datetime, zone: tzinfo) -> datetime | None:
    return occurrence_near(s, after, zone, forward=True)


def latest_due(s: Schedule, after: datetime, through: datetime, zone: tzinfo) -> datetime | None:
    value = occurrence_near(s, through, zone, forward=False)
    return value if value is not None and value > after.astimezone(UTC) else None


def occurrence_key(s: Schedule, at: datetime, zone: tzinfo) -> str:
    if _elapsed_step(s) is not None:
        return at.astimezone(UTC).isoformat()
    # Calendar identity remains the same across a fall-back or local zone change.
    return at.astimezone(zone).date().isoformat() + "T" + s.time


def describe(s: Schedule) -> str:
    if s.kind == "once":
        return f"Once · {s.start_date} at {s.time}"
    if s.kind == "daily":
        return f"Daily · {s.time}"
    if s.kind == "weekly":
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return f"{', '.join(names[i] for i in sorted(s.weekdays))} · {s.time}"
    return f"Every {s.interval} {s.interval_unit} · from {s.start_date} {s.time}"
