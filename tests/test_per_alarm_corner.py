"""Per-alarm Corner Image: persistence + isolation (Qt-free) and the removed Dashboard button (needs PySide6)."""
import json
import random
from alarmclock.carousel import Carousel, sync_alarm
from alarmclock.models import Alarm, Corner
from alarmclock.storage import Repository


def test_corner_config_is_per_alarm_persisted_and_carousel_isolated():
    repo = Repository(":memory:")
    a, b = Alarm(name="A"), Alarm(name="B")
    assert a.corner.enabled is False and a.corner.images == []  # new alarms: disabled
    a.corner = Corner(enabled=True, images=["a1.png", "a2.png", "a3.png"], order="sequential", loop=False, width=111, opacity=40, monitor="X", trim=True)
    b.corner = Corner(enabled=True, images=["b1.gif", "b2.gif"], width=222)
    repo.save_alarm(a); repo.save_alarm(b)
    ra, rb = repo.alarm(a.id), repo.alarm(b.id)  # round-trip through the existing SQLite serialization
    assert ra.corner == a.corner and rb.corner == b.corner and ra.corner.width != rb.corner.width
    # backward compatibility: a record saved before this change has no "corner" key
    old = a.to_dict(); old.pop("corner")
    assert Alarm.from_dict(json.loads(json.dumps(old))).corner == Corner()
    # independent progression keyed by stable alarm id (finite A stays on its last image, B keeps looping)
    ca, cb = Carousel(repo, random.Random(1), a.id), Carousel(repo, random.Random(1), b.id)
    assert [ca.select(ra.corner.images, "sequential", False) for _ in range(4)] == ["a1.png", "a2.png", "a3.png", "a3.png"]
    assert cb.select(rb.corner.images, "sequential", True) == "b1.gif"
    assert cb.select(rb.corner.images, "sequential", True) == "b2.gif"
    assert ca.next_index == 3 and cb.next_index == 2
    # editing A's list keeps A's position and never touches B
    new = Corner(**{**a.corner.__dict__, "images": ["a0.png"] + a.corner.images})
    sync_alarm(repo, a.id, a.corner, new)
    assert cb.next_index == 2 and Carousel(repo, scope=a.id).next_index == 3
    # duplicate: copies the configuration, starts at its own position (keys are per new id)
    copy = ra.clone()
    assert copy.id != a.id and copy.corner == ra.corner and Carousel(repo, scope=copy.id).next_index == 0
    # deleting an alarm forgets only its carousel state
    Carousel(repo, scope=a.id).forget()
    assert Carousel(repo, scope=a.id).next_index == 0 and cb.next_index == 2


def test_dashboard_has_no_corner_image_button_but_keeps_the_others(app, repo, tmp_path):
    from PySide6.QtWidgets import QPushButton
    from alarmclock.controller import Controller
    from alarmclock.ui.dashboard import Dashboard
    from alarmclock.ui.theme import apply_theme
    apply_theme(app, "dark")
    window = Dashboard(Controller(repo), tmp_path)
    texts = {b.text() for b in window.findChildren(QPushButton)}
    assert "Corner Image" not in texts and not hasattr(window, "corner_image_button")
    assert {"+ Add Alarm", "Edit Alarm", "Duplicate", "Delete", "Trigger Now", "Settings"} <= texts
    # The redundant toggle button was removed; the alarm's On checkbox retains this action.
    assert "Enable / Disable" not in texts
    window.close()
