"""Serializable settings. No media is ever supplied or substituted by the application."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, time, timezone
from typing import Any
from uuid import uuid4

UTC = timezone.utc

def timestamp() -> float:
    return datetime.now(UTC).timestamp()

@dataclass
class Schedule:
    kind: str = "once"
    start_date: str = field(default_factory=lambda: date.today().isoformat())
    time: str = "08:00:00"
    weekdays: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    interval: int = 1
    interval_unit: str = "days"

@dataclass
class Banner:
    path: str = ""
    width: int = 640
    height: int = 400
    lock_aspect: bool = True
    fit: str = "fit"
    position: str = "center"
    x: int = 0
    y: int = 0
    screen: str = ""
    always_on_top: bool = True
    fullscreen: bool = False
    background: str = "#151b2b"
    transparent: bool = False
    opacity: float = 1.0
    border: bool = False
    rounded: int = 16
    text: str = ""
    text_position: str = "bottom"
    font_family: str = "Segoe UI"
    font_size: int = 24
    text_color: str = "#ffffff"
    hide_text: bool = False
    hide_buttons: bool = False
    gif_speed: int = 100
    loop_gif: bool = True
    duration: float = 0.0
    auto_action: str = "close"

@dataclass
class Audio:
    tracks: list[str] = field(default_factory=list)
    mode: str = "sequential"
    selected_track: int = 0
    cycles: int | None = 1
    volume: int = 80
    fade_in: float = 0.0
    fade_out: float = 0.0
    track_gap: float = 0.0
    cycle_gap: float = 0.0
    shuffle_each_cycle: bool = True

@dataclass
class ReAlert:
    count: int | None = 0
    interval_seconds: int = 300
    restart_audio: bool = True
    reopen_banner: bool = True
    keep_banner: bool = True

@dataclass
class Corner:
    """Per-alarm corner image settings. Stored inside the alarm's own JSON; Alarm A's never affects Alarm B's."""
    enabled: bool = False  # new alarms: disabled
    images: list = field(default_factory=list)  # ordered files the user imported (never copied or deleted)
    order: str = "sequential"  # or "shuffle"
    loop: bool = True  # False: finite, stop at the last image
    cycle: bool = False
    cycle_seconds: int = 10
    width: int = 300
    height: int = 300
    keep_aspect: bool = True
    mode: str = "fit"
    opacity: int = 100  # percent
    monitor: str = "primary"
    on_top: bool = True
    trim: bool = False

@dataclass
class Notify:
    """Per-alarm compact message notification (text = Alarm.message). Stored inside the alarm's own JSON."""
    enabled: bool = False
    duration: int = 10  # seconds
    font_size: int = 16

@dataclass
class Alarm:
    id: str = field(default_factory=lambda: uuid4().hex)
    name: str = "New alarm"
    message: str = ""
    enabled: bool = True
    schedule: Schedule = field(default_factory=Schedule)
    banner: Banner = field(default_factory=Banner)
    audio: Audio = field(default_factory=Audio)
    realert: ReAlert = field(default_factory=ReAlert)
    corner: Corner = field(default_factory=Corner)
    notify: Notify = field(default_factory=Notify)
    snooze_seconds: int = 300
    revision: int = 1
    effective_at: float = field(default_factory=timestamp)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Alarm:
        values = {f.name: data[f.name] for f in fields(cls) if f.name in data}
        for key, factory in (("schedule", Schedule), ("banner", Banner), ("audio", Audio), ("realert", ReAlert), ("corner", Corner), ("notify", Notify)):
            raw = values.get(key, {})
            if not isinstance(raw, dict):
                raw = {}
            values[key] = factory(**{f.name: raw[f.name] for f in fields(factory) if f.name in raw})
        return cls(**values)

    def clone(self) -> Alarm:
        copy = self.from_dict(self.to_dict())
        copy.id = uuid4().hex
        copy.name += " (copy)"
        copy.enabled = False
        copy.revision = 1
        copy.effective_at = timestamp()
        return copy

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("Give this alarm a name.")
        s = self.schedule
        date.fromisoformat(s.start_date)
        time.fromisoformat(s.time)
        if s.kind not in {"once", "daily", "weekly", "interval"}:
            raise ValueError("Choose a valid schedule type.")
        if s.kind == "weekly" and (not s.weekdays or any(d not in range(7) for d in s.weekdays)):
            raise ValueError("Select at least one weekday.")
        if not 1 <= s.interval <= 999999 or s.interval_unit not in {"seconds", "minutes", "hours", "days", "weeks"}:
            raise ValueError("Enter a positive recurrence interval.")
        a = self.audio
        if a.mode not in {"single", "sequential", "cyclic", "shuffle", "repeat_track", "repeat_count", "infinite"}:
            raise ValueError("Choose a valid playback mode.")
        if a.cycles is not None and not 1 <= a.cycles <= 999:
            raise ValueError("Audio cycle count must be 1–999 or Infinite.")
        if a.tracks and not 0 <= a.selected_track < len(a.tracks):
            raise ValueError("Choose an existing selected track.")
        if not 0 <= a.volume <= 100 or any(v < 0 for v in (a.fade_in, a.fade_out, a.track_gap, a.cycle_gap)):
            raise ValueError("Volume or playback timing is out of range.")
        r = self.realert
        if r.count is not None and not 0 <= r.count <= 999:
            raise ValueError("Additional re-alert count must be 0–999 or Infinite.")
        if r.interval_seconds < 1 or self.snooze_seconds < 1:
            raise ValueError("Re-alert and snooze intervals must be positive.")
        if not 1 <= self.notify.duration <= 3600 or not 8 <= self.notify.font_size <= 72:
            raise ValueError("Notification duration must be 1–3600 seconds and font size 8–72.")
        b = self.banner
        if not 32 <= b.width <= 16384 or not 32 <= b.height <= 16384:
            raise ValueError("Banner dimensions must be between 32 and 16384 pixels.")
        if not 0.05 <= b.opacity <= 1 or b.gif_speed < 1 or b.duration < 0:
            raise ValueError("Banner opacity, speed or duration is invalid.")
        if b.fit not in {"fit", "fill", "stretch"} or b.auto_action not in {"close", "dismiss"}:
            raise ValueError("Invalid banner display options.")

@dataclass
class Occurrence:
    id: str
    alarm_id: str
    key: str
    scheduled_at: float
    snapshot: Alarm
    status: str = "active"
    additional_done: int = 0
    next_alert: float | None = None
    snooze_until: float | None = None
    last_activation: float = 0.0
    sound_stopped: bool = False
    banner_closed: bool = False
    audio_checkpoint: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Occurrence:
        values = dict(data)
        values["snapshot"] = Alarm.from_dict(values["snapshot"])
        return cls(**values)

    def has_more_alerts(self) -> bool:
        count = self.snapshot.realert.count
        return count is None or self.additional_done < count

@dataclass(frozen=True)
class EngineEvent:
    kind: str
    occurrence_id: str
    reason: str = ""
