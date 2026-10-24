"""
The autoplaylist poll: which instances get built, and with what.

Port of ``AutoPlaylistManager::buildAutoPlaylist()``. The interesting cases are
the order the playlists go in, the repeat-until-full loop and the guards that
stop it, and the poll lock the two services share.
"""

from datetime import datetime, time, timedelta, timezone

import pytest
from model_bakery import baker

from ....core.models import Preference
from ...models import Playlist, PlaylistContent, Schedule, Show, ShowInstance
from ...services.autoplaylist import (
    POLL_LOCK,
    build_autoplaylists,
    poll_interval_passed,
    upcoming_instances,
)
from ...services.populate import PreferenceStore, to_instant

UTC = timezone.utc

NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _database(db):
    PreferenceStore.set("default_crossfade_duration", "0")
    yield


def make_playlist(name: str, length: timedelta) -> Playlist:
    playlist = baker.make(Playlist, name=name)

    baker.make(
        PlaylistContent,
        playlist=playlist,
        kind=PlaylistContent.Kind.FILE,
        file=baker.make("storage.File", exists=True, hidden=False, scheduled=False),
        stream=None,
        block=None,
        position=0,
        offset=0.0,
        length=length,
        cue_in=timedelta(0),
        cue_out=length,
        fade_in=time(0, 0, 1),
        fade_out=time(0, 0, 1),
    )

    return playlist


def make_show(
    *,
    auto_playlist: Playlist | None = None,
    repeat: bool = False,
    intro: Playlist | None = None,
    outro: Playlist | None = None,
) -> Show:
    return baker.make(
        Show,
        linked=False,
        linkable=True,
        auto_playlist_enabled=auto_playlist is not None,
        auto_playlist=auto_playlist,
        auto_playlist_repeat=repeat,
        override_intro_playlist=intro is not None,
        intro_playlist=intro,
        override_outro_playlist=outro is not None,
        outro_playlist=outro,
    )


def make_instance(show: Show, starts: datetime, *, minutes: int = 60) -> ShowInstance:
    return baker.make(
        ShowInstance,
        show=show,
        starts_at=starts,
        ends_at=starts + timedelta(minutes=minutes),
        description="",
        record_enabled=0,
        rebroadcast=0,
        modified=False,
        auto_playlist_built=False,
        filled_time=timedelta(0),
    )


# --------------------------------------------------------------------------
# which instances are picked up
# --------------------------------------------------------------------------


def test_only_upcoming_unbuilt_autoplaylist_shows_are_selected() -> None:
    main = make_playlist("main", timedelta(minutes=10))
    plain = make_show()
    autoplay = make_show(auto_playlist=main)

    inside = make_instance(autoplay, NOW + timedelta(minutes=30))
    too_late = make_instance(autoplay, NOW + timedelta(hours=3))
    already_built = make_instance(autoplay, NOW + timedelta(minutes=40))
    already_built.auto_playlist_built = True
    already_built.save()
    no_playlist = make_instance(plain, NOW + timedelta(minutes=45))
    started = make_instance(autoplay, NOW - timedelta(minutes=5))

    selected = {instance.pk for instance in upcoming_instances(NOW)}

    assert selected == {inside.pk}
    assert too_late.pk not in selected
    assert already_built.pk not in selected
    assert no_playlist.pk not in selected
    assert started.pk not in selected


def test_a_show_without_a_main_playlist_is_left_alone() -> None:
    show = make_show()
    instance = make_instance(show, NOW + timedelta(minutes=10))

    assert build_autoplaylists(now=NOW) == 0

    instance.refresh_from_db()
    assert instance.auto_playlist_built is False
    assert Schedule.objects.filter(instance=instance).count() == 0


# --------------------------------------------------------------------------
# what gets written
# --------------------------------------------------------------------------


def test_intro_then_main_then_outro_in_that_order() -> None:
    main = make_playlist("main", timedelta(minutes=20))
    intro = make_playlist("intro", timedelta(minutes=1))
    outro = make_playlist("outro", timedelta(minutes=2))

    show = make_show(auto_playlist=main, intro=intro, outro=outro)
    instance = make_instance(show, NOW + timedelta(minutes=10))

    assert build_autoplaylists(now=NOW) == 1

    rows = list(Schedule.objects.filter(instance=instance).order_by("position"))

    assert [row.length for row in rows] == [
        timedelta(minutes=1),  # intro, on an empty instance: at the very start
        timedelta(minutes=20),  # main
        timedelta(minutes=2),  # outro, after the repeat loop
    ]
    assert to_instant(rows[0].starts_at) == to_instant(instance.starts_at)

    instance.refresh_from_db()
    assert instance.auto_playlist_built is True


def test_the_global_intro_and_outro_are_used_when_the_show_has_none() -> None:
    main = make_playlist("main", timedelta(minutes=20))
    global_intro = make_playlist("global intro", timedelta(minutes=1))

    Preference.objects.filter(key="intro_playlist").delete()
    PreferenceStore.set("intro_playlist", str(global_intro.pk))

    show = make_show(auto_playlist=main)
    instance = make_instance(show, NOW + timedelta(minutes=10))

    build_autoplaylists(now=NOW)

    rows = list(Schedule.objects.filter(instance=instance).order_by("position"))

    assert [row.length for row in rows] == [
        timedelta(minutes=1),
        timedelta(minutes=20),
    ]


def test_repeat_until_full_adds_the_playlist_until_the_show_is_full() -> None:
    """A 20 minute playlist fills a 60 minute show on the fourth pass."""
    main = make_playlist("main", timedelta(minutes=20))
    show = make_show(auto_playlist=main, repeat=True)
    instance = make_instance(show, NOW + timedelta(minutes=10), minutes=60)

    build_autoplaylists(now=NOW)

    rows = Schedule.objects.filter(instance=instance)

    # 33%, 67%, 100% - and only past 100 does the loop stop, so a fourth pass.
    assert rows.count() == 4
    instance.refresh_from_db()
    assert instance.filled_time is not None
    assert instance.filled_time >= instance.ends_at - instance.starts_at


def test_a_repeating_playlist_that_adds_nothing_is_reported_not_retried(
    caplog,
) -> None:
    """
    The legacy loop breaks silently here; this one logs and moves on.

    A playlist that adds nothing can never fill a show. The original notices by
    comparing the percentage against the previous pass and stops without a word,
    leaving the show marked as built. The port keeps the show unbuilt and says
    which one, because an operator can do nothing about a silent break.
    """
    empty = baker.make(Playlist, name="empty but present")
    show = make_show(auto_playlist=empty, repeat=True)
    instance = make_instance(show, NOW + timedelta(minutes=10))

    with caplog.at_level("WARNING"):
        built = build_autoplaylists(now=NOW)

    assert built == 0
    assert Schedule.objects.filter(instance=instance).count() == 0

    instance.refresh_from_db()
    assert instance.auto_playlist_built is False

    assert "did not fill it" in caplog.text


def test_the_announcement_happens_once_per_built_instance() -> None:
    calls: list[int] = []
    main = make_playlist("main", timedelta(minutes=20))
    show = make_show(auto_playlist=main)
    make_instance(show, NOW + timedelta(minutes=10))
    make_instance(show, NOW + timedelta(minutes=20))

    built = build_autoplaylists(now=NOW, notify=lambda: calls.append(1))

    assert built == 2
    assert len(calls) == 2


# --------------------------------------------------------------------------
# the shared poll lock
# --------------------------------------------------------------------------


def test_the_poll_lock_paces_both_services() -> None:
    Preference.objects.filter(key=POLL_LOCK).delete()

    assert poll_interval_passed(NOW) is True

    PreferenceStore.set(POLL_LOCK, str(NOW.timestamp()))

    assert poll_interval_passed(NOW + timedelta(seconds=30)) is False
    assert poll_interval_passed(NOW + timedelta(seconds=61)) is True


def test_the_lock_moves_only_when_something_was_built() -> None:
    Preference.objects.filter(key=POLL_LOCK).delete()

    build_autoplaylists(now=NOW)

    assert PreferenceStore.get(POLL_LOCK) == ""

    main = make_playlist("main", timedelta(minutes=20))
    make_instance(make_show(auto_playlist=main), NOW + timedelta(minutes=10))

    build_autoplaylists(now=NOW)

    assert float(PreferenceStore.get(POLL_LOCK)) == NOW.timestamp()
