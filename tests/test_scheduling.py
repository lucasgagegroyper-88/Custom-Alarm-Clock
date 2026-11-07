from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from alarmclock.models import Schedule, UTC
from alarmclock.scheduling import next_occurrence, latest_due, resolve_local, occurrence_key

NY = ZoneInfo("America/New_York")

def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def test_once_boundary_and_expiry():
    s = Schedule("once", "2025-06-10", "08:00:00")
    due = utc("2025-06-10T12:00:00")
    assert next_occurrence(s, due - timedelta(seconds=1), NY) == due
    assert next_occurrence(s, due, NY) is None
    assert latest_due(s, due - timedelta(seconds=1), due, NY) == due
    assert latest_due(s, due, due + timedelta(seconds=1), NY) is None


def test_daily_midnight_rollover():
    s = Schedule("daily", "2025-06-01", "00:00:00")
    assert next_occurrence(s, utc("2025-06-11T03:59:59"), NY) == utc("2025-06-11T04:00:00")
    assert next_occurrence(s, utc("2025-06-11T04:00:00"), NY) == utc("2025-06-12T04:00:00")


def test_weekly_selected_days():
    s = Schedule("weekly", "2025-06-01", "08:00:00", [0, 4])
    assert next_occurrence(s, utc("2025-06-09T12:00:00"), NY) == utc("2025-06-13T12:00:00")
    assert latest_due(s, utc("2025-06-01"), utc("2025-06-12T12:00:00"), NY) == utc("2025-06-09T12:00:00")


def test_future_start_date():
    s = Schedule("daily", "2025-12-01", "08:00:00")
    assert next_occurrence(s, utc("2025-01-01"), NY) == utc("2025-12-01T13:00:00")
    assert latest_due(s, utc("2024-01-01"), utc("2025-01-01"), NY) is None


def test_dst_spring_gap_moves_by_gap():
    s = Schedule("daily", "2025-03-01", "02:30:00")
    assert next_occurrence(s, utc("2025-03-09T06:00:00"), NY) == utc("2025-03-09T07:30:00")
    assert resolve_local(datetime(2025, 3, 9, 2, 30), NY).astimezone(NY).hour == 3


def test_dst_fold_rings_only_first_instance():
    s = Schedule("daily", "2025-11-01", "01:30:00")
    first = utc("2025-11-02T05:30:00")
    assert next_occurrence(s, first - timedelta(seconds=1), NY) == first
    assert next_occurrence(s, first, NY) == utc("2025-11-03T06:30:00")
    assert latest_due(s, first, utc("2025-11-02T06:30:00"), NY) is None


def test_half_hour_dst_gap():
    zone = ZoneInfo("Australia/Lord_Howe")
    result = resolve_local(datetime(2025, 10, 5, 2, 15), zone).astimezone(zone)
    assert (result.hour, result.minute) == (2, 45)


@pytest.mark.parametrize("unit,amount,seconds", [("seconds", 5, 5), ("minutes", 3, 180), ("hours", 2, 7200)])
def test_elapsed_interval(unit, amount, seconds):
    s = Schedule("interval", "2025-01-01", "00:00:00", interval=amount, interval_unit=unit)
    anchor = utc("2025-01-01T05:00:00")
    assert next_occurrence(s, anchor, NY) == anchor + timedelta(seconds=seconds)
    assert latest_due(s, anchor, anchor + timedelta(seconds=seconds*9999+1), NY) == anchor + timedelta(seconds=seconds*9999)


@pytest.mark.parametrize("unit,amount,expected", [("days", 2, "2025-03-10T12:00:00"), ("weeks", 1, "2025-03-15T12:00:00")])
def test_calendar_interval_preserves_wall_clock(unit, amount, expected):
    s = Schedule("interval", "2025-03-08", "08:00:00", interval=amount, interval_unit=unit)
    assert next_occurrence(s, utc("2025-03-08T13:00:00"), NY) == utc(expected)


def test_latest_due_is_bounded_after_years():
    s = Schedule("interval", "2000-01-01", "00:00:00", interval=1, interval_unit="seconds")
    end = utc("2030-01-01T00:00:00")
    assert latest_due(s, utc("2000-01-02"), end, NY) == end


def test_calendar_key_zone_independent_date():
    s = Schedule("daily", "2025-01-01", "08:00:00")
    ny = utc("2025-06-10T12:00:00")
    la = utc("2025-06-10T15:00:00")
    assert occurrence_key(s, ny, NY) == occurrence_key(s, la, ZoneInfo("America/Los_Angeles"))


@pytest.mark.parametrize("kind", ["daily", "weekly", "interval"])
def test_next_and_latest_agree_across_year(kind):
    s = Schedule(kind, "2025-01-01", "02:30:00", [1, 3, 6], interval=3, interval_unit="days")
    at = utc("2025-01-01")
    for _ in range(100):
        value = next_occurrence(s, at, NY)
        assert value > at
        assert latest_due(s, at, value, NY) == value
        at = value
