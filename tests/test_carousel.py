"""Pure-Python tests of the event-driven carousel (no Qt needed): run with `python -m pytest tests/test_carousel.py`."""
import random
from alarmclock.carousel import Carousel
from alarmclock.storage import Repository

A, B, C, D = "/imports/a.png", "/imports/b.gif", "/imports/c.jpg", "/imports/d.webp"


def make(seed=1):
    repo = Repository(":memory:")
    return repo, Carousel(repo, random.Random(seed))


def draw(carousel, images, count, order="sequential", loop=True):
    return [carousel.select(images, order, loop) for _ in range(count)]


def test_sequential_one_image_per_activation_and_wraps():
    repo, c = make()
    assert draw(c, [A, B, C], 5) == [A, B, C, A, B]


def test_empty_collection_returns_nothing_and_changes_nothing():
    repo, c = make()
    assert c.select([], "sequential", True) is None
    assert repo.get("corner_next") is None


def test_finite_mode_stops_at_last_image_without_wrapping():
    repo, c = make()
    assert draw(c, [A, B, C], 6, loop=False) == [A, B, C, C, C, C]


def test_position_survives_restart():
    repo, c = make()
    draw(c, [A, B, C], 2)
    restarted = Carousel(repo)  # new object, same SQLite preferences
    assert restarted.select([A, B, C], "sequential", True) == C


def test_shuffle_uses_every_image_once_per_cycle_and_never_repeats_across_boundary():
    repo, c = make(7)
    images = [A, B, C, D]
    picks = draw(c, images, 12, order="shuffle")
    for cycle in (picks[0:4], picks[4:8], picks[8:12]):
        assert sorted(cycle) == sorted(images)
    assert all(picks[i] != picks[i + 1] for i in range(len(picks) - 1))


def test_shuffle_cycle_is_persisted_across_restart():
    repo, c = make(3)
    images = [A, B, C, D]
    first = draw(c, images, 2, order="shuffle")
    second = draw(Carousel(repo, random.Random(99)), images, 2, order="shuffle")
    assert sorted(first + second) == sorted(images)  # same cycle continued, no duplicate in it


def test_shuffle_finite_stops_after_one_cycle():
    repo, c = make(5)
    picks = draw(c, [A, B, C], 6, order="shuffle", loop=False)
    assert sorted(picks[:3]) == [A, B, C] and picks[3:] == [picks[2]] * 3


def test_remove_the_next_image_continues_with_its_successor():
    repo, c = make()
    draw(c, [A, B, C], 1)  # next is B
    c.images_changed([A, B, C], [A, C], "sequential")
    assert c.select([A, C], "sequential", True) == C


def test_removing_an_already_shown_image_does_not_skip_or_repeat():
    repo, c = make()
    draw(c, [A, B, C], 2)  # next is C
    c.images_changed([A, B, C], [B, C], "sequential")
    assert c.select([B, C], "sequential", True) == C


def test_added_image_is_reached_after_the_current_end_and_reorder_keeps_the_next_image():
    repo, c = make()
    draw(c, [A, B], 2, loop=False)  # finished
    c.images_changed([A, B], [A, B, D], "sequential")
    assert c.select([A, B, D], "sequential", False) == D
    repo2, c2 = make()
    draw(c2, [A, B, C], 1)  # next is B
    c2.images_changed([A, B, C], [C, B, A], "sequential")
    assert c2.select([C, B, A], "sequential", True) == B


def test_shuffle_edit_keeps_unshown_images_in_the_cycle():
    repo, c = make(11)
    shown = draw(c, [A, B, C], 1, order="shuffle")[0]
    remaining = [x for x in (A, B, C) if x != shown]
    c.images_changed([A, B, C], [A, B, C, D], "shuffle")
    rest = draw(c, [A, B, C, D], 3, order="shuffle")
    assert sorted(rest) == sorted(remaining + [D])


def test_switching_order_does_not_advance_by_itself():
    repo, c = make()
    draw(c, [A, B, C], 2)  # last shown B
    c.order_changed([A, B, C], "shuffle")
    c.order_changed([A, B, C], "sequential")
    assert c.select([A, B, C], "sequential", True) == C
