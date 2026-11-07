"""Event-driven image carousel for the corner overlay. No Qt: pure state + SQLite preferences.

One call to ``select()`` == one genuine alarm activation (or one optional timed-cycle tick). The carousel never
looks at a clock and never opens anything; it only answers "which of the user's imported files is next?".

Persisted preferences (all via the existing Repository.get/set; with scope=<alarm id> each key gets a ':<alarm id>' suffix):
    corner_next  int        index in the current playback sequence of the image to show next
    corner_seq   list[str]  the current shuffled cycle (shuffle mode only)
    corner_last  str        the image shown most recently (used to avoid an immediate repeat after a reshuffle)

Only paths the user imported are ever returned; nothing is generated, bundled or substituted.
"""
from __future__ import annotations
import random

NEXT, SEQ, LAST = "corner_next", "corner_seq", "corner_last"


def sync_alarm(repo, alarm_id: str, old, new):
    """Keep one alarm's carousel position stable after that alarm's Corner settings were edited (old/new: models.Corner)."""
    if old is None:
        return
    carousel = Carousel(repo, scope=alarm_id)
    if new.images != old.images:
        carousel.images_changed(old.images, new.images, old.order)
    if new.order != old.order:
        carousel.order_changed(new.images, new.order)


class Carousel:
    def __init__(self, repo, rng: random.Random | None = None, scope: str = ""):
        """`scope` is the stable alarm id: every alarm gets its own persisted position, shuffle cycle and last image."""
        self.repo = repo
        self.rng = rng or random.Random()
        suffix = f":{scope}" if scope else ""
        self.k_next, self.k_seq, self.k_last = NEXT + suffix, SEQ + suffix, LAST + suffix

    def forget(self):
        """Drop this carousel's saved state (alarm deleted). Imported media files are never touched."""
        for key in (self.k_next, self.k_seq, self.k_last):
            self.repo.unset(key)

    # ---- persisted state -------------------------------------------------------------------------
    @property
    def next_index(self) -> int:
        try:
            return max(0, int(self.repo.get(self.k_next, 0)))
        except (TypeError, ValueError):
            return 0

    @property
    def last(self) -> str:
        return str(self.repo.get(self.k_last, "") or "")

    def _stored_seq(self) -> list[str]:
        seq = self.repo.get(self.k_seq, [])
        return [str(p) for p in seq] if isinstance(seq, list) else []

    def _save(self, nxt: int, seq: list[str] | None = None, last: str | None = None):
        with self.repo.transaction():
            self.repo.set(self.k_next, int(nxt))
            if seq is not None:
                self.repo.set(self.k_seq, list(seq))
            if last is not None:
                self.repo.set(self.k_last, last)

    # ---- sequence helpers ------------------------------------------------------------------------
    def _new_cycle(self, images: list[str], avoid_first: str = "") -> list[str]:
        seq = list(images)
        self.rng.shuffle(seq)
        if len(seq) > 1 and seq[0] == avoid_first:
            swap = self.rng.randrange(1, len(seq))
            seq[0], seq[swap] = seq[swap], seq[0]
        return seq

    @staticmethod
    def _same_set(a: list[str], b: list[str]) -> bool:
        return len(a) == len(b) and set(a) == set(b)

    def sequence(self, images: list[str], order: str) -> list[str]:
        """Current playback sequence without modifying state (shuffle falls back to list order if no valid cycle)."""
        if order == "shuffle":
            seq = self._stored_seq()
            if self._same_set(seq, images):
                return seq
        return list(images)

    # ---- selection -------------------------------------------------------------------------------
    def select(self, images: list[str], order: str = "sequential", loop: bool = True) -> str | None:
        """Pick the image for ONE activation and persist the new position. None when there are no images."""
        n = len(images)
        if n == 0:
            return None
        nxt = self.next_index
        if order == "shuffle":
            seq = self._stored_seq()
            if not self._same_set(seq, images):
                seq, nxt = self._new_cycle(images, self.last), 0
        else:
            seq = list(images)
        if nxt >= n:
            if loop:
                nxt = 0
                if order == "shuffle":
                    seq = self._new_cycle(images, self.last)
            else:
                nxt = n - 1  # finite: stay on the final image, never wrap
        chosen = seq[nxt]
        self._save(nxt + 1, seq if order == "shuffle" else [], chosen)
        return chosen

    # ---- keeping the position stable when the user edits the collection ----------------------------
    def images_changed(self, old: list[str], new: list[str], order: str):
        """Called after the user adds/removes/reorders images. The position follows the image that was next."""
        if old == new:
            return
        seq_old = self.sequence(old, order)
        nxt = self.next_index
        survivors = [p for p in seq_old if p in new]
        if order == "shuffle":
            new_seq = survivors + [p for p in new if p not in seq_old]
        else:
            new_seq = list(new)
        if nxt < len(seq_old) and seq_old[nxt] in new_seq:
            position = new_seq.index(seq_old[nxt])
        else:
            # The "next" image was removed (or the cycle had finished): continue after the images that survive.
            position = sum(1 for p in seq_old[:nxt] if p in new)
        position = max(0, min(position, len(new_seq)))
        self._save(position, new_seq if order == "shuffle" else [])
        if not new:
            self._save(0, [], "")

    def order_changed(self, images: list[str], order: str):
        """Switching sequential <-> shuffle: continue after the last shown image (sequential) or start a fresh cycle."""
        if order == "shuffle":
            self._save(0, self._new_cycle(images, self.last))
        else:
            last = self.last
            self._save(images.index(last) + 1 if last in images else 0, [])
