"""
Choose the files a dynamic smart block contributes.

Port of ``Application_Model_Block::getListOfFilesUnderLimit()`` and of the
subset-sum solver next to it in ``legacy/application/models/Block.php``. The
criteria that decide *which* files are candidates live in
:mod:`.criteria`; this module decides *how many of them, in what order*, so that
the block fills the window it was given.

Three shapes, exactly as the original has them:

*   **Limited by items** - take them in order until the count is reached.
*   **Limited by time** - take them in order while they fit, stop on the first
    one that would run past the window, unless "allow overflow" says to take it
    anyway. With "repeat tracks" the selection is then topped up by drawing from
    the items already chosen, which is what fills a long show from a small
    shelf.
*   **Sorted randomly, no overflow, no item limit** - the window is packed rather
    than filled in order, by the solver below: fifty trials of a randomised
    greedy pass, each followed by a replacement pass that trades chosen tracks
    for longer ones that still fit.

Where the original can spin forever, this does not. Its repeat loop keeps drawing
as long as the window is not full, and if no remaining track fits the leftover
time the loop never ends - a request that hangs rather than a show that comes out
a few seconds short. Here a full pass over the candidates that adds nothing stops
the top-up, and the shortfall is logged.

The randomness itself is left as it was - unbiased, different on every rebuild -
and is injected through ``rng`` so that a seeded generator can be turned on in
one place later, which is what makes a rebuild reproducible.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass

from .criteria import BlockLimit

logger = logging.getLogger(__name__)

# The legacy solver stops chasing a better packing once the leftover is under
# this many seconds, and stops considering tracks that small apart from each
# other. It is a display tolerance, not a musical one.
CLOSE_ENOUGH = 0.25

# The original considers at most this many candidates, and packs in this many
# randomised trials.
MAX_TRACKS = 500
SOLVER_TRIALS = 50


@dataclass(frozen=True)
class Track:
    """A candidate file and the length that gets scheduled from it."""

    id: int
    length: float

    def __float__(self) -> float:
        return self.length


def _sum(tracks: Sequence[Track]) -> float:
    return sum(track.length for track in tracks)


def _is_close_enough(delta: float) -> bool:
    return delta < CLOSE_ENOUGH


def _max_by_length(tracks: Sequence[Track]) -> Track | None:
    """``maxByOrNull()``: the longest track, or None when there is none."""
    best: Track | None = None

    for track in tracks:
        if best is None or track.length > best.length:
            best = track

    return best


def solve(
    tracks: Sequence[Track],
    target: float,
    *,
    trials: int = SOLVER_TRIALS,
    rng: random.Random | None = None,
) -> list[Track]:
    """
    Pack as close to ``target`` seconds as an easy heuristic gets.

    Not optimal, and not meant to be: the legacy solver is a randomised greedy
    with one improvement pass per trial, and keeps the best result it saw.

    Args:
        tracks: Candidates.
        target: Seconds to fill.
        trials: Randomised attempts.
        rng: Generator to shuffle with. Pass a seeded ``random.Random`` for a
            reproducible packing.
    """
    generator: random.Random = rng or random.Random()

    candidates = list(tracks)
    best: list[Track] = []

    for _ in range(trials):
        generator.shuffle(candidates)

        initial: list[Track] = []
        for track in candidates:
            if _sum(initial) + track.length <= target:
                initial.append(track)

        # Everything fits: nothing to improve on.
        if len(initial) == len(candidates):
            return initial

        accepted = list(initial)
        generator.shuffle(accepted)

        improved = list(initial)
        for track in accepted:
            delta = target - _sum(improved)
            if _is_close_enough(delta):
                continue

            chosen = {item.id for item in improved}
            replacements = [
                item
                for item in candidates
                if item.id not in chosen
                and item.length > track.length
                and item.length - track.length <= delta
            ]

            replacement = _max_by_length(replacements)
            if replacement is None:
                continue

            improved = [item for item in improved if item.id != track.id]
            improved.append(replacement)

        if _sum(improved) > _sum(best):
            best = improved

        if _sum(best) == 0.0:
            return best

    return best


def _is_full(chosen: Sequence[Track], total: float, limit: BlockLimit) -> bool:
    if limit.items is not None and len(chosen) >= limit.items:
        return True

    return total > limit.time_seconds


def _add(
    track: Track,
    chosen: list[Track],
    total: float,
    limit: BlockLimit,
) -> float:
    """Add one track when the rules allow it, returning the new total."""
    if limit.overflow_tracks:
        chosen.append(track)

        return total + track.length

    if total + track.length <= limit.time_seconds:
        chosen.append(track)

        return total + track.length

    return total


def fill(
    candidates: Sequence[Track],
    limit: BlockLimit,
    *,
    rng: random.Random | None = None,
) -> list[Track]:
    """
    Turn candidates into the tracks a block actually contributes.

    Args:
        candidates: Files matching the block, in the order the block sorts them.
        limit: Window, item limit and the repeat and overflow switches.
        rng: Generator used by the random packing and by the repeat top-up.

    Returns:
        The chosen tracks, in play order.
    """
    if not candidates:
        return []

    tracks = list(candidates[:MAX_TRACKS])

    if limit.sort == "random" and not limit.overflow_tracks and limit.items is None:
        return _fill_random(tracks, limit, rng=rng)

    return _fill_in_order(tracks, limit, rng=rng)


def _fill_random(
    tracks: list[Track], limit: BlockLimit, *, rng: random.Random | None
) -> list[Track]:
    generator: random.Random = rng or random.Random()

    shortest = min(track.length for track in tracks)

    chosen: list[Track] = []
    total = 0.0

    while True:
        solution = solve(tracks, limit.time_seconds - total, rng=generator)
        chosen.extend(solution)
        total += _sum(solution)

        if not limit.repeat_tracks or limit.time_seconds - total <= shortest:
            break

    generator.shuffle(chosen)

    return chosen


def _fill_in_order(
    tracks: list[Track], limit: BlockLimit, *, rng: random.Random | None
) -> list[Track]:
    generator: random.Random = rng or random.Random()

    chosen: list[Track] = []
    total = 0.0

    for track in tracks:
        total = _add(track, chosen, total, limit)

        if _is_full(chosen, total, limit):
            return chosen

    if not limit.repeat_tracks or not chosen:
        return chosen

    # Top up from the tracks already chosen, as the original does, but stop when
    # a whole pass over the candidates adds nothing: nothing left fits, and the
    # legacy loop would keep drawing forever.
    pool = len(chosen)
    idle_draws = 0

    while not _is_full(chosen, total, limit) and idle_draws < pool:
        candidate = chosen[generator.randrange(pool)]

        before = len(chosen)
        total = _add(candidate, chosen, total, limit)
        idle_draws = 0 if len(chosen) > before else idle_draws + 1

    if not _is_full(chosen, total, limit) and total < limit.time_seconds:
        logger.warning(
            "smart block left %.2f seconds of its window unfilled; no remaining "
            "track fits what is left",
            limit.time_seconds - total,
        )

    return chosen
