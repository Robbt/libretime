"""
Schedule playlist content into a show instance.

Port of the insert path behind ``Application_Model_Scheduler::scheduleAfter()``
(``legacy/application/models/Scheduler.php``) as the autoplaylist builder uses
it: take the items of a playlist and append them to a show instance, overlapping
them by the crossfade duration, then refresh the instance statistics.

Only the parts the builder exercises are here. Items that reference a smart
block are refused rather than half implemented, because a block has to be
evaluated first - that arrives together with the fill semantics.

The two rules that decide where every item lands:

*   The first item of an empty instance starts when the instance starts. Every
    item after it starts ``crossfade`` before the previous one ended, and ends
    ``clip_length`` after it started. When content is appended after items that
    are already scheduled, the very first appended item also starts one crossfade
    before the last existing item ends - which is the only difference between
    filling an empty show and adding to one that has content.
*   If the instance has already started by the time content is added, the time
    between its start and now is filled with a *filler* row - an item that is
    never played (``playout_status = -1``) and exists only to keep the schedule
    continuous - and the new content starts at the current instant. The legacy
    service then does not overlap that first item with anything.

Times are stored with microsecond precision, the way the legacy insert writes
them with ``DEFAULT_MICROTIME_FORMAT``. This differs from the linked show copy
in :mod:`.populate`, which truncates to whole seconds: the two paths really do
behave differently, and playout has learned to live with both.

One deliberate difference. The legacy playlist content arrives out of
``Application_Model_Playlist::getContents()``, which formats lengths for the
interface through ``LengthFormatter`` - and that formatter rounds the seconds
field to one decimal. So the legacy builder schedules a 4:58.75 clip as 4:58.8
and a 1.25 cue as 1.2, losing up to fifty milliseconds per item on its way to
the schedule. This module keeps the precision of the stored row: the rounding is
a display artefact of the legacy UI leaking into a write path, not something
playout depends on. ``tools/playlist-scheduler-equivalence/README.md`` records
the measured difference and keeps its fixtures on values both sides agree on.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from ..models import (
    Playlist,
    PlaylistContent,
    Schedule,
    ShowInstance,
)
from .populate import (
    PreferenceStore,
    to_instant,
    to_microseconds,
    update_schedule_status,
)

logger = logging.getLogger(__name__)

# cc_playlistcontents.type: a file, a stream, or a smart block.
ItemType = PlaylistContent.Kind


class UnsupportedContent(Exception):
    """The playlist holds content the port would only guess at."""


# Half a second, the fade retrieveMediaFiles() gives every stream.
DEFAULT_STREAM_FADE = time(0, 0, 0, 500000)


@dataclass(frozen=True)
class MediaItem:
    """
    One entry of a playlist, in the shape the scheduler needs.

    Attributes:
        kind: File, stream or block.
        item_id: ``cc_files.id`` or ``cc_webstreams.id``.
        length: Clip length as stored on the playlist row. The scheduler adds
            this to the start of the item and never looks at the file again.
        cue_in: Start offset within the file.
        cue_out: Stop offset within the file.
        fade_in: Seconds, either as a bare number as the table stores them or
            already as a time.
        fade_out: Same.
    """

    kind: ItemType
    item_id: int | None
    length: timedelta
    cue_in: timedelta
    cue_out: timedelta
    fade_in: time | None
    fade_out: time | None


def _as_time(value: object) -> time | None:
    if value is None or value == "":
        return None

    if isinstance(value, time):
        return value

    raise UnsupportedContent(f"unexpected fade value {value!r}")


def _as_delta(value: object) -> timedelta:
    if value is None:
        return timedelta(0)

    if isinstance(value, timedelta):
        return value

    return timedelta(microseconds=to_microseconds(str(value)))


def playlist_items(playlist: Playlist) -> list[MediaItem]:
    """
    The content of a playlist, in the order it plays.

    ``Application_Model_Playlist::getContents()`` unions files, streams and
    blocks and orders by position; the same order decides the schedule, so ties
    on position fall back to the row id to stay deterministic.
    """
    rows = PlaylistContent.objects.filter(playlist=playlist).order_by("position", "pk")

    items: list[MediaItem] = []

    for row in rows:
        try:
            kind = ItemType(row.kind)
        except ValueError as error:
            raise UnsupportedContent(
                f"playlist content {row.pk} has unknown type {row.kind!r}"
            ) from error

        if kind is ItemType.BLOCK:
            # A block has to be evaluated into files first, which is the next
            # step of this port rather than a detail of this one.
            raise UnsupportedContent(
                f"playlist {playlist.pk} contains the smart block {row.block_id}; "
                "blocks are not scheduled by this version of the port"
            )
        item_id = row.file_id if kind is ItemType.FILE else row.stream_id

        if item_id is None:
            raise UnsupportedContent(f"playlist content {row.pk} references nothing")

        # A stream is faded by the scheduler itself rather than by the row:
        # retrieveMediaFiles() hardcodes half a second in and out for streams,
        # with the row's own fades left in a comment behind it.
        fade_in = DEFAULT_STREAM_FADE if kind is ItemType.STREAM else _fade(row.fade_in)
        fade_out = (
            DEFAULT_STREAM_FADE if kind is ItemType.STREAM else _fade(row.fade_out)
        )

        items.append(
            MediaItem(
                kind=kind,
                item_id=item_id,
                length=_as_delta(row.length),
                cue_in=_as_delta(row.cue_in),
                cue_out=_as_delta(row.cue_out),
                fade_in=fade_in,
                fade_out=fade_out,
            )
        )

    return items


def _fade(value: object) -> time | None:
    """
    A fade as the table holds it.

    The legacy rows carry either ``00:00:01`` or a bare number of seconds such
    as ``0.5``, and ``insertAfter()`` converts the numeric ones; a null means
    the default fade of the installation.
    """
    if value is None or value == "":
        return None

    if isinstance(value, time):
        return value

    if isinstance(value, (int, float, Decimal)):
        return seconds_to_time(value)

    text = str(value)
    if ":" in text:
        hours, minutes, seconds = text.split(":")
        return time(int(hours), int(minutes), int(float(seconds)))

    return seconds_to_time(text)


def seconds_to_time(value: object) -> time:
    """
    Seconds, possibly fractional, as a ``time`` - the column type is TIME.

    The legacy ``secondsToPlaylistTime()`` answers ``SS.uuuuuu``, which the
    ``fade_in`` and ``fade_out`` columns then read back as seconds past midnight.
    """
    microseconds = to_microseconds(
        value if isinstance(value, timedelta) else str(value)
    )
    seconds, rest = divmod(microseconds, 1_000_000)

    return time(0, seconds // 60, seconds % 60, rest)


@dataclass
class ScheduledCount:
    """What an append did, for logs and for the caller's notifications."""

    items: int = 0
    filler: bool = False
    instance_id: int = 0

    def __bool__(self) -> bool:
        return self.items > 0


def _shift(moment: datetime, microseconds: int) -> datetime:
    return moment + timedelta(microseconds=microseconds)


def _last_item(instance: ShowInstance) -> Schedule | None:
    """
    The item the next content is appended after.

    ``getLastAudioItemId()`` asks for the latest ``ends`` of the instance,
    fillers included, and the scheduler then starts from its end.
    """
    return Schedule.objects.filter(instance=instance).order_by("-ends_at").first()


def append_items(
    instance: ShowInstance,
    items: Sequence[MediaItem],
    *,
    now: datetime | None = None,
    crossfade: timedelta | None = None,
) -> ScheduledCount:
    """
    Append media items to a show instance, overlapping them by the crossfade.

    Args:
        instance: Show instance to fill.
        items: Content in play order, from :func:`playlist_items`.
        now: Current instant, ``timezone.now()`` when not given. It decides
            whether the elapsed part of a running instance has to be filled.
        crossfade: Overlap between items. Defaults to the preference.

    Returns:
        How many schedule rows were written.
    """
    if not items:
        return ScheduledCount(instance_id=instance.pk)

    moment = now or timezone.now()
    overlap = crossfade or timedelta(microseconds=PreferenceStore.default_crossfade())

    starts = to_instant(instance.starts_at)
    last = _last_item(instance)

    if last is None:
        position = 0
        next_start = starts
        # An empty show has nothing to fade into.
        apply_crossfade = False
    else:
        position = last.position + 1
        next_start = to_instant(last.ends_at)
        apply_crossfade = True

    filler = False

    if next_start < moment:
        filler = _insert_filler(instance, next_start, moment)
        next_start = moment
        apply_crossfade = False

    rows: list[Schedule] = []

    for item in items:
        if apply_crossfade:
            next_start = _shift(next_start, -to_microseconds(overlap))

        ends = _shift(next_start, to_microseconds(item.length))

        rows.append(
            Schedule(
                starts_at=next_start,
                ends_at=ends,
                length=item.length,
                fade_in=item.fade_in,
                fade_out=item.fade_out,
                cue_in=item.cue_in,
                cue_out=item.cue_out,
                file_id=item.item_id if item.kind is ItemType.FILE else None,
                stream_id=item.item_id if item.kind is ItemType.STREAM else None,
                instance_id=instance.pk,
                position=position,
                # The legacy insert lists neither of these, so both take their
                # column defaults: inside the instance, and never played.
                position_status=Schedule.PositionStatus.INSIDE,
                broadcasted=0,
                played=False,
            )
        )

        # Every following item starts one crossfade before this one ends.
        next_start = ends
        apply_crossfade = True
        position += 1

    Schedule.objects.bulk_create(rows)
    mark_scheduled(rows)
    update_schedule_status(instance, now=moment)

    logger.info(
        "scheduled %d items into show instance %d%s",
        len(rows),
        instance.pk,
        " after a filler" if filler else "",
    )

    return ScheduledCount(items=len(rows), filler=filler, instance_id=instance.pk)


def _insert_filler(instance: ShowInstance, starts: datetime, ends: datetime) -> bool:
    """
    Fill the part of the instance that has already passed.

    A filler keeps the schedule continuous and is never played; the legacy code
    writes one from its own record of when the instance was last scheduled, so
    the same instant is used here.
    """
    Schedule.objects.create(
        starts_at=starts,
        ends_at=ends,
        length=ends - starts,
        cue_in=timedelta(0),
        cue_out=timedelta(0),
        instance_id=instance.pk,
        position=0,
        position_status=Schedule.PositionStatus.FILLER,
        broadcasted=0,
        played=False,
    )

    return True


def mark_scheduled(rows: Sequence[Schedule]) -> None:
    """
    Flag the files that are now on a schedule.

    The library uses ``is_scheduled`` to warn before a file is deleted, and the
    legacy code sets it only for files that were not flagged yet.
    """
    from ...storage.models import File  # local: avoids a module cycle

    file_ids = [row.file_id for row in rows if row.file_id is not None]

    if not file_ids:
        return

    File.objects.filter(id__in=file_ids, scheduled=False).update(scheduled=True)


def append_playlist(
    instance: ShowInstance,
    playlist: Playlist,
    *,
    now: datetime | None = None,
    notify: Callable[[], None] | None = None,
) -> ScheduledCount:
    """
    Schedule a whole playlist at the end of a show instance.

    ``notify`` is called once at the end, because a schedule change only means
    anything to playout when it is announced. It is a parameter rather than a
    direct call so that a caller can build several instances and announce them
    once - which is what the autoplaylist poll does - and so that this module
    stays free of transport.
    """
    result = append_items(instance, playlist_items(playlist), now=now)

    if result and notify is not None:
        notify()

    return result
