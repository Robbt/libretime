from datetime import timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger
from django.db.models import F
from django.utils import timezone

from ..notify import update_schedule
from .models import Schedule
from .services.autoplaylist import build_autoplaylists, poll_interval_passed
from .services.populate import PreferenceStore, populate_show_instances

logger = get_task_logger(__name__)

CLEAN_OVERBOOKED_SCHEDULE_SOFT_TIME_LIMIT = timedelta(minutes=28)
CLEAN_OVERBOOKED_SCHEDULE_TIME_LIMIT = timedelta(minutes=30)

# The horizon the legacy AutoPlaylistManager keeps, copied so that switching
# general.show_instance_populator between the two services does not move the
# calendar horizon - the instance of a show are created by whichever service
# gets there first and both apply the same rule.
POPULATE_AHEAD = timedelta(days=7)
POPULATE_MIN_AHEAD = timedelta(days=2)
POPULATE_SOFT_TIME_LIMIT = timedelta(minutes=4)
POPULATE_TIME_LIMIT = timedelta(minutes=5)

BUILD_AUTOPLAYLISTS_SOFT_TIME_LIMIT = timedelta(minutes=2)
BUILD_AUTOPLAYLISTS_TIME_LIMIT = timedelta(minutes=3)


@shared_task(
    soft_time_limit=CLEAN_OVERBOOKED_SCHEDULE_SOFT_TIME_LIMIT.seconds,
    time_limit=CLEAN_OVERBOOKED_SCHEDULE_TIME_LIMIT.seconds,
)
def clean_overbooked_schedule(batch_size: int = 10_000) -> int:
    """
    Clean unused overbooked schedule items.

    When the schedule items for a show instances are generated, the schedule generates
    more items that can fit the show instance duration. Once the show instance has aired,
    the is no need to keep the overbooked schedule items.

    Args:
        batch_size: Number of schedule items deleted per query.

    Returns:
        Number of deleted schedule items.
    """
    keep_since = timezone.now() - timedelta(days=90)

    items = (
        Schedule.objects.filter(
            # Keep the last 3 months regardless.
            instance__ends_at__lt=keep_since,
            ends_at__lt=keep_since,
            # Keep broadcasted or played schedules items.
            played=False,
            broadcasted=0,
            # Overbooked items start after the end of their show instance.
            starts_at__gt=F("instance__ends_at"),
            # Overbooked items are outside the boundary of their show instance.
            position_status=Schedule.PositionStatus.OUTSIDE,
        )
        .order_by()
        .values_list("pk", flat=True)
    )

    count = 0
    try:
        while True:
            batch_count, _ = Schedule.objects.filter(pk__in=items[:batch_size]).delete()
            count += batch_count
            if batch_count < batch_size:
                break

    except SoftTimeLimitExceeded:
        logger.warning(
            "task soft time limit exceeded (%s), exiting",
            CLEAN_OVERBOOKED_SCHEDULE_SOFT_TIME_LIMIT,
        )

    logger.info("deleted %d overbooked schedule items", count)
    return count


@shared_task(
    soft_time_limit=POPULATE_SOFT_TIME_LIMIT.seconds,
    time_limit=POPULATE_TIME_LIMIT.seconds,
)
def populate_shows() -> int:
    """
    Keep the future show calendar populated.

    Repeating shows exist as rows only up to the ``shows_populated_until``
    preference, so somebody has to extend it ahead of the clock. This task is
    scheduled only when ``general.show_instance_populator`` says the API owns
    that job; the legacy service otherwise keeps doing it and takes over again
    when this one stops running.

    Returns:
        Number of show instances created, zero when the horizon is still long
        enough to leave alone.
    """
    now = timezone.now()
    horizon = PreferenceStore.shows_populated_until()

    # Only a cheap way to avoid taking out a serializable transaction every
    # half hour. The guard that decides is the one inside the transaction.
    if horizon is not None and horizon >= now + POPULATE_MIN_AHEAD:
        return 0

    until = now + POPULATE_AHEAD

    logger.info(
        "extending show instances from %s to %s",
        horizon.strftime("%Y-%m-%d %H:%M:%S") if horizon else "unset",
        until.strftime("%Y-%m-%d %H:%M:%S"),
    )

    return populate_show_instances(until)


@shared_task(
    soft_time_limit=BUILD_AUTOPLAYLISTS_SOFT_TIME_LIMIT.seconds,
    time_limit=BUILD_AUTOPLAYLISTS_TIME_LIMIT.seconds,
)
def build_upcoming_autoplaylists() -> int:
    """
    Fill the shows that start within the hour from their playlists.

    Scheduled only when ``general.auto_playlist_builder`` says this worker owns
    the job; the legacy service keeps a slower poll either way, and both read the
    same poll lock, so handing the job over cannot make them build twice in the
    same minute.

    Returns:
        Number of show instances built.
    """
    if not poll_interval_passed():
        return 0

    built = build_autoplaylists(notify=update_schedule)

    return built
