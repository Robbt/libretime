"""
Build the autoplaylists of upcoming shows.

Port of ``AutoPlaylistManager::buildAutoPlaylist()``
(``legacy/application/common/AutoPlaylistManager.php``): every show instance that
starts within the next hour, has an autoplaylist and has not been built yet gets
its intro playlist, its main playlist - repeated while it is still not full, if
the show asks for that - and its outro playlist, and is then marked as built.

Two things about the original are worth keeping in view.

The order is not incidental. The intro is added first, onto an empty instance,
which is what makes appending at the end land it at the start of the show. The
outro is added *after* the repeat-until-full loop rather than inside it, because
an outro that contains a "fill the remaining time" smart block would otherwise
be counted as progress and stop the loop early.

The loop has three exits, and they are all load-bearing: percent scheduled past
one hundred, the show not configured to repeat, and - the one that keeps a
misconfigured show from spinning forever - a main playlist that is null, or that
adds nothing. The last one is the shape of the bug this port refuses to
reproduce: here the no-progress case raises rather than looping.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime, timedelta

from django.utils import timezone as dj_timezone

from ..models import Playlist, ShowInstance
from .populate import PreferenceStore, to_instant
from .scheduler import append_playlist

logger = logging.getLogger(__name__)

# cc_pref key holding when the poll last ran, shared with the legacy service.
POLL_LOCK = "autoplaylist_poll_lock"

# The legacy poll runs at most once a minute, and only builds shows that start
# within the next hour.
POLL_INTERVAL = timedelta(seconds=60)
BUILD_WINDOW = timedelta(hours=1)


class NoProgress(Exception):
    """
    A repeat-until-full show that adds nothing.

    The legacy loop breaks silently in this case, which leaves a show that is not
    full and a log with no trace of why. Raising means the operator finds out.
    """


def _poll_lock() -> float | None:
    raw = PreferenceStore.get(POLL_LOCK)

    if not raw:
        return None

    try:
        return float(raw)
    except ValueError:
        return None


def poll_interval_passed(now: datetime | None = None) -> bool:
    """
    Whether the poll should run again, on the shared preference lock.

    The same key the legacy service uses, so that turning the builder over to the
    API - or back - cannot make both of them build in the same minute.
    """
    moment = now or dj_timezone.now()
    last = _poll_lock()

    if last is None:
        return True

    return moment.timestamp() >= last + POLL_INTERVAL.total_seconds()


def upcoming_instances(now: datetime) -> list[ShowInstance]:
    """
    Show instances that need their autoplaylist built.

    Starts strictly in the future and within the next hour, of a show with an
    autoplaylist, not already built, and not a manually edited instance - the
    same four conditions the legacy query applies.
    """
    return list(
        ShowInstance.objects.filter(
            show__auto_playlist_enabled=True,
            auto_playlist_built=False,
            modified=False,
            starts_at__gt=now,
            starts_at__lt=now + BUILD_WINDOW,
        ).order_by("starts_at", "pk")
    )


def _percent_scheduled(instance: ShowInstance) -> int:
    """
    How full an instance is, as the legacy builder measures it.

    ``ceil(filled / duration * 100)``, and zero when the duration is zero, which
    is the legacy guard against dividing by nothing.
    """
    instance.refresh_from_db(fields=["filled_time"])

    duration = to_instant(instance.ends_at) - to_instant(instance.starts_at)

    if duration <= timedelta(0):
        return 0

    filled = instance.filled_time or timedelta(0)

    percent = (filled.total_seconds() / duration.total_seconds()) * 100

    return math.ceil(percent)


def _playlist_of(instance: ShowInstance) -> Playlist | None:
    show = instance.show

    if not show.auto_playlist_id:
        return None

    return Playlist.objects.filter(pk=show.auto_playlist_id).first()


def _intro_playlist(instance: ShowInstance) -> Playlist | None:
    """The show's own intro, or the installation wide one."""
    show = instance.show

    if show.override_intro_playlist:
        return _named_playlist(show.intro_playlist_id)

    return _named_playlist(_pref_int("intro_playlist"))


def _outro_playlist(instance: ShowInstance) -> Playlist | None:
    show = instance.show

    if show.override_outro_playlist:
        return _named_playlist(show.outro_playlist_id)

    return _named_playlist(_pref_int("outro_playlist"))


def _named_playlist(playlist_id: int | None) -> Playlist | None:
    if not playlist_id:
        return None

    return Playlist.objects.filter(pk=playlist_id).first()


def _pref_int(key: str) -> int | None:
    raw = PreferenceStore.get(key)

    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def build_instance(
    instance: ShowInstance, *, now: datetime, notify: Callable[[], None] | None = None
) -> bool:
    """
    Fill one show instance from its show's playlists.

    Returns whether anything was built; an instance whose show has no main
    playlist is left unbuilt, exactly as the legacy poll leaves it.
    """
    show = instance.show
    main = _playlist_of(instance)

    intro = _intro_playlist(instance)
    if intro is not None:
        append_playlist(instance, intro, now=now)

    percent = _percent_scheduled(instance)

    if main is None:
        # Nothing to add. The legacy loop would break here as well.
        logger.info("show instance %d has no autoplaylist to build", instance.pk)
    else:
        while True:
            append_playlist(instance, main, now=now)

            scheduled = _percent_scheduled(instance)

            if scheduled > 100 or not show.auto_playlist_repeat:
                break

            if scheduled == percent:
                raise NoProgress(
                    f"show instance {instance.pk} repeats its autoplaylist until "
                    f"full but stayed at {scheduled}% - the playlist adds nothing"
                )

            percent = scheduled

    outro = _outro_playlist(instance)
    if outro is not None:
        # Outside the loop on purpose: an outro with a remaining-time block
        # would otherwise be counted as progress and end the repeat early.
        append_playlist(instance, outro, now=now)

    instance.auto_playlist_built = True
    instance.save(update_fields=["auto_playlist_built"])

    if notify is not None:
        notify()

    return True


def build_autoplaylists(
    *, now: datetime | None = None, notify: Callable[[], None] | None = None
) -> int:
    """
    Build the autoplaylists of the shows that start soon.

    Called by the worker on a schedule. The notification is announced per
    instance, like the legacy builder does through ``addPlaylistToShow()``, but
    a caller that prefers one announcement for the whole batch is what the
    ``notify`` parameter is for.

    Returns:
        Number of show instances built.
    """
    moment = now or dj_timezone.now()

    instances = upcoming_instances(moment)

    if not instances:
        return 0

    built = 0

    for instance in instances:
        try:
            if build_instance(instance, now=moment, notify=notify):
                built += 1
        except NoProgress:
            logger.warning(
                "autoplaylist of show instance %d did not fill it",
                instance.pk,
                exc_info=True,
            )

    if built:
        PreferenceStore.set(POLL_LOCK, str(moment.timestamp()))
        logger.info("built %d autoplaylists", built)

    return built
