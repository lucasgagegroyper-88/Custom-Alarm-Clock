"""Pure playlist sequencing; advance exactly once per actual EndOfMedia event."""
from __future__ import annotations
from dataclasses import dataclass
import random
from .models import Audio

@dataclass(frozen=True)
class TrackStep:
    index: int
    cycle: int
    delay: float = 0.0

class PlaylistCursor:
    def __init__(self, config: Audio, rng: random.Random | None = None):
        self.config = config
        self.rng = rng or random.Random()
        self.completed_cycles = 0
        self.position = 0
        self.cycle = 1
        self.finished = not bool(config.tracks)
        self.limit = 1 if config.mode in {"single", "sequential"} else (None if config.mode == "infinite" else config.cycles)
        self.order = self._order()

    def _order(self):
        if not self.config.tracks:
            return []
        if self.config.mode in {"single", "repeat_track"}:
            return [min(self.config.selected_track, len(self.config.tracks) - 1)]
        result = list(range(len(self.config.tracks)))
        if self.config.mode == "shuffle":
            self.rng.shuffle(result)
        return result

    @property
    def current(self) -> TrackStep | None:
        return None if self.finished else TrackStep(self.order[self.position], self.cycle)

    @property
    def is_final_track(self) -> bool:
        return self.limit is not None and self.cycle >= self.limit and self.position == len(self.order) - 1

    def complete_track(self) -> TrackStep | None:
        if self.finished:
            return None
        self.position += 1
        if self.position < len(self.order):
            return TrackStep(self.order[self.position], self.cycle, self.config.track_gap)
        self.completed_cycles += 1
        if self.limit is not None and self.completed_cycles >= self.limit:
            self.finished = True
            return None
        self.cycle += 1
        self.position = 0
        if self.config.mode == "shuffle" and self.config.shuffle_each_cycle:
            self.order = self._order()
        return TrackStep(self.order[0], self.cycle, self.config.cycle_gap)

    def checkpoint(self) -> dict:
        return {"order": list(self.order), "position": self.position, "cycle": self.cycle,
                "completed_cycles": self.completed_cycles, "finished": self.finished}

    def restore(self, checkpoint: dict):
        order = checkpoint["order"]
        expected = [self.config.selected_track] if self.config.mode in {"single", "repeat_track"} else list(range(len(self.config.tracks)))
        if sorted(order) != sorted(expected):
            raise ValueError("Saved playback state does not match this playlist. Dismiss and trigger the alarm again.")
        self.order = list(order)
        self.position = int(checkpoint["position"])
        self.cycle = int(checkpoint["cycle"])
        self.completed_cycles = int(checkpoint["completed_cycles"])
        self.finished = bool(checkpoint["finished"])
        if not self.finished and (not 0 <= self.position < len(self.order) or self.cycle != self.completed_cycles + 1):
            raise ValueError("Saved playback progress is invalid. Dismiss and trigger the alarm again.")
