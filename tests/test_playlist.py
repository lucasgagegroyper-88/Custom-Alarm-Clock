import random
import pytest
from alarmclock.models import Audio
from alarmclock.playlist import PlaylistCursor

# Symbolic paths only. No media is created, bundled, downloaded or decoded.
TRACKS = ["A.mp3", "B.mp3", "C.mp3"]

def sequence(cursor, maximum=10000):
    result = []
    step = cursor.current
    while step and len(result) < maximum:
        result.append(step.index)
        step = cursor.complete_track()
    return result


@pytest.mark.parametrize("count", [1, 2, 3, 999])
@pytest.mark.parametrize("mode", ["cyclic", "repeat_count"])
def test_exact_total_cycles(mode, count):
    cursor = PlaylistCursor(Audio(tracks=TRACKS, mode=mode, cycles=count))
    assert sequence(cursor) == [0, 1, 2] * count
    assert cursor.completed_cycles == count and cursor.finished
    assert cursor.complete_track() is None


def test_sequential_always_once():
    assert sequence(PlaylistCursor(Audio(tracks=TRACKS, mode="sequential", cycles=999))) == [0, 1, 2]


def test_single_selected_track():
    assert sequence(PlaylistCursor(Audio(tracks=TRACKS, mode="single", selected_track=2, cycles=999))) == [2]


def test_repeat_selected_track():
    assert sequence(PlaylistCursor(Audio(tracks=TRACKS, mode="repeat_track", selected_track=1, cycles=4))) == [1]*4


@pytest.mark.parametrize("mode,cycles", [("infinite", 1), ("cyclic", None), ("repeat_track", None)])
def test_infinite_playback_state(mode, cycles):
    cursor = PlaylistCursor(Audio(tracks=TRACKS, mode=mode, cycles=cycles))
    assert len(sequence(cursor, 1000)) == 1000
    assert not cursor.finished


def test_shuffle_is_permutation_each_cycle():
    cursor = PlaylistCursor(Audio(tracks=TRACKS, mode="shuffle", cycles=20), random.Random(51))
    values = sequence(cursor)
    cycles = [values[i:i+3] for i in range(0, len(values), 3)]
    assert all(sorted(cycle) == [0, 1, 2] for cycle in cycles)
    assert len(set(tuple(cycle) for cycle in cycles)) > 1


def test_shuffle_can_keep_order_between_cycles():
    cursor = PlaylistCursor(Audio(tracks=TRACKS, mode="shuffle", cycles=5, shuffle_each_cycle=False), random.Random(3))
    values = sequence(cursor)
    assert values == values[:3]*5


def test_separate_gaps():
    cursor = PlaylistCursor(Audio(tracks=TRACKS, mode="cyclic", cycles=2, track_gap=2, cycle_gap=7))
    assert cursor.current.delay == 0
    assert cursor.complete_track().delay == 2
    assert cursor.complete_track().delay == 2
    assert cursor.complete_track().delay == 7


def test_empty_playlist_is_silent():
    cursor = PlaylistCursor(Audio())
    assert cursor.finished and cursor.current is None


def test_final_track_determined_exactly():
    cursor = PlaylistCursor(Audio(tracks=TRACKS, mode="cyclic", cycles=2))
    for i in range(6):
        assert cursor.is_final_track == (i == 5)
        cursor.complete_track()


def test_checkpoint_preserves_completed_cycles_and_order():
    config = Audio(tracks=TRACKS, mode="shuffle", cycles=3)
    original = PlaylistCursor(config, random.Random(50))
    for _ in range(4):
        original.complete_track()
    checkpoint = original.checkpoint()
    restored = PlaylistCursor(config)
    restored.restore(checkpoint)
    assert restored.current == original.current
    assert restored.order == original.order
    assert len(sequence(restored)) == 5
    assert restored.completed_cycles == 3


def test_bad_checkpoint_cannot_substitute_tracks():
    cursor = PlaylistCursor(Audio(tracks=TRACKS))
    checkpoint = cursor.checkpoint()
    checkpoint["order"] = [2]
    with pytest.raises(ValueError, match="does not match"):
        cursor.restore(checkpoint)
