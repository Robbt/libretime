"""
The show editor: turning the editor's payload into a show.

Port of ``Application_Service_ShowService``.  What the editor does is more than
insert a row: one save writes the show, its repeat rules, its rebroadcasts and its
hosts, then creates the instances that fall inside the populated window, and —
when the show is being edited — reconciles the instances that already exist with
the ones the new rules imply, moving the content scheduled inside them.

The payload is the legacy form's own keys, because the editor is one of the last
screens to be moved and its interface — the calendar's show dialog — posts exactly
this shape. ``tools/show-editor-equivalence`` feeds the same payloads to php and to
this code and compares the resulting rows, so "equivalent" here means measured
rather than intended.

Three places where this code refuses to inherit an accident of php, each marked
where it happens:

* an exception rolls the whole save back and is raised, instead of being logged and
  swallowed while the caller is handed a show id;
* a value that cannot be interpreted as a date, a time or a duration is rejected
  with a message naming the field, instead of being coerced by php's comparison
  rules into whatever the form happened to contain;
* the show, its days and its rebroadcasts are written in one transaction, as php
  does, but the notification to playout happens only after it commits — a rolled
  back save must not tell the playout service to reload a schedule that did not
  change.
"""

# Port of legacy/application/services/ShowService.php, kept in one module so that a
# reviewer can read it side by side with the php it replaces. The 1000-line default
# measures files that grew without anyone deciding; this one is long because the service
# it mirrors is long, and what pins its behaviour is tools/show-editor-equivalence.
# pylint: disable=too-many-lines

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import connection, models, transaction
from django.utils import timezone

from ...core.models import Preference
from ...notify.playout import update_schedule
from ...storage.models import File
from ..models import Schedule, Show, ShowDays, ShowHost, ShowInstance, ShowRebroadcast
from .populate import (
    NO_REPEAT,
    REPEAT_MONTHLY_MONTHLY,
    InstanceBuilder,
    PreferenceStore,
    fill_linked_instances,
    to_instant,
)

logger = logging.getLogger(__name__)

# legacy/application/configs/constants.php
MAX_REBROADCAST_DATES = 10

# The editor numbers the week the way php's date("w") does: 0 is Sunday.
DAY_RANGE = range(0, 7)


class ShowFormError(ValueError):
    """The payload cannot describe a show; it names the field at fault."""


@dataclass(frozen=True)
class Rebroadcast:
    """One rebroadcast: an offset in days from the show date, and a time."""

    day_offset: int
    start_time: time


@dataclass(frozen=True)
class ShowForm:
    # One attribute per value the show editor posts, because the port's job is to read
    # that payload and nothing else; splitting these into groups would move the reader
    # further from the php the equivalence harness compares against.
    # pylint: disable=too-many-instance-attributes
    """
    A show as the editor describes it.

    Attributes:
        show_id: ``None`` for a new show; the id of the show being edited
            otherwise.
        instance_id: The instance the dialog was opened from, which a linked show
            copies its content from.
        repeat_kind: One of ``RepeatType``, already resolved: the editor sends
            repeat type 2 for "monthly" and puts the real choice — on a date or on
            a weekday — in a separate field, like the legacy constructor does.
    """

    show_id: int | None
    instance_id: int | None

    name: str
    description: str | None
    url: str | None
    genre: str | None
    #: The show's colours. Required by everything that writes the show row, and
    #: absent for the "edit this instance" payload, which describes one night rather
    #: than the show and never touches them.
    color: str | None
    background_color: str | None

    live_registered: bool
    live_custom: bool
    live_user: str | None
    live_password: str | None

    auto_playlist_enabled: bool
    auto_playlist_id: int | None
    auto_playlist_repeat: bool
    intro_playlist_id: int | None
    outro_playlist_id: int | None

    linked: bool

    start_date: date
    start_time: time
    timezone_name: str
    #: ``cc_show_days.duration`` as the column holds it, ``"1:30"``.  The editor
    #: receives ``"01h 30m"``; ``format_duration`` makes the column form.
    duration: str

    end_date: date | None
    no_end: bool

    repeats: bool
    repeat_kind: int
    #: Whether the form said "monthly" (repeat type 2), which is not the same
    #: question as the resolved ``repeat_kind``: the editor can also send 3
    #: directly, and the legacy code branches on the submitted value when it
    #: decides whether a repeat rule carries a weekday at all.
    monthly_by_form: bool
    days: tuple[int, ...]

    record: bool
    rebroadcast: bool
    rebroadcasts: tuple[Rebroadcast, ...]

    hosts: tuple[int, ...] = ()
    #: The note the "edit this instance" dialog writes onto the instance it splits out
    #: of a repeating show. Not the show's description.
    instance_description: str | None = None

    # -- parsing --------------------------------------------------------------

    @classmethod
    def from_legacy_payload(
        cls, data: Mapping[str, Any], *, with_colors: bool = True
    ) -> ShowForm:
        """
        Read the payload the show dialog posts.

        ``with_colors`` is off only for the "edit this instance" dialog, which
        carries no colours because it does not touch the show row - and which would
        otherwise be refused for a field it never intended to send.

        This is the one reading of a show payload: the dialog posts it, the api
        rebuilds it from whatever vocabulary the client used, and the equivalence
        harness feeds php the same dictionary. Rows are written from here, so a
        show means the same thing whichever way it was asked for.
        """
        repeats = bool(data.get("add_show_repeats"))
        repeat_kind = _repeat_kind(data, repeats)
        start_date = _required_date(
            data.get("add_show_start_date"), "add_show_start_date"
        )

        return cls(
            show_id=_id(data.get("add_show_id")),
            instance_id=_id(data.get("add_show_instance_id")),
            instance_description=_text(data.get("add_show_instance_description")),
            name=_required_text(data.get("add_show_name"), "add_show_name"),
            description=_text(data.get("add_show_description")),
            url=_text(data.get("add_show_url")),
            genre=_text(data.get("add_show_genre")),
            color=(
                _required_text(data.get("add_show_color"), "add_show_color")
                if with_colors
                else _text(data.get("add_show_color"))
            ),
            background_color=(
                _required_text(
                    data.get("add_show_background_color"), "add_show_background_color"
                )
                if with_colors
                else _text(data.get("add_show_background_color"))
            ),
            live_registered=_flag(data.get("cb_airtime_auth")),
            live_custom=_flag(data.get("cb_custom_auth")),
            live_user=_text(data.get("custom_username")),
            live_password=_text(data.get("custom_password")),
            auto_playlist_enabled=_flag(data.get("add_show_has_autoplaylist")),
            auto_playlist_id=_id(data.get("add_show_autoplaylist_id")),
            auto_playlist_repeat=_flag(data.get("add_show_autoplaylist_repeat")),
            intro_playlist_id=_id(data.get("add_show_intro_playlist_id")),
            outro_playlist_id=_id(data.get("add_show_outro_playlist_id")),
            linked=_flag(data.get("add_show_linked")),
            start_date=start_date,
            start_time=_time(data["add_show_start_time"], "add_show_start_time"),
            timezone_name=_timezone(data.get("add_show_timezone")),
            # A zone the system cannot resolve is refused here rather than at
            # instance creation: the show day would be written, and the show would
            # come back half made. php notices only when it builds instances, inside
            # the transaction it then rolls back and logs.
            duration=format_duration(_duration_of(data)),
            end_date=_date(data.get("add_show_end_date"), "add_show_end_date"),
            no_end=_flag(data.get("add_show_no_end")),
            repeats=repeats,
            repeat_kind=repeat_kind,
            monthly_by_form=repeats
            and _int(data.get("add_show_repeat_type"), "add_show_repeat_type") == 2,
            days=tuple(_days(data, repeats, start_date)),
            record=_flag(data.get("add_show_record")),
            rebroadcast=_flag(data.get("add_show_rebroadcast")),
            rebroadcasts=_rebroadcasts(data, repeat_kind),
            hosts=_hosts(data.get("add_show_hosts")),
        )


def save_show(form: ShowForm, *, is_update: bool = False, now: datetime | None = None):
    """
    Write a show exactly as the editor would, and return it.

    Args:
        form: The parsed payload.
        is_update: Whether the show already exists. An edit reconciles what is
            already on the calendar with the rules the payload describes: it adopts
            the instances that still belong, removes the ones the new rules no
            longer produce, and moves the content scheduled inside the survivors.
            The whole of that only happens on an update, like the legacy service.
        now: Instant of the request, for the "do not schedule into the past"
            checks. Injectable so the tests and the harness are not at the mercy of
            the clock.

    Raises:
        ShowFormError: a field could not be read as the thing it names.
    """
    now = now or timezone.now()

    with transaction.atomic():
        show = _save_show(form, is_update)
        original = _read_original(show) if is_update else None
        scheduled: list[int] = []

        if original is not None:
            # Before the new rules are written, because the cleanup compares the
            # payload against what is in the database now.
            _cleanup_instances(form, show, original, now)
            _delete_future_rebroadcast_instances(show, now)
            _delete_hosts(show)

            if form.rebroadcast:
                _delete_rebroadcast_rules(show)

            # Which instances hold scheduled content that is about to have to move.
            scheduled = list(_future_instance_ids(show, now))

        _save_show_days(form, show, original)
        _save_rebroadcasts(form, show)
        _save_hosts(form, show)

        created = _create_instances(show, now=now, original=original)

        if original is not None:
            _adjust_schedule(form, show, original, scheduled, now)

    # Outside the transaction: a rolled back save must not tell playout to reload.
    update_schedule()

    return show, created


# -- rows ----------------------------------------------------------------------


def _save_show(form: ShowForm, is_update: bool) -> Show:
    """
    The ``cc_show`` row.

    Port of ``setCcShow()``.  Two of its quirks are reproduced rather than fixed,
    because both are observable in the row and the harness would otherwise differ
    for a reason nobody chose:
    """
    if is_update:
        if form.show_id is None:
            raise ShowFormError("add_show_id: an edit has to say which show")

        show = Show.objects.filter(pk=form.show_id).first()

        if show is None:
            raise ShowFormError(f"add_show_id: no show {form.show_id}")
    else:
        show = Show()

    show.name = form.name
    show.description = form.description
    show.url = form.url
    show.genre = form.genre
    if form.color is not None:
        show.foreground_color = form.color
        show.background_color = form.background_color
    show.live_auth_registered = form.live_registered
    show.live_auth_custom = form.live_custom
    show.live_auth_custom_user = form.live_user
    show.live_auth_custom_password = form.live_password
    show.auto_playlist_enabled = form.auto_playlist_enabled
    show.auto_playlist_repeat = form.auto_playlist_repeat

    # php writes the autoplaylist only when the submitted value is not the empty
    # string, so an edit that clears it leaves the previous one in place. Kept, and
    # reported: it means a show can keep an intro playlist its editor removed.
    if form.auto_playlist_id is not None:
        show.auto_playlist_id = form.auto_playlist_id

    # And the override flag comes from the playlist id, not from the form's own
    # override checkbox - which is why the checkbox is not read here.
    show.override_intro_playlist = bool(form.intro_playlist_id)
    if form.intro_playlist_id is not None:
        show.intro_playlist_id = form.intro_playlist_id

    show.override_outro_playlist = bool(form.outro_playlist_id)
    if form.outro_playlist_id is not None:
        show.outro_playlist_id = form.outro_playlist_id

    if show.image is None:
        # php's ORM writes the column default; Django would write null.
        show.image = ""

    if show.pk is None:
        # Same reason: is_linkable defaults to true in the schema.
        show.linkable = True

    show.linked = form.linked
    show.save()

    return show


def _save_show_days(
    form: ShowForm, show: Show, original: _Original | None = None
) -> None:
    """
    The ``cc_show_days`` rows: one per repeating day, or one for a single show.

    Port of ``setCcShowDays()``.  Each rule starts on the first date *on or after*
    the given start date that falls on its weekday, which is why a show started on
    a Wednesday but set to repeat on Mondays has its first Monday after the 1st.
    """
    end = _effective_end(form)

    first_dow = form.start_date.weekday()
    # php's date("w"): 0 is Sunday, 1 is Monday ... 6 is Saturday.
    start_dow = (first_dow + 1) % 7

    if form.monthly_by_form:
        # "Don't set day for monthly repeat type, it's invalid" - the weekday of a
        # monthly show comes from its first show date.
        #
        # Keyed on the submitted repeat type rather than the resolved kind, which
        # is what the legacy branch reads: a show that arrives with repeat type 3 -
        # monthly on the Nth weekday - takes the branch below and keeps a weekday.
        # The instance dates are the same either way, since the monthly rule derives
        # its weekday from the first show date; the column just says something
        # different, and the harness reports that as a row, not a shrug.
        _store_day(
            show,
            first_show=form.start_date,
            week_day=None,
            form=form,
            end=end,
            original=original,
        )

        return

    for day in form.days:
        shifted = _roll_to_weekday(form.start_date, start_dow, day)

        if end is not None and _midnight(shifted) > end:
            # A rule whose first occurrence is past the last show is not written at
            # all: adding a repeat day that starts after the show ends does nothing.
            continue

        _store_day(
            show,
            first_show=shifted,
            week_day=day,
            form=form,
            end=end,
            original=original,
        )


def _existing_rule(
    show: Show, original: _Original, week_day: int | None
) -> ShowDays | None:
    """
    The rule this edit is rewriting, if there is one.

    An edit does not add a rule per weekday and leave the old ones: it finds the row
    that describes the same night and overwrites it, so the show keeps one row per
    night it airs and the ids stay where the instances' history points.

    The search uses the repeat kind the show *had*, not the one the payload
    describes: changing a weekly show to biweekly is the same row with a different
    interval, and changing a monthly show drops the weekday from the search, since a
    monthly rule has none - it takes its weekday from its first show date. A null
    weekday is matched as null, which is what a monthly rule stores.
    """
    lookup = week_day

    if original.repeat_kind in (2, 3):
        lookup = None
    elif original.repeat_kind == NO_REPEAT:
        # A show that used to run once is being made to repeat: the row to rewrite
        # is the one the single instance came from, whichever weekday that fell on.
        lookup = original.week_day

    rules = ShowDays.objects.filter(show=show, repeat_kind=original.repeat_kind)

    if lookup is None:
        rules = rules.filter(week_day__isnull=True)
    else:
        rules = rules.filter(week_day=lookup)

    return rules.order_by("id").first()


def _store_day(
    show: Show,
    *,
    first_show: date,
    week_day: int | None,
    form: ShowForm,
    end: datetime | None,
    original: _Original | None = None,
) -> ShowDays:
    """
    Write one repeat rule, over the previous one if this is an edit.

    A new rule for a night that already had one would leave the show with two
    descriptions of the same airing, and the instance builder would obey both.
    """
    rule = _existing_rule(show, original, week_day) if original else None

    if rule is None:
        rule = ShowDays()

    rule.show = show
    rule.first_show_on = first_show
    rule.last_show_on = end.date() if end else None
    rule.start_time = form.start_time
    rule.timezone = form.timezone_name
    rule.duration = form.duration
    rule.week_day = week_day
    rule.repeat_kind = form.repeat_kind
    rule.record_enabled = 1 if form.record else 0
    # Where instance generation resumes. Reset to the first show on every save, so
    # editing a show rebuilds its calendar from the beginning - which is what the
    # instance reuse then walks over: the nights that already exist are found and
    # kept, not created again beside themselves.
    rule.repeat_next_on = first_show
    rule.save()

    return rule


def _create_day_row(
    show: Show,
    *,
    first_show: date,
    week_day: int | None,
    form: ShowForm,
    end: datetime | None,
) -> ShowDays:
    """Write one repeat rule."""
    return ShowDays.objects.create(
        show=show,
        first_show_on=first_show,
        last_show_on=end.date() if end else None,
        start_time=form.start_time,
        timezone=form.timezone_name,
        duration=form.duration,
        week_day=week_day,
        repeat_kind=form.repeat_kind,
        record_enabled=1 if form.record else 0,
        # Where instance generation resumes. Reset to the first show on every
        # save, so editing a show rebuilds its calendar from the beginning.
        repeat_next_on=first_show,
    )


def _save_rebroadcasts(form: ShowForm, show: Show) -> None:
    """
    The ``cc_show_rebroadcast`` rows, for a recorded show only.

    Port of ``setCcShowRebroadcasts()``: rebroadcasts exist for recorded shows,
    and for a show that does not repeat the editor picks absolute dates, which are
    stored as an offset from the first show because that is all the instance
    builder needs.
    """
    if not (form.record and form.rebroadcast):
        return

    for rebroadcast in form.rebroadcasts:
        ShowRebroadcast.objects.create(
            show=show,
            day_offset=f"{rebroadcast.day_offset} days",
            start_time=rebroadcast.start_time,
        )


def _delete_hosts(show: Show) -> None:
    """
    Every host of the show, before they are written again.

    php deletes and re-inserts rather than diffing, which is why the ids the
    ``cc_show_hosts`` rows carry change on an edit that did not touch the hosts at
    all. Nothing references those rows, so it does not matter - but it is the kind of
    thing an "obviously equivalent" refactor gets wrong, so the crudeness is kept.
    """
    ShowHost.objects.filter(show=show).delete()


def _save_hosts(form: ShowForm, show: Show) -> None:
    """The ``cc_show_hosts`` rows. An empty list leaves the show unhosted."""
    for host_id in form.hosts:
        ShowHost.objects.create(show=show, user_id=host_id)


# -- instances -----------------------------------------------------------------


def _create_instances(
    show: Show,
    *,
    now: datetime,
    original: _Original | None = None,
    fill_instances: bool = False,
) -> dict[int, list[int]]:
    """
    Create the instances the populated window calls for.

    Port of ``delegateInstanceCreation()`` for the save path: every rule of this
    show, including a non repeating one, up to the date the calendar has been
    populated to. The populate task uses the same builder for the rules that have
    run out of instances; the difference is only in which show days are selected.
    """
    populate_until = PreferenceStore.shows_populated_until() or to_instant(now)

    builder = InstanceBuilder(
        now=to_instant(now),
        adopt=_informer(original, show) if original else None,
        is_update=original is not None,
    )

    for day in ShowDays.objects.filter(show=show).order_by("id"):
        if day.repeat_kind == NO_REPEAT:
            builder.create_single(day, populate_until)
        else:
            builder.build([day], populate_until=populate_until)

    if (fill_instances or original is not None) and show.linked:
        # A linked show's instances all air the same content, so the ones that did
        # not exist a moment ago have to be given it. On a create there is nothing
        # to copy from - a brand new show has no instance holding anything - which
        # is why the legacy service runs this only for an edit, or when the calendar
        # has been moved forward and asked to fill.
        fill_linked_instances(
            show,
            builder.new_instance_ids.get(show.pk, []),
            populated_until=populate_until,
            now=now,
        )
    elif show.linked:
        logger.debug("linked show %s created without content to copy", show.pk)

    return builder.new_instance_ids


# -- payload reading -----------------------------------------------------------


def format_duration(value: str) -> str:
    """
    ``"01h 30m"`` to the ``"1:30"`` the interval column is given.

    Port of ``formatShowDuration()``, reproduced exactly - including that it is
    text surgery, not parsing: the hour part keeps its leading zero if the form
    sent one, and a value with neither unit becomes ``"0:0"``. Rounding it into
    shape here would make the row differ from php's for a reason nobody chose.
    """
    hours = "0"
    minutes = "0"

    hour_at = value.find("h")
    minute_at = value.find("m")

    if hour_at != -1:
        hours = value[:hour_at].strip()

    if minute_at != -1:
        minutes = value[(hour_at + 1) if hour_at != -1 else 0 : minute_at].strip()

    return f"{hours}:{minutes}"


def _duration_of(data: Mapping[str, Any]) -> str:
    """The editor sends "01h 30m"; the row stores an interval."""
    raw = data.get("add_show_duration")

    if not isinstance(raw, str) or not raw.strip():
        raise ShowFormError("add_show_duration: a duration is required")

    return raw


def _repeat_kind(data: Mapping[str, Any], repeats: bool) -> int:
    if not repeats:
        return NO_REPEAT

    kind = _int(data.get("add_show_repeat_type"), "add_show_repeat_type")

    if kind == 2:
        # "Monthly" in the form, refined by a second control.
        return _int(
            data.get("add_show_monthly_repeat_type"), "add_show_monthly_repeat_type"
        )

    return kind


def _days(data: Mapping[str, Any], repeats: bool, start_date: date) -> list[int]:
    """
    The weekdays to repeat on.

    A show that does not repeat gets the weekday of its start date, which is how
    the legacy code gives a single instance a day at all.
    """
    if not repeats:
        return [(start_date.weekday() + 1) % 7]

    submitted = data.get("add_show_day_check")

    if submitted is None:
        raise ShowFormError("add_show_day_check: a repeating show needs a day")

    if not isinstance(submitted, (list, tuple)):
        submitted = [submitted]

    days = [_int(day, "add_show_day_check") for day in submitted]

    if not days or any(day not in DAY_RANGE for day in days):
        raise ShowFormError("add_show_day_check: days must be between 0 and 6")

    return days


def _rebroadcasts(data: Mapping[str, Any], repeat_kind: int) -> tuple[Rebroadcast, ...]:
    """
    The rebroadcast rules, as offsets from the first show.

    A repeating show is given offsets directly; a show that does not repeat is
    given dates, and the offset is the number of days between them - absolute, as
    php's ``%a`` is, so a rebroadcast before the show is a positive offset too.
    """
    absolute = repeat_kind == NO_REPEAT
    rules: list[Rebroadcast] = []

    for index in range(1, MAX_REBROADCAST_DATES + 1):
        suffix = f"_{index}"
        date_key = f"add_show_rebroadcast_date{'_absolute' if absolute else ''}{suffix}"
        time_key = f"add_show_rebroadcast_time{'_absolute' if absolute else ''}{suffix}"

        raw_date = data.get(date_key)
        raw_time = data.get(time_key)

        if not raw_date or not raw_time:
            continue

        day_offset = _int(raw_date, date_key) if not absolute else None

        if day_offset is None:
            start = _required_date(
                data.get("add_show_start_date"), "add_show_start_date"
            )
            when = _required_date(raw_date, date_key)
            day_offset = abs((when - start).days)

        rules.append(Rebroadcast(day_offset, _time(raw_time, time_key)))

    return tuple(rules)


def _hosts(value: Any) -> tuple[int, ...]:
    if value is None:
        return ()

    if not isinstance(value, (list, tuple)):
        raise ShowFormError("add_show_hosts: expected a list of user ids")

    return tuple(_int(host, "add_show_hosts") for host in value)


def _effective_end(form: ShowForm) -> datetime | None:
    """
    The exclusive end of the show's run, in UTC.

    Port of ``calculateEndDate()``: no end date means no end; a repeating show ends
    the day after its last date; a show that does not repeat ends the day after it
    starts. The extra day is what makes ``last_show`` inclusive for the rules and
    exclusive for the instance loop.
    """
    if form.no_end:
        return None

    if form.repeats:
        if form.end_date is None:
            raise ShowFormError("add_show_end_date: a repeating show needs an end date")

        return datetime.combine(form.end_date, time.min) + timedelta(days=1)

    return datetime.combine(form.start_date, time.min) + timedelta(days=1)


def _midnight(day: date) -> datetime:
    """
    Midnight of ``day``, with no zone.

    ``_effective_end`` is naive for the same reason: it is a comparison against a
    date the editor picked, and the show's own timezone belongs to the instances,
    not to the question "is this day past the end".
    """
    return datetime.combine(day, time.min)


def _roll_to_weekday(start: date, start_dow: int, wanted: int) -> date:
    """
    First date on or after ``start`` falling on ``wanted``.

    Port of the arithmetic in ``setCcShowDays()``, including its wrap: when the
    wanted day is earlier in the week the date moves forward into next week rather
    than back into the past.
    """
    if start_dow == wanted:
        return start

    forward = (wanted - start_dow) % 7

    return start + timedelta(days=forward)


def _timezone(value: Any) -> str:
    name = _required_text(value, "add_show_timezone")

    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ShowFormError(
            f"add_show_timezone: unknown time zone ({name!r})"
        ) from error

    return name


def _flag(value: Any) -> bool:
    """php's ``== 1`` on a checkbox, with the strings an html form produces."""
    if isinstance(value, str):
        return value.strip() in ("1", "true", "on", "yes")

    return bool(value)


def _id(value: Any) -> int | None:
    """The form uses -1 for "none"."""
    if value in (None, "", -1, "-1"):
        return None

    return int(value)


def _int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ShowFormError(f"{field_name}: expected a number") from error


def _text(value: Any, field_name: str = "text") -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise ShowFormError(f"{field_name}: expected text")

    return value


def _required_text(value: Any, field_name: str) -> str:
    text = _text(value, field_name)

    if text is None:
        raise ShowFormError(f"{field_name}: required")

    return text


def _required_date(value: Any, field_name: str) -> date:
    parsed = _date(value, field_name)

    if parsed is None:
        raise ShowFormError(f"{field_name}: required")

    return parsed


def _date(value: Any, field_name: str) -> date | None:
    if value in (None, ""):
        return None

    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise ShowFormError(f"{field_name}: not a date ({value!r})") from error


def _time(value: Any, field_name: str) -> time:
    if value in (None, ""):
        raise ShowFormError(f"{field_name}: a time is required")

    if isinstance(value, time):
        return value

    parts = str(value).split(":")

    if len(parts) < 2:
        raise ShowFormError(f"{field_name}: not a time ({value!r})")

    try:
        return time(int(parts[0]), int(parts[1]))
    except ValueError as error:
        raise ShowFormError(f"{field_name}: not a time ({value!r})") from error


__all__ = ["Rebroadcast", "ShowForm", "ShowFormError", "save_show"]


# -- deleting -------------------------------------------------------------------


class ShowInstanceNotFound(KeyError):
    """The instance the caller named does not exist."""


class ShowInstanceInThePast(ValueError):
    """
    The instance has already aired.

    php says "Cannot delete a show instance in the past", and the reason is not
    tidiness: playout keeps its own idea of what it has broadcast, and history that
    refers to an instance nobody can look up is worse than a show nobody wanted.
    """


def delete_show_instance(
    instance_id: int,
    *,
    single_instance: bool = False,
    now: datetime | None = None,
) -> int:
    """
    Take an instance - or an instance and everything after it - off the air.

    Returns the id of the show it belonged to, which the caller needs because a
    show with no instances left stops existing: ``cc_show`` is a definition, and
    a definition with nothing to instantiate is not a show.

    ``single_instance`` is the calendar's two menu entries: "delete this instance"
    and "delete this instance and all following".
    """
    when = now or timezone.now()

    with transaction.atomic():
        instance = ShowInstance.objects.filter(pk=instance_id).first()

        if instance is None:
            raise ShowInstanceNotFound(instance_id)

        show_id = instance.show_id

        if not single_instance:
            _delete_show_images(show_id)

        if single_instance:
            victims = [instance]
        else:
            victims = list(
                ShowInstance.objects.filter(
                    show_id=show_id, starts_at__gte=to_instant(instance.starts_at)
                ).order_by("id")
            )

        if when > to_instant(instance.ends_at):
            raise ShowInstanceInThePast(instance_id)

        _delete_instances(victims, show_id)

        _unmark_files_no_longer_scheduled(when)

    update_schedule()

    return show_id


def _delete_instances(instances: list[ShowInstance], show_id: int) -> None:
    """
    Soft-delete the instances, hard-delete what was inside them.

    An instance that was deleted by the calendar is kept, flagged
    ``modified_instance``, for as long as the show survives: it is the difference
    between "this show does not air that night" and "this show never existed", and
    playout's history points at the row. What is removed outright is the content
    scheduled inside it, and the rebroadcasts of a recording that will now never be
    made.
    """
    recordings = []

    for instance in instances:
        instance.modified = True
        instance.save(update_fields=["modified"])
        Schedule.objects.filter(instance=instance).delete()

        if instance.record_enabled:
            recordings.append(instance)

    # Second pass, because a rebroadcast of a recording that has just been
    # cancelled is often itself among the instances being cancelled: php deletes
    # them as it walks the list and does not care that Propel's later update of an
    # already-deleted row matches nothing. Django does care, and so does the
    # meaning - those rows are not being cancelled, they are gone.
    for recording in recordings:
        ShowInstance.objects.filter(instance=recording).delete()

    if _has_no_live_instances(show_id):
        _delete_show(show_id)
    elif len(instances) > 1:
        _set_last_repeating_show_date(show_id)


def _has_no_live_instances(show_id: int) -> bool:
    """
    Whether anything the show still puts on the air is left.

    Soft-deleted instances do not count, and neither do rebroadcasts: they are
    consequences of an instance rather than one of the show's own.
    """
    return not ShowInstance.objects.filter(
        show_id=show_id, modified=False, rebroadcast=0
    ).exists()


def _delete_show(show_id: int) -> None:
    """
    Remove the show and everything that only exists because of it.

    This is where the port deliberately does more than php. ``cc_show_days.show_id``
    and ``cc_show_instances.show_id`` have no foreign key in the shipped schema -
    only ``cc_show_hosts`` does, with a cascade - so the legacy ``deleteShow``
    deletes the ``cc_show`` row and leaves the repeat rules and the soft-deleted
    instances pointing at an id that now belongs to nobody. Upstream's own dataset
    for this scenario expects them gone, because the test database is built from the
    installer sql which has the cascades the production schema does not: a bug its
    test suite is structurally unable to see. The row is deleted here with its
    children, which is what the expectation says and what a station wants.
    """
    # Queued content first: cc_schedule has no foreign key to anything, so
    # deleting the rows below by hand still leaves its items behind.
    Schedule.objects.filter(instance__show_id=show_id).delete()
    ShowHost.objects.filter(show_id=show_id).delete()
    ShowRebroadcast.objects.filter(show_id=show_id).delete()
    ShowDays.objects.filter(show_id=show_id).delete()
    ShowInstance.objects.filter(show_id=show_id).delete()
    Show.objects.filter(pk=show_id).delete()


def _delete_show_images(show_id: int) -> None:
    """
    Show artwork is storage this api does not have yet.

    php's ``deleteShow`` removes the images from the upload storage whenever it
    deletes a whole show, and fails the delete if the files cannot be removed. The
    api neither writes nor serves show images - ``image_path`` is still written by
    the legacy screens - so there is nothing here to remove, and pretending
    otherwise would mean inventing a path convention to delete by.
    """
    logger.debug("show %s: image storage is still the legacy application's", show_id)


def _set_last_repeating_show_date(show_id: int) -> None:
    """
    Tell each repeat rule where the show stopped, so the populator does not put it back.

    Without this, deleting a show's last instances from the calendar is temporary:
    the periodic populate reads ``next_pop_date`` and ``last_show``, sees no end,
    and cheerfully creates the instances somebody just removed. The date is the
    last remaining instance's start plus a day, in the rule's own timezone - the
    same arithmetic the create path used to decide the show had not ended.
    """
    live = list(
        ShowInstance.objects.filter(
            show_id=show_id, modified=False, rebroadcast=0
        ).order_by("starts_at")
    )

    if not live:
        return

    rules = list(ShowDays.objects.filter(show_id=show_id).order_by("id"))
    timezone_name = _rule_timezone(rules)

    last_by_week_day: dict[int, datetime] = {}

    for instance in live:
        local = to_instant(instance.starts_at).astimezone(ZoneInfo(timezone_name))
        # php's date("w"): 0 is Sunday.
        last_by_week_day[(local.weekday() + 1) % 7] = local

    for week_day, last_start in last_by_week_day.items():
        rule = (
            ShowDays.objects.filter(show_id=show_id, week_day=week_day)
            .exclude(repeat_kind=NO_REPEAT)
            .first()
        )

        if rule is None:
            continue

        in_rule_zone = last_start.astimezone(ZoneInfo(rule.timezone))
        # A date, and the day after the last airing: last_show is exclusive.
        rule.last_show_on = (in_rule_zone + timedelta(days=1)).date()
        rule.save(update_fields=["last_show_on"])

    # A rule whose instances have all gone keeps its first date, and the day the
    # delete left nothing for is closed by ending it where it began.
    for rule in ShowDays.objects.filter(show_id=show_id).exclude(repeat_kind=NO_REPEAT):
        if rule.last_show_on is None:
            rule.last_show_on = rule.first_show_on
            rule.save(update_fields=["last_show_on"])


def _rule_timezone(rules: list[ShowDays]) -> str:
    """
    The zone to read instance times in: a repeating rule's, else the first.

    php asks in that order too. Every rule of a show carries the same zone in
    practice, since they are all written from one form field, so the order only
    decides what happens to a show whose rules disagree.
    """
    for rule in rules:
        if rule.repeat_kind != NO_REPEAT:
            return rule.timezone

    return rules[0].timezone if rules else "UTC"


def _unmark_files_no_longer_scheduled(now: datetime) -> None:
    """
    Files whose only scheduling has gone back to being free.

    ``cc_files.is_scheduled`` is what stops the library offering to move or delete
    something that is queued to air, so a delete that leaves it set would keep files
    locked that nothing will ever play again. Only files with nothing left in the
    future are cleared, which is why this is a set difference rather than a blanket
    update: a file in another show tomorrow is still scheduled.
    """
    still_scheduled = set(
        Schedule.objects.filter(ends_at__gt=now)
        .exclude(file=None)
        .values_list("file_id", flat=True)
    )

    stale = File.objects.filter(scheduled=True).exclude(pk__in=still_scheduled)

    stale.update(scheduled=False)


# -- editing -------------------------------------------------------------------

#: The repeat kinds whose instances are built by counting from the first show date.
#: Move that date and every one of them lands on the wrong night, so an edit that
#: moves it starts the show again rather than shifting it. Weekly shows are not in
#: this list: their instances are anchored to a weekday, and moving the first date
#: to another Friday does not move the Fridays.
RESTART_ON_START_DATE_CHANGE = {1, 4, 5, 2, 3}


@dataclass(frozen=True)
class _Original:
    """
    What the show was before this edit.

    ``storeOrigLocalShowInfo``: the rule the instances were built from, its timezone
    and its start time, kept apart from the payload because the payload may change
    the timezone, and because the instances have to be found by where the show
    *used* to air. A show edited across a DST boundary is where that matters: read
    the old start in the new zone and every instance shifts by an hour.
    """

    timezone_name: str
    start_hour: int
    start_minute: int
    first_show_on: date
    start_time: time
    repeat_kind: int
    week_day: int | None

    @property
    def local_start(self) -> datetime:
        """The old start, on the wall clock of the show's own zone."""
        return datetime.combine(self.first_show_on, self.start_time).replace(
            tzinfo=ZoneInfo(self.timezone_name)
        )


def _read_original(show: Show) -> _Original | None:
    """
    The rule an edit is measured against, or ``None`` if there is nothing to measure.

    A show with no rules cannot be edited rather than created; php would fatal on
    the missing row, and here it takes the create path, which is the only one that
    describes what is in the database.
    """
    rules = list(ShowDays.objects.filter(show=show).order_by("id"))

    if not rules:
        return None

    # php asks the show whether it repeats - which means: is its *first* rule a
    # repeating one - and then reads the first repeating rule, or just the first.
    if rules[0].repeat_kind != NO_REPEAT:
        rule = next((row for row in rules if row.repeat_kind != NO_REPEAT), rules[0])
    else:
        rule = rules[0]

    hour, minute, _ = (rule.start_time.strftime("%H:%M:%S").split(":") + ["0"])[:3]

    return _Original(
        timezone_name=rule.timezone,
        start_hour=int(hour),
        start_minute=int(minute),
        first_show_on=rule.first_show_on,
        start_time=rule.start_time,
        repeat_kind=rule.repeat_kind,
        week_day=rule.week_day,
    )


def _informer(original: _Original, show: Show):
    """
    The builder's look-up for "is there already an instance at this instant".

    php's ``getInstance()`` reads the candidate instant in the zone the show had
    *before* the edit, sets the clock back to the time the show used to start at,
    and converts to UTC before matching. That round trip is what lets an edit across
    a DST boundary find the row it is moving instead of creating a second one -
    the instances were written against the old wall clock, and so is the candidate.
    """

    def adopt(starts_utc: datetime) -> ShowInstance | None:
        if original.repeat_kind == NO_REPEAT:
            # A show that runs once has exactly one instance to find, and php looks
            # it up by the old start rather than by the occurrence.
            found = ShowInstance.objects.filter(
                show=show,
                starts_at=to_instant(original.local_start),
                rebroadcast=0,
            ).first()

            if found is None:
                raise ShowInstanceNotFound(
                    f"show {show.pk} has no instance at {original.local_start:%Y-%m-%d %H:%M}"
                )

            return found

        shifted = (
            to_instant(starts_utc)
            .astimezone(ZoneInfo(original.timezone_name))
            .replace(
                hour=original.start_hour,
                minute=original.start_minute,
                second=0,
                microsecond=0,
            )
            .astimezone(UTC)
        )

        return ShowInstance.objects.filter(
            show=show, starts_at=shifted, rebroadcast=0
        ).first()

    return adopt


def _cleanup_instances(
    form: ShowForm, show: Show, original: _Original, now: datetime
) -> list[int]:
    """
    Remove what the new rules no longer describe, and report which days are new.

    Port of ``delegateInstanceCleanup()``, whose own comment says "this function is
    messy, needs refactoring". It is reproduced branch by branch rather than
    tidied, because each branch exists for a case a station hit once: the order of
    the deletions decides which rows survive, and a tidier version would be a new
    design that nobody has proved equivalent.

    The returned days of the week are threaded through the legacy instance builders
    as ``$daysAdded`` and never read by them - an added day's instances are created
    because its rule is new, and an existing day's are not because the builder finds
    the instances already there. Computing the list is still what deletes the removed
    days' rows, so it is returned; nothing consumes it.
    """
    rules = list(ShowDays.objects.filter(show=show).order_by("id"))

    if not rules:
        return []

    current = (
        rules[0]
        if original.repeat_kind == NO_REPEAT
        else next((row for row in rules if row.repeat_kind != NO_REPEAT), rules[0])
    )
    repeating_rules = [row for row in rules if row.repeat_kind != NO_REPEAT]

    days_added: list[int] = []

    if form.repeats != (current.repeat_kind != NO_REPEAT):
        _delete_all_repeat_instances(current, show, now)

        if not form.repeats:
            _delete_all_rules(show)

        if form.repeats and current.week_day is not None:
            days_added.append(current.week_day)

    if form.repeats:
        local_start = _local_start(current)

        if (
            form.repeat_kind in RESTART_ON_START_DATE_CHANGE
            and form.start_date != local_start.date()
        ):
            # Counting from a different first date puts every instance in the wrong
            # place, so the show starts again from here.
            _delete_all_instances(show, now)

        if current.repeat_kind not in (NO_REPEAT, form.repeat_kind):
            _delete_all_instances(show, now)
            _delete_all_rules(show)
        elif current.repeat_kind != REPEAT_MONTHLY_MONTHLY:
            # Monthly-on-a-date rules carry no weekday, so there is nothing to
            # compare: a weekday can only have changed on a rule that has one.
            existing_days = [row.week_day for row in repeating_rules]
            days_added += _reconcile_days(
                form.days, existing_days, repeating_rules, show, now
            )

            if form.start_date != local_start.date() or form.start_time != (
                local_start.timetz().replace(tzinfo=None)
            ):
                if form.start_date > local_start.date():
                    _delete_instances_before(form.start_date, show, now)

        end = _payload_end(form)
        current_end = _repeating_end_date(show)

        if end is not None and (current_end is None or end < current_end):
            _delete_instances_from(end.astimezone(UTC).date(), show, now)

    return days_added


def _reconcile_days(
    wanted: tuple[int, ...],
    existing: list[int | None],
    rules: list[ShowDays],
    show: Show,
    now: datetime,
) -> list[int]:
    """
    Add the days that are new, drop the instances of the days that were removed.

    Returns the added weekdays, which - as above - nothing reads.
    """
    present = [day for day in existing if day is not None]

    changed = len(wanted) != len(present) or set(wanted) != set(present)

    if not changed:
        return []

    removed = [day for day in present if day not in wanted]
    added = [day for day in wanted if day not in present]

    if removed:
        _delete_removed_day_instances(removed, rules, show, now)

    return added


def _local_start(rule: ShowDays) -> datetime:
    """A rule's first date and time, on its own wall clock."""
    return datetime.combine(rule.first_show_on, rule.start_time).replace(
        tzinfo=ZoneInfo(rule.timezone)
    )


# -- the deletions an edit makes -----------------------------------------------


def _sql(statement: str, params: Mapping[str, Any]) -> None:
    connection.cursor().execute(statement, params)


def _delete_instance_rows(clause: str, params: Mapping[str, Any]) -> None:
    """
    Delete the instances a statement selects, and what is queued inside them.

    The extra statement is the port's, and it is here because ``cc_schedule`` has no
    foreign key to ``cc_show_instances`` in the shipped schema - php's own bulk
    deletes therefore leave items scheduled in hours that no longer exist, pointing
    at instance ids that no longer exist. Upstream's dataset for the scenario that
    removes a repeat day expects them gone; its test database is built from an
    installer script with the cascade production does not have, which is the same
    blind spot that hides the orphaned show days.
    """
    _sql(
        "DELETE FROM cc_schedule WHERE instance_id IN"
        f" (SELECT id FROM cc_show_instances WHERE {clause})",
        params,
    )
    _sql(f"DELETE FROM cc_show_instances WHERE {clause}", params)


def _delete_all_instances(show: Show, now: datetime) -> None:
    """Every future instance, however it was made."""
    _delete_instance_rows(
        "starts > %(timestamp)s AND show_id = %(show_id)s",
        {"timestamp": to_instant(now), "show_id": show.pk},
    )


def _delete_all_repeat_instances(rule: ShowDays, show: Show, now: datetime) -> None:
    """
    Every future instance except the one that matches this rule exactly.

    php keeps ``starts != firstShow`` when a show stops repeating: the single
    instance the show still describes is not a leftover of the repeat, and deleting
    it would leave a show that runs once with nothing to run.
    """
    _delete_instance_rows(
        "starts > %(timestamp)s AND show_id = %(show_id)s"
        " AND starts != %(first_show)s",
        {
            "timestamp": to_instant(now),
            "show_id": show.pk,
            "first_show": to_instant(_local_start(rule).astimezone(UTC)),
        },
    )


def _delete_all_rules(show: Show) -> None:
    _sql("DELETE FROM cc_show_days WHERE show_id = %(show_id)s", {"show_id": show.pk})


def _delete_instances_before(first_show_on: date, show: Show, now: datetime) -> None:
    """
    Instances before a date the show was moved to.

    Both conditions are needed: ``date(starts) <`` is the new start, and ``starts >``
    keeps the past out of it. An edit that pushes a show's first night forward does
    not delete the nights it already aired, and it could not: playout's history
    points at those rows.
    """
    _delete_instance_rows(
        "date(starts) < %(new_start)s AND starts >"
        " %(timestamp)s AND show_id = %(show_id)s",
        {"new_start": first_show_on, "timestamp": to_instant(now), "show_id": show.pk},
    )


def _delete_instances_from(last_show_on: date, show: Show, now: datetime) -> None:
    """Instances on or after a date the show was cut short at."""
    _delete_instance_rows(
        "date(starts) >= %(end_date)s AND starts >"
        " %(timestamp)s AND show_id = %(show_id)s",
        {"end_date": last_show_on, "timestamp": to_instant(now), "show_id": show.pk},
    )


def _delete_removed_day_instances(
    days_removed: list[int], rules: list[ShowDays], show: Show, now: datetime
) -> None:
    """
    The instances of weekdays somebody unticked.

    The weekday is read from the *utc* instant of the rule's first airing, not from
    the rule's own ``day`` column: a show that airs Friday 02:00 Berlin on a Thursday
    evening in UTC is deleted by its UTC weekday, which is the weekday the instances
    are actually on. php does the same conversion, and the two agree only because of
    it.
    """
    utc_week_days = []

    for rule in rules:
        if rule.week_day in days_removed:
            rule.refresh_from_db()
            in_utc = to_instant(_local_start(rule).astimezone(UTC))
            utc_week_days.append((in_utc.weekday() + 1) % 7)

    ShowDays.objects.filter(show=show, week_day__in=days_removed).delete()

    if utc_week_days:
        _delete_instance_rows(
            "EXTRACT(DOW FROM starts) IN ("
            + ",".join(str(int(day)) for day in utc_week_days)
            + ") AND starts > %(timestamp)s AND show_id = %(show_id)s",
            {"timestamp": to_instant(now), "show_id": show.pk},
        )


def _delete_future_rebroadcast_instances(show: Show, now: datetime) -> None:
    """
    The replays, all of them, to be rebuilt from the new rules.

    Rebroadcast offsets are relative to an airing and cheap to regenerate, and an
    edit may change the recording, the offsets or both; carrying the old ones over
    would mean matching each up with the airing it belongs to, which is exactly the
    arithmetic the builder is about to do again.
    """
    _delete_instance_rows(
        "starts > %(timestamp)s AND show_id = %(show_id)s AND rebroadcast = 1",
        {"timestamp": to_instant(now), "show_id": show.pk},
    )


def _delete_rebroadcast_rules(show: Show) -> None:
    ShowRebroadcast.objects.filter(show=show).delete()


def _repeating_end_date(show: Show) -> datetime | None:
    """
    The latest end any rule carries, in its own zone.

    ``getRepeatingEndDate()``: one row, ordered by ``last_show`` descending, which in
    Postgres puts the nulls - the shows that never end - first. So a show with one
    open-ended rule reports no end date at all, and that is what makes editing the
    end date of a multi-day show so blunt: the open rule wins, and "no end" means
    nothing earlier has to be deleted.
    """
    rule = ShowDays.objects.filter(show=show).order_by("-last_show_on", "id").first()

    if rule is None or rule.last_show_on is None:
        return None

    return datetime.combine(rule.last_show_on, time.min).replace(
        tzinfo=ZoneInfo(rule.timezone)
    )


def _payload_end(form: ShowForm) -> datetime | None:
    """The end the payload describes, one day past the last day it runs."""
    if form.no_end:
        return None

    end_on = form.end_date if form.repeats else form.start_date

    if end_on is None:
        return None

    return (datetime.combine(end_on, time.min) + timedelta(days=1)).replace(
        tzinfo=ZoneInfo(form.timezone_name)
    )


# -- the schedule an edit moves ------------------------------------------------


def _future_instance_ids(show: Show, now: datetime) -> list[int]:
    return list(
        ShowInstance.objects.filter(
            show=show, starts_at__gt=to_instant(now)
        ).values_list("id", flat=True)
    )


def _adjust_schedule(
    form: ShowForm,
    show: Show,
    original: _Original,
    instance_ids: list[int],
    now: datetime,
) -> None:
    """
    Move what is queued inside the surviving instances, and re-mark it.

    A file cued to start at 20:30 in a show that has just moved to 21:00 has to move
    too, or playout plays it in the wrong hour - and, unlike a calendar row, a
    scheduled item carries a cue and a fade that nothing else knows about.

    The difference is measured in the two zones the payload and the database speak:
    the new start in the zone the form names, the old one read back in the station's
    own. php does exactly that, and the seam shows only when the two differ - which
    is why it is copied rather than smoothed over with one sensible zone.
    """
    new_start = datetime.combine(form.start_date, form.start_time).replace(
        tzinfo=ZoneInfo(form.timezone_name)
    )

    if _show_is_repeating(show) and original.first_show_on != form.start_date:
        # For a repeating show the first date is an anchor, not a change: the
        # instances are on the same weekdays, and the difference between the old
        # first Friday and the new one is a week, not an edit to the airtime.
        new_start = datetime.combine(original.first_show_on, form.start_time).replace(
            tzinfo=ZoneInfo(form.timezone_name)
        )

    station_zone = ZoneInfo(_station_timezone())
    diff = new_start - to_instant(original.local_start).astimezone(station_zone)

    _shift_schedule_times(instance_ids, diff)

    for pk in _future_instance_ids(show, now):
        _update_schedule_status(ShowInstance.objects.get(pk=pk), now=now)


def _show_is_repeating(show: Show) -> bool:
    rule = ShowDays.objects.filter(show=show).order_by("id").first()

    return rule is not None and rule.repeat_kind != NO_REPEAT


def _station_timezone() -> str:
    """The station's own zone, which is what php reads the old instance in."""
    return (
        Preference.objects.filter(key="timezone")
        .values_list("value", flat=True)
        .first()
        or "UTC"
    )


def _shift_schedule_times(instance_ids: list[int], diff: timedelta) -> None:
    """
    Move every queued item of these instances by the same span.

    Row by row, as php does: the interval is applied to each item's own start and
    end, so an item spanning the hour a DST change falls in moves with its show
    rather than being re-derived from a wall clock that has shifted under it.
    """
    if not diff or not instance_ids:
        return

    for item in Schedule.objects.filter(instance_id__in=instance_ids):
        item.starts_at = to_instant(item.starts_at) + diff
        item.ends_at = to_instant(item.ends_at) + diff
        item.save(update_fields=["starts_at", "ends_at"])


def _update_schedule_status(instance: ShowInstance, *, now: datetime) -> None:
    """
    Re-mark where the queued items now sit relative to the instance's end.

    ``updateScheduleStatus()``: filled time, then each item marked as inside the
    show, straddling its end, or spilled past it. The third case is what playout
    refuses to start on, so an edit that shortens a show without re-marking leaves
    a night that will not play.
    """
    total = Schedule.objects.filter(instance=instance).aggregate(models.Sum("length"))[
        "length__sum"
    ]

    ends_at = to_instant(instance.ends_at)
    inside = {"instance_id": instance.pk, "position_status__gte": 0}

    Schedule.objects.filter(**inside, ends_at__lte=ends_at).update(position_status=1)
    Schedule.objects.filter(
        **inside, starts_at__lt=ends_at, ends_at__gt=ends_at
    ).update(position_status=2)
    Schedule.objects.filter(**inside, starts_at__gt=ends_at).update(position_status=0)

    instance.filled_time = total or timedelta(0)
    instance.last_scheduled_at = to_instant(now)
    instance.save(update_fields=["filled_time", "last_scheduled_at"])


def edit_repeating_instance(payload: Mapping[str, Any], *, now: datetime | None = None):
    """
    Take one instance out of a repeating sequence and give it its own time.

    The calendar's "edit this instance" dialog, which is a different question from
    editing the show: the show goes on repeating, and this one night stops being
    part of the pattern. php does it by deleting whatever single-instance rule was
    already attached to that night - there can be one from a previous edit, and its
    ids are not stored, so it is found by the night's original date and time -
    writing a new rule for the new time, building the one instance, and moving the
    queued items onto it.
    """
    now = now or timezone.now()

    form = ShowForm.from_legacy_payload(payload, with_colors=False)

    if form.show_id is None or form.instance_id is None:
        raise ShowFormError(
            "add_show_instance_id: editing one instance has to say which"
        )

    with transaction.atomic():
        show = Show.objects.filter(pk=form.show_id).first()

        if show is None:
            raise ShowFormError(f"add_show_id: no show {form.show_id}")

        original = ShowInstance.objects.filter(pk=form.instance_id).first()

        if original is None:
            raise ShowInstanceNotFound(form.instance_id)

        station_zone = ZoneInfo(_station_timezone())
        new_start = datetime.combine(form.start_date, form.start_time).replace(
            tzinfo=ZoneInfo(form.timezone_name)
        )
        diff = new_start - to_instant(original.starts_at).astimezone(station_zone)

        if diff:
            _shift_schedule_times([original.pk], diff)

        was = to_instant(original.starts_at).astimezone(
            ZoneInfo(_show_timezone(show) or "UTC")
        )
        ShowDays.objects.filter(
            show=show,
            repeat_kind=NO_REPEAT,
            first_show_on=was.date(),
            start_time=was.time().replace(second=0),
        ).delete()

        _save_show_days(form, show)

        rule = (
            ShowDays.objects.filter(
                show=show,
                repeat_kind=NO_REPEAT,
                first_show_on=form.start_date,
                start_time=form.start_time,
            )
            .order_by("-id")
            .first()
        )

        if rule is None:
            raise ShowFormError("add_show_start_date: no rule was written for it")

        builder = InstanceBuilder(now=to_instant(now))
        edited = builder.create_single(rule, _populate_until(now))

        if edited is not None:
            Schedule.objects.filter(instance=original).update(instance=edited)
            _update_schedule_status(edited, now=now)
            edited.description = form.instance_description or ""
            edited.save(update_fields=["description"])

        original.modified = True
        original.description = form.instance_description or ""
        original.save(update_fields=["modified", "description"])

    update_schedule()

    return edited


def _show_timezone(show: Show) -> str | None:
    rule = ShowDays.objects.filter(show=show).order_by("id").first()

    return rule.timezone if rule else None


def _populate_until(now: datetime) -> datetime:
    return PreferenceStore.shows_populated_until() or to_instant(now)


def extend_show_instances(
    show_id: int,
    until: date,
    *,
    fill_instances: bool = False,
    now: datetime | None = None,
) -> dict[int, list[int]]:
    """
    Build a show's instances further into the calendar, on request.

    The calendar's "load more": the interface asks for another stretch of weeks and
    the service builds the show's nights into them, which is ``delegateInstance-
    Creation`` with an end date past the one the station has populated to.

    ``until`` overrides ``shows_populated_until`` for this call only, and does not
    write it: a station that asked for a peek at March should not wake up believing
    its calendar is built to March, because the periodic populator would then stop
    at that date and never pass it. php keeps the two apart the same way.

    ``fill_instances`` is php's own flag: linked shows air the same content, and the
    caller that is extending a calendar - rather than populating it on a schedule -
    says so, because the fresh instances have to be given that content to air.
    """
    now = now or timezone.now()

    show = Show.objects.filter(pk=show_id).first()

    if show is None:
        raise ShowFormError(f"show_id: no show {show_id}")

    rules = list(
        ShowDays.objects.filter(show=show).exclude(repeat_kind=NO_REPEAT).order_by("id")
    ) or list(ShowDays.objects.filter(show=show).order_by("id"))

    populate_until = datetime.combine(until, time.min).replace(tzinfo=UTC)

    builder = InstanceBuilder(now=to_instant(now), is_update=True)

    for rule in rules:
        if rule.repeat_kind == NO_REPEAT:
            builder.create_single(rule, populate_until)
        else:
            builder.build([rule], populate_until=populate_until)

    if show.linked and fill_instances:
        fill_linked_instances(
            show,
            builder.new_instance_ids.get(show.pk, []),
            populated_until=populate_until,
            now=now,
        )

    update_schedule()

    return builder.new_instance_ids
