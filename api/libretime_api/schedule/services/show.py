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

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone

from ...notify.playout import update_schedule
from ..models import Show, ShowDays, ShowHost, ShowRebroadcast
from .populate import NO_REPEAT, InstanceBuilder, PreferenceStore, to_instant

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
    color: str
    background_color: str

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

    # -- parsing --------------------------------------------------------------

    @classmethod
    def from_legacy_payload(cls, data: Mapping[str, Any]) -> "ShowForm":
        """
        Read the payload the show dialog posts.

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
            name=_required_text(data.get("add_show_name"), "add_show_name"),
            description=_text(data.get("add_show_description")),
            url=_text(data.get("add_show_url")),
            genre=_text(data.get("add_show_genre")),
            color=_required_text(data.get("add_show_color"), "add_show_color"),
            background_color=_required_text(
                data.get("add_show_background_color"), "add_show_background_color"
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
        is_update: Whether the show already exists. The reconciliation of existing
            instances only happens on an update, like the legacy service.
        now: Instant of the request, for the "do not schedule into the past"
            checks. Injectable so the tests and the harness are not at the mercy of
            the clock.

    Raises:
        ShowFormError: a field could not be read as the thing it names.
    """
    now = now or timezone.now()

    with transaction.atomic():
        show = _save_show(form, is_update)

        # On an update the legacy service removes the instances the new rules no
        # longer cover before writing the new rules, because the cleanup reads the
        # original show day to know what changed. That is the next piece of the
        # port; until it exists, an update here would silently leave stale
        # instances behind, which is worse than refusing.
        if is_update:
            raise NotImplementedError(
                "editing an existing show is not ported yet; see "
                "tools/show-editor-equivalence for what php leaves behind"
            )

        _save_show_days(form, show)
        _save_rebroadcasts(form, show)
        _save_hosts(form, show)

        created = _create_instances(show, now=now)

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


def _save_show_days(form: ShowForm, show: Show) -> None:
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
        )


def _store_day(
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


def _save_hosts(form: ShowForm, show: Show) -> None:
    """The ``cc_show_hosts`` rows. An empty list leaves the show unhosted."""
    for host_id in form.hosts:
        ShowHost.objects.create(show=show, user_id=host_id)


# -- instances -----------------------------------------------------------------


def _create_instances(show: Show, *, now: datetime) -> dict[int, list[int]]:
    """
    Create the instances the populated window calls for.

    Port of ``delegateInstanceCreation()`` for the save path: every rule of this
    show, including a non repeating one, up to the date the calendar has been
    populated to. The populate task uses the same builder for the rules that have
    run out of instances; the difference is only in which show days are selected.
    """
    populate_until = PreferenceStore.shows_populated_until() or to_instant(now)

    builder = InstanceBuilder(now=to_instant(now))

    for day in ShowDays.objects.filter(show=show).order_by("id"):
        if day.repeat_kind == NO_REPEAT:
            builder.create_single(day, populate_until)
        else:
            builder.build([day], populate_until=populate_until)

    if show.linked and show.auto_playlist_enabled:
        # A linked show copies its content into its new instances. Only on an
        # update does the legacy service do this for the show just saved; a new
        # show has no content to copy yet, so nothing happens here today.
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
