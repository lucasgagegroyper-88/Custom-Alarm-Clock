from alarmclock.models import Alarm, Notify


def test_notify_is_per_alarm_default_off_and_cloned():
    a, b = Alarm(), Alarm()
    a.notify = Notify(True, 20, 22)
    assert b.notify == Notify() and not b.notify.enabled
    legacy = a.to_dict()
    legacy.pop("notify")
    assert Alarm.from_dict(legacy).notify.enabled is False  # existing alarms load disabled
    copy = a.clone()
    copy.notify.duration = 99
    assert a.notify.duration == 20 and copy.notify.font_size == 22


def test_notification_uses_message_not_banner_text(app):
    import pytest
    pytest.importorskip("PySide6")
    from alarmclock.ui.banner import notification_config
    a = Alarm(message="Time to wake up.")
    a.banner.text = "GOOD MORNING!"
    cfg = notification_config(a)
    assert cfg.text == "Time to wake up." and cfg.path == "" and not cfg.fullscreen
