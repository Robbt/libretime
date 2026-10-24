"""
How a smart block decides how much of its shelf to contribute.

The deterministic shapes are compared exactly. The random one cannot be - the
legacy solver shuffles, so its answer differs per rebuild - and is tested by what
must hold for any answer: the criteria fit the window, nothing is invented, and a
seeded generator gives the same answer twice.
"""

import random
from dataclasses import replace
from datetime import timedelta

import pytest

from ...services.criteria import BlockLimit
from ...services.fill import MAX_TRACKS, Track, fill, solve

# A day of window is what an item limit still carries with it, and "newest"
# keeps these cases on the ordered path rather than the random one.
BY_ITEMS = BlockLimit(time_seconds=24 * 60 * 60, items=3, sort="newest")
BY_MINUTES = BlockLimit(time_seconds=180, sort="newest")


def tracks(*lengths: float) -> list[Track]:
    return [Track(id=index + 1, length=length) for index, length in enumerate(lengths)]


def test_items_limit_takes_them_in_order() -> None:
    chosen = fill(tracks(60, 60, 60, 60, 60), BY_ITEMS)

    assert [track.id for track in chosen] == [1, 2, 3]


def test_a_track_too_long_for_the_window_is_skipped_not_stopped_at() -> None:
    """
    The legacy loop keeps going after a track that does not fit.

    A three minute window with 100, 200, 30 and 40 second tracks takes 100,
    passes on 200 and then takes 30 and 40 - it does not stop at the first one
    that is too long, which is what a "skip the big ones" shelf relies on.
    """
    chosen = fill(tracks(100, 200, 30, 40), BY_MINUTES)

    assert [track.id for track in chosen] == [1, 3, 4]
    assert sum(track.length for track in chosen) == 170


def test_overflow_takes_the_track_that_runs_past_the_end() -> None:
    limit = replace(BY_MINUTES, overflow_tracks=True)

    chosen = fill(tracks(100, 200, 30), limit)

    # Overflow adds the track that runs past the end and then stops.
    assert [track.id for track in chosen] == [1, 2]
    assert sum(track.length for track in chosen) > BY_MINUTES.time_seconds


def test_repeat_draws_from_what_was_already_chosen() -> None:
    limit = replace(BY_MINUTES, repeat_tracks=True)

    chosen = fill(tracks(60.0, 90.0), limit)

    total = sum(track.length for track in chosen)

    assert total <= 180
    assert total >= 150  # 60 + 90, and then repeats of what is already there
    assert {track.id for track in chosen} <= {1, 2}


def test_a_repeat_that_cannot_fill_the_window_stops_instead_of_spinning() -> None:
    """
    The legacy loop would never finish here.

    One minute of material into a three minute window: it fills to 180 exactly,
    then the leftover is zero and no further draw is possible. A shorter window
    and a longer track make the leftover smaller than anything available, and the
    original keeps drawing in that case.
    """
    limit = BlockLimit(time_seconds=90, repeat_tracks=True)

    chosen = fill(tracks(60), limit)

    assert [track.id for track in chosen] == [1]


def test_only_the_first_five_hundred_candidates_are_considered() -> None:
    many = tracks(*([1.0] * (MAX_TRACKS + 50)))
    limit = BlockLimit(time_seconds=MAX_TRACKS * 2, items=MAX_TRACKS + 10)

    chosen = fill(many, limit)

    assert len(chosen) == MAX_TRACKS


def test_no_candidates_no_tracks() -> None:
    assert fill([], BY_MINUTES) == []


# ---------------------------------------------------------------------------
# the random packing
# ---------------------------------------------------------------------------


def test_the_solver_never_runs_past_the_window() -> None:
    candidates = tracks(*[float(length) for length in range(10, 60, 7)])

    solution = solve(candidates, 200, rng=random.Random(7))

    assert sum(track.length for track in solution) <= 200
    assert len(solution) > 0


def test_a_seeded_generator_packs_the_same_way_twice() -> None:
    candidates = tracks(30.0, 45.0, 20.0, 60.0, 15.0, 90.0)
    limit = BlockLimit(time_seconds=300, sort="random")

    first = fill(candidates, limit, rng=random.Random(11))
    second = fill(candidates, limit, rng=random.Random(11))

    assert [track.id for track in first] == [track.id for track in second]


def test_an_unseeded_random_block_stays_within_its_window_and_its_shelf() -> None:
    candidates = tracks(*[float(length) for length in range(5, 80, 5)])
    limit = BlockLimit(time_seconds=240, sort="random")

    for seed in range(5):
        chosen = fill(candidates, limit, rng=random.Random(seed))

        assert sum(track.length for track in chosen) <= 240
        assert {track.id for track in chosen} <= {track.id for track in candidates}


def test_repeat_random_keeps_packing_until_nothing_fits() -> None:
    limit = BlockLimit(time_seconds=200, sort="random", repeat_tracks=True)

    chosen = fill(tracks(30.0, 50.0), limit, rng=random.Random(3))

    total = sum(track.length for track in chosen)

    assert total <= 200
    # Two tracks of 30 and 50 cannot land exactly on 200 in every trial, but the
    # solver plus the repeat must get within one track length of it.
    assert total >= 200 - 50
