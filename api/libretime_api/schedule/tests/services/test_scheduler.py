"""
Scheduling playlist content into a show instance.

The expected times come from the legacy ``insertAfter()`` loop: the first item of
an empty instance starts when the instance starts, every later item starts one
crossfade before the previous one ends, and each item runs ``clip_length`` from
its own start. ``tools/playlist-scheduler-equivalence/`` checks the same numbers
against the legacy service.
"""

from datetime import datetime, time, timedelta, timezone

import pytest
from model_bakery import baker

from ...models import (
    Playlist,
    PlaylistContent,
    Schedule,
    Show,
    ShowInstance,
    Webstream,
)
from ...services.populate import PreferenceStore, to_instant
from ...services.scheduler import (
    UnsupportedContent,
    append_items,
    append_playlist,
    playlist_items,
    seconds_to_time,
)

UTC = timezone.utc

START = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
CROSSFADE = timedelta(milliseconds=1500)


@pytest.fixture(autouse=True)
def _database(db):
    # The fixtures below spell out the overlap they expect, so the preference
    # they were computed against is set rather than left to the default of zero.
    PreferenceStore.set("default_crossfade_duration", "1.5")
    yield


def make_instance(starts: datetime = START) -> ShowInstance:
    show = baker.make(Show, linked=False, linkable=True)

    return baker.make(
        ShowInstance,
        show=show,
        starts_at=starts,
        ends_at=starts + timedelta(hours=2),
        description="",
        record_enabled=0,
        rebroadcast=0,
        modified=False,
        auto_playlist_built=False,
        filled_time=timedelta(0),
    )


def make_playlist(*lengths: timedelta) -> Playlist:
    """A playlist of files with the given clip lengths."""
    playlist = baker.make(Playlist, name="build me")

    for position, length in enumerate(lengths):
        baker.make(
            PlaylistContent,
            playlist=playlist,
            kind=PlaylistContent.Kind.FILE,
            file=baker.make("storage.File", exists=True, hidden=False, scheduled=False),
            stream=None,
            block=None,
            position=position,
            offset=0.0,
            length=length,
            cue_in=timedelta(0),
            cue_out=length,
            fade_in=time(0, 0, 1),
            fade_out=time(0, 0, 1),
        )

    return playlist


def scheduled(instance: ShowInstance) -> list[Schedule]:
    return list(Schedule.objects.filter(instance=instance).order_by("position"))


def test_playlist_content_keeps_its_order() -> None:
    playlist = make_playlist(
        timedelta(minutes=1), timedelta(minutes=2), timedelta(minutes=3)
    )

    items = playlist_items(playlist)

    assert [item.length for item in items] == [
        timedelta(minutes=1),
        timedelta(minutes=2),
        timedelta(minutes=3),
    ]
    assert all(item.item_id is not None for item in items)


def test_empty_instance_starts_at_its_own_start_and_overlaps_by_crossfade() -> None:
    instance = make_instance()
    playlist = make_playlist(
        timedelta(minutes=10), timedelta(minutes=20), timedelta(minutes=5)
    )

    result = append_playlist(instance, playlist, now=START)

    assert result.items == 3
    assert result.filler is False

    rows = scheduled(instance)
    first, second, third = rows

    assert to_instant(first.starts_at) == START
    assert to_instant(first.ends_at) == START + timedelta(minutes=10)

    # The second starts 1.5 seconds before the first ends.
    assert to_instant(second.starts_at) == to_instant(first.ends_at) - CROSSFADE
    assert to_instant(second.ends_at) == to_instant(second.starts_at) + timedelta(
        minutes=20
    )

    assert to_instant(third.starts_at) == to_instant(second.ends_at) - CROSSFADE
    assert to_instant(third.ends_at) == to_instant(third.starts_at) + timedelta(
        minutes=5
    )

    assert [row.position for row in rows] == [0, 1, 2]


def test_rows_carry_the_cues_fades_and_defaults_of_the_playlist() -> None:
    instance = make_instance()
    playlist = make_playlist(timedelta(minutes=1))

    append_playlist(instance, playlist, now=START)

    row = scheduled(instance)[0]

    assert row.length == timedelta(minutes=1)
    assert row.cue_in == timedelta(0)
    assert row.cue_out == timedelta(minutes=1)
    assert row.fade_in == time(0, 0, 1)
    assert row.fade_out == time(0, 0, 1)
    assert row.file_id is not None
    assert row.stream_id is None
    # The legacy insert lists neither, so both take their column defaults.
    assert row.position_status == Schedule.PositionStatus.INSIDE
    assert row.broadcasted == 0
    assert row.played is False


def test_appending_after_existing_content_overlaps_the_last_item() -> None:
    instance = make_instance()
    append_playlist(instance, make_playlist(timedelta(minutes=10)), now=START)

    # A second playlist lands after the first, crossed into its last item.
    append_playlist(instance, make_playlist(timedelta(minutes=5)), now=START)

    rows = scheduled(instance)

    assert len(rows) == 2
    assert rows[1].position == 1
    assert to_instant(rows[1].starts_at) == to_instant(rows[0].ends_at) - CROSSFADE


def test_a_running_instance_is_filled_up_to_now() -> None:
    """
    Content added to a show that already started cannot be scheduled in the past.

    The legacy code writes a filler row - playout_status -1, never played - over
    the elapsed part and starts the real content at the current instant, without
    overlapping it.
    """
    now = datetime(2030, 1, 1, 12, 15, tzinfo=UTC)
    started = now - timedelta(hours=1)
    instance = make_instance(starts=started)

    append_playlist(instance, make_playlist(timedelta(minutes=5)), now=now)

    rows = scheduled(instance)

    assert len(rows) == 2
    filler, content = rows

    assert filler.position_status == Schedule.PositionStatus.FILLER
    assert to_instant(filler.starts_at) == started
    assert to_instant(filler.ends_at) == now
    assert filler.length == timedelta(hours=1)

    # The first real item is not crossed into the filler.
    assert to_instant(content.starts_at) == now
    assert to_instant(content.ends_at) == now + timedelta(minutes=5)


def test_files_are_marked_as_scheduled() -> None:
    from ....storage.models import File

    instance = make_instance()
    playlist = make_playlist(timedelta(minutes=1))
    file_id = playlist_items(playlist)[0].item_id
    assert file_id is not None

    assert File.objects.get(id=file_id).scheduled is False  # fixture default

    append_playlist(instance, playlist, now=START)

    assert File.objects.get(id=file_id).scheduled is True


def test_the_instance_statistics_are_refreshed() -> None:
    instance = make_instance()
    PreferenceStore.set("shows_populated_until", "2030-01-01 00:00:00")

    append_playlist(instance, make_playlist(timedelta(minutes=10)), now=START)

    instance.refresh_from_db()

    assert instance.filled_time == timedelta(minutes=10)
    assert instance.last_scheduled_at is not None


def test_streams_are_scheduled_without_a_file() -> None:
    instance = make_instance()
    playlist = baker.make(Playlist, name="stream playlist")
    stream: Webstream = baker.make("schedule.Webstream", owner=baker.make("core.User"))

    baker.make(
        PlaylistContent,
        playlist=playlist,
        kind=PlaylistContent.Kind.STREAM,
        file=None,
        stream=stream,
        block=None,
        position=0,
        offset=0.0,
        length=timedelta(minutes=3),
        cue_in=timedelta(0),
        cue_out=timedelta(minutes=3),
        fade_in=time(0, 0, 3),
        fade_out=time(0, 0, 3),
    )

    append_playlist(instance, playlist, now=START)

    row = scheduled(instance)[0]

    assert row.stream_id == stream.pk
    assert row.file_id is None
    # The scheduler hardcodes half a second of fade for streams, whatever the
    # playlist row says.
    assert row.fade_in == time(0, 0, 0, 500000)
    assert row.fade_out == time(0, 0, 0, 500000)


def test_blocks_are_refused_until_the_fill_lands() -> None:
    playlist = baker.make(Playlist, name="with a block")
    baker.make(
        PlaylistContent,
        playlist=playlist,
        kind=PlaylistContent.Kind.BLOCK,
        file=None,
        stream=None,
        block=baker.make("schedule.SmartBlock"),
        position=0,
        offset=0.0,
        length=timedelta(minutes=3),
        cue_in=timedelta(0),
        cue_out=timedelta(minutes=3),
    )

    with pytest.raises(UnsupportedContent):
        playlist_items(playlist)


def test_an_empty_playlist_does_nothing() -> None:
    instance = make_instance()
    playlist = baker.make(Playlist, name="empty")

    result = append_playlist(instance, playlist, now=START)

    assert result.items == 0
    assert Schedule.objects.filter(instance=instance).count() == 0


def test_the_caller_is_told_once_and_only_when_something_was_written() -> None:
    calls: list[str] = []
    instance = make_instance()

    append_playlist(
        instance,
        make_playlist(timedelta(minutes=1)),
        now=START,
        notify=lambda: calls.append("x"),
    )
    append_playlist(
        instance,
        baker.make(Playlist, name="empty"),
        now=START,
        notify=lambda: calls.append("x"),
    )

    assert calls == ["x"]


def test_seconds_to_time_keeps_subsecond_precision() -> None:
    """The table stores fades as TIME, the legacy helper writes SS.uuuuuu."""
    assert seconds_to_time("0.5") == time(0, 0, 0, 500000)
    assert seconds_to_time(2) == time(0, 0, 2)
    assert seconds_to_time("75") == time(0, 1, 15)


def test_fades_stored_as_numbers_are_converted() -> None:
    instance = make_instance()
    playlist = baker.make(Playlist, name="numeric fades")
    baker.make(
        PlaylistContent,
        playlist=playlist,
        kind=PlaylistContent.Kind.FILE,
        file=baker.make("storage.File", exists=True, hidden=False),
        stream=None,
        block=None,
        position=0,
        offset=0.0,
        length=timedelta(minutes=1),
        cue_in=timedelta(0),
        cue_out=timedelta(minutes=1),
        fade_in=time(0, 0, 0, 500000),
        fade_out=time(0, 0, 1),
    )

    items = playlist_items(playlist)

    assert items[0].fade_in == time(0, 0, 0, 500000)

    append_items(instance, items, now=START)

    assert scheduled(instance)[0].fade_in == time(0, 0, 0, 500000)
