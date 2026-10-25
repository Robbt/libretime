import re
from datetime import date, timedelta

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from ..models import Show, ShowDays, ShowHost, ShowInstance, ShowRebroadcast
from ..services.show import MAX_REBROADCAST_DATES as MAX_REBROADCAST_DAYS


class ShowSerializer(serializers.ModelSerializer):
    class Meta:
        model = Show
        fields = [
            "id",
            "name",
            "description",
            "genre",
            "url",
            "image",
            "foreground_color",
            "background_color",
            "live_enabled",
            "linked",
            "linkable",
            "auto_playlist",
            "auto_playlist_enabled",
            "auto_playlist_repeat",
            "intro_playlist",
            "override_intro_playlist",
            "outro_playlist",
            "override_outro_playlist",
        ]


class ShowDaysSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShowDays
        fields = "__all__"


class ShowHostSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShowHost
        fields = "__all__"


class ShowInstanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShowInstance
        fields = "__all__"


class ShowRebroadcastSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShowRebroadcast
        fields = "__all__"


# -- creating a show ------------------------------------------------------------

WEEK_DAYS = (
    "sunday",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
)

WEEK_DAY_NAMES = {
    **{name: number for number, name in enumerate(WEEK_DAYS)},
    **{name[:3]: number for number, name in enumerate(WEEK_DAYS)},
}
"""The days a show comes back on, spelled out.

The numbers are the calendar's, where 0 is Sunday, and are accepted as they are.
The names are here because that numbering is unfamiliar enough that a client
guessing "1 is Monday" would get its show on the wrong night with a response
saying everything worked.
"""

#: How often a show returns, and the pair of numbers the calendar uses to say it:
#: the repeat type, and - when the type is "monthly", which is not yet an answer -
#: the second control that refines it.
REPEAT_KINDS = {
    "none": (0, None, None),
    "weekly": (1, 0, None),
    "biweekly": (1, 1, None),
    "triweekly": (1, 4, None),
    "quadweekly": (1, 5, None),
    "monthly": (1, 2, 2),
    "monthly_by_weekday": (1, 3, None),
}

#: The same table read backwards, for a payload in the calendar's vocabulary.
#:
#: Both ways of posting a monthly show are accepted: the calendar sends the refined
#: type straight out (3), and a client that reads the form as two questions sends 2
#: with a monthly type of 3. They mean the same show, and the legacy code agrees -
#: it only asks its second question when the first answer was "monthly".
REPEAT_NAMES = {
    (0, None, None): "none",
    (1, 0, None): "weekly",
    (1, 1, None): "biweekly",
    (1, 4, None): "triweekly",
    (1, 5, None): "quadweekly",
    (1, 2, 2): "monthly",
    (1, 2, 3): "monthly_by_weekday",
    (1, 3, None): "monthly_by_weekday",
    (1, 3, 2): "monthly_by_weekday",
}

#: One-to-one field names, this endpoint's to the calendar's.
#:
#: The rest of the mapping - repeats, days, rebroadcasts, the end date - is not a
#: rename and is done in :func:`from_legacy_payload`.
LEGACY_FIELD_NAMES = {
    "name": "add_show_name",
    "description": "add_show_description",
    "url": "add_show_url",
    "genre": "add_show_genre",
    "foreground_color": "add_show_color",
    "background_color": "add_show_background_color",
    "timezone": "add_show_timezone",
    "first_show_on": "add_show_start_date",
    "start_time": "add_show_start_time",
    "duration": "add_show_duration",
    "linked": "add_show_linked",
    "record": "add_show_record",
    "hosts": "add_show_hosts",
    "auto_playlist": "add_show_autoplaylist_id",
    "auto_playlist_repeat": "add_show_autoplaylist_repeat",
    "intro_playlist": "add_show_intro_playlist_id",
    "outro_playlist": "add_show_outro_playlist_id",
    "live_stream_enabled": "cb_airtime_auth",
    "live_stream_custom": "cb_custom_auth",
    "live_stream_user": "custom_username",
    "live_stream_password": "custom_password",
}


_DAY_LIST = ", ".join(WEEK_DAYS)


def parse_duration_text(value) -> tuple[int, int] | None:
    """
    How long a show runs, read from the ways people write it.

    ``"1:30"``, ``"01h 30m"``, ``"90m"``, ``"2h"``. Seconds have nowhere to go,
    which is the calendar's behaviour and is not worth inventing a column for.
    """
    text = str(value).strip().lower()

    if match := re.fullmatch(r"(\d{1,3}):([0-5]\d)", text):
        return int(match[1]), int(match[2])

    if match := re.fullmatch(r"(\d{1,3})\s*h(?:\s*(\d{1,2})\s*m?)?", text):
        return int(match[1]), int(match[2] or 0)

    if match := re.fullmatch(r"(\d{1,4})\s*m", text):
        return 0, int(match[1])

    return None


#: The parts of a payload that are not one field's worth of text, and what this
#: endpoint calls them. A refusal from the code that writes the show names the
#: calendar's key; the answer goes back in the words the client used.
LEGACY_DERIVED_FIELD_NAMES = {
    "add_show_repeats": "repeats",
    "add_show_repeat_type": "repeats",
    "add_show_monthly_repeat_type": "repeats",
    "add_show_day_check": "week_days",
    "add_show_no_end": "last_show_on",
    "add_show_end_date": "last_show_on",
    "add_show_has_autoplaylist": "auto_playlist",
    "add_show_rebroadcast": "rebroadcasts",
}


@extend_schema_field(
    {
        "type": "array",
        "items": {"type": "integer", "minimum": 0, "maximum": 6},
        "description": "Days of the week, 0 is Sunday. Names are accepted on the way in.",
    }
)
class WeekDaysField(serializers.Field):
    """The days of the week a repeating show comes back on, by name or number."""

    default_error_messages = {
        "not_a_list": "Expected a weekday, or a list of weekdays.",
        "unknown": "Not a weekday: {value}. Use {days} or a number from 0 to 6.",
        "empty": "A repeating show needs at least one day of the week.",
    }

    def to_internal_value(self, value):
        if isinstance(value, (str, int, float)):
            value = [value]

        if not isinstance(value, (list, tuple)):
            self.fail("not_a_list")

        days = []

        for item in value:
            if isinstance(item, str) and item.strip().lower() in WEEK_DAY_NAMES:
                days.append(WEEK_DAY_NAMES[item.strip().lower()])
            elif isinstance(item, (int, float)) and not isinstance(item, bool):
                if int(item) not in range(7):
                    self.fail("unknown", value=item, days=_DAY_LIST)

                days.append(int(item))
            else:
                self.fail("unknown", value=item, days=_DAY_LIST)

        if not days:
            self.fail("empty")

        return days

    def to_representation(self, value):
        return list(value)


@extend_schema_field(
    OpenApiTypes.STR,
)
class DurationField(serializers.Field):
    """
    How long a show runs.

    ``"1:30"`` and ``"01h 30m"`` both mean an hour and a half. Whatever the client
    writes, the value reaches the code that writes the show as the calendar would
    have written it, because the duration column is a string made out of that string
    by text surgery - so "an hour thirty" has to arrive as ``01h 30m`` to end up in
    the row as ``01:30``, the same as the same show made by the browser.
    """

    default_error_messages = {"invalid": 'Not a duration. Use "1:30" or "01h 30m".'}

    def to_internal_value(self, value):
        parsed = parse_duration_text(value)

        if parsed is None or parsed == (0, 0):
            self.fail("invalid")

        hours, minutes = parsed

        return f"{hours:02d}h {minutes:02d}m"

    def to_representation(self, value):
        return value


class RebroadcastField(serializers.Serializer):
    """
    One airing of a recording, some days after the show that made it.

    Days rather than dates, because for a show that repeats the rebroadcasts repeat
    with it, and a date would have to mean something different each time. A show
    that runs once has its rebroadcasts as dates in the calendar, and this means the
    same thing asked in the other tense: the row stores the number of days either
    way.
    """

    after_days = serializers.IntegerField(min_value=1, max_value=366)
    at = serializers.TimeField()


class ShowCreateSerializer(serializers.Serializer):
    """
    A show, as something to be put on the calendar.

    Not a row. A show is the show, the days it comes back on, and the instances
    those produce, and creating one writes all three in one transaction and then
    tells the playout service to reload. The row serializer next to this one
    describes what a show is; this describes what runs when.
    """

    name = serializers.CharField()
    description = serializers.CharField(required=False, allow_null=True)
    url = serializers.URLField(required=False, allow_null=True, allow_blank=True)
    genre = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    foreground_color = serializers.CharField(required=False, default="ffffff")
    background_color = serializers.CharField(required=False, default="364492")

    timezone = serializers.CharField()
    first_show_on = serializers.DateField()
    start_time = serializers.TimeField()
    duration = DurationField()

    repeats = serializers.ChoiceField(choices=sorted(REPEAT_KINDS), default="weekly")
    week_days = WeekDaysField(required=False, allow_null=True)

    #: The last day the show runs, counted as running that day. Left out, or null,
    #: for a show that keeps going until the station stops it.
    last_show_on = serializers.DateField(required=False, allow_null=True)

    record = serializers.BooleanField(required=False, default=False)
    rebroadcasts = RebroadcastField(many=True, required=False)

    hosts = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_null=True
    )
    linked = serializers.BooleanField(required=False, default=False)

    auto_playlist = serializers.IntegerField(required=False, allow_null=True)
    auto_playlist_repeat = serializers.BooleanField(required=False, default=False)
    intro_playlist = serializers.IntegerField(required=False, allow_null=True)
    outro_playlist = serializers.IntegerField(required=False, allow_null=True)

    live_stream_enabled = serializers.BooleanField(required=False, default=False)
    live_stream_custom = serializers.BooleanField(required=False, default=False)
    live_stream_user = serializers.CharField(required=False, allow_null=True)
    live_stream_password = serializers.CharField(
        required=False, allow_null=True, allow_blank=True
    )

    def to_internal_value(self, data):
        return super().to_internal_value(from_legacy_payload(data))

    def validate(self, show):
        if show["repeats"] != "none" and not show.get("week_days"):
            raise serializers.ValidationError(
                {
                    "week_days": "A show that repeats needs the days it repeats on. "
                    f"Use {', '.join(WEEK_DAY_NAMES)} or the numbers 0 to 6."
                }
            )

        last_show_on = show.get("last_show_on")

        if last_show_on is not None and last_show_on < show["first_show_on"]:
            raise serializers.ValidationError(
                {"last_show_on": "The show would end before it starts."}
            )

        rebroadcasts = show.get("rebroadcasts") or ()

        if len(rebroadcasts) > MAX_REBROADCAST_DAYS:
            raise serializers.ValidationError(
                {"rebroadcasts": f"A show has at most {MAX_REBROADCAST_DAYS}."}
            )

        if rebroadcasts and not show.get("record"):
            raise serializers.ValidationError(
                {"rebroadcasts": "Only a recorded show has rebroadcasts."}
            )

        return show

    def as_legacy_payload(self) -> dict:
        """
        The payload the calendar posts, built from what was validated.

        The code that writes the show reads that payload and nothing else, and the
        equivalence harness feeds a payload of exactly that shape to php. Rebuilding
        it here - rather than having this endpoint write rows in its own way - is
        what keeps a request in one vocabulary from meaning something different from
        the same request in the other.
        """
        values = self.validated_data
        repeats, repeat_type, monthly_type = REPEAT_KINDS[values["repeats"]]
        once = values["repeats"] == "none"

        payload = {
            legacy: values.get(name) for name, legacy in LEGACY_FIELD_NAMES.items()
        }
        payload.update(
            {
                "add_show_id": -1,
                "add_show_instance_id": -1,
                "add_show_repeats": repeats,
                "add_show_repeat_type": repeat_type,
                "add_show_day_check": values.get("week_days") or [],
                "add_show_no_end": values.get("last_show_on") is None,
                "add_show_end_date": values.get("last_show_on"),
                # An id is what makes an override; the calendar sends a checkbox
                # alongside it and the legacy code reads the id. Saying it here, in
                # the one place that has both, so the reader does not have to.
                "add_show_has_autoplaylist": payload["add_show_autoplaylist_id"]
                is not None,
            }
        )

        if monthly_type is not None:
            payload["add_show_monthly_repeat_type"] = monthly_type

        for index, rebroadcast in enumerate(values.get("rebroadcasts") or (), 1):
            # The calendar asks for dates when the show runs once, and for offsets
            # when it does not. Both end up as an offset in the row.
            suffix = f"_{index}"
            family = (
                "add_show_rebroadcast_date_absolute"
                if once
                else "add_show_rebroadcast_date"
            )
            time_family = (
                "add_show_rebroadcast_time_absolute"
                if once
                else "add_show_rebroadcast_time"
            )

            payload[f"{family}{suffix}"] = (
                values["first_show_on"] + timedelta(days=rebroadcast["after_days"])
                if once
                else rebroadcast["after_days"]
            )
            payload[f"{time_family}{suffix}"] = rebroadcast["at"]

        payload["add_show_rebroadcast"] = bool(values.get("rebroadcasts"))

        return payload


def from_legacy_payload(data):
    """
    Rewrite the calendar's payload as this endpoint's, leaving this endpoint's alone.

    Only the parts that are not plain renames are handled here: the two numbers that
    say how often a show repeats, the end date which the calendar spells as a flag
    plus a date, and the rebroadcasts which the calendar numbers across the page.
    """
    payload = dict(data)

    for name, legacy in LEGACY_FIELD_NAMES.items():
        if name not in payload and legacy in payload:
            payload[name] = payload[legacy]

    if "repeats" not in payload and "add_show_repeats" in payload:
        repeats = 1 if payload["add_show_repeats"] else 0
        repeat_type = _number(payload.get("add_show_repeat_type"))
        monthly = _number(payload.get("add_show_monthly_repeat_type"))

        if not repeats:
            kind = "none"
        else:
            kind = REPEAT_NAMES.get((repeats, repeat_type, monthly))

            if kind is None:
                kind = REPEAT_NAMES.get((repeats, repeat_type, None), "weekly")

        payload["repeats"] = kind

    if "week_days" not in payload and "add_show_day_check" in payload:
        payload["week_days"] = payload["add_show_day_check"]

    if "last_show_on" not in payload:
        ended = _number(payload.get("add_show_no_end"))

        payload["last_show_on"] = (
            None
            if ended or "add_show_end_date" not in payload
            else payload["add_show_end_date"]
        )

    if "rebroadcasts" not in payload:
        rebroadcasts = _legacy_rebroadcasts(payload)

        if rebroadcasts:
            payload["rebroadcasts"] = rebroadcasts

    return payload


def _legacy_rebroadcasts(payload):
    """The numbered rebroadcast fields, as a list of offsets from the show."""
    once = payload.get("repeats") == "none"
    first_show_on = payload.get("first_show_on")
    rules = []

    for index in range(1, MAX_REBROADCAST_DAYS + 1):
        suffix = f"_{index}"
        family = (
            "add_show_rebroadcast_date_absolute"
            if once
            else "add_show_rebroadcast_date"
        )

        day = payload.get(f"{family}{suffix}")
        at = payload.get(
            f"add_show_rebroadcast_time{'_absolute' if once else ''}{suffix}"
        )

        if not day or not at:
            continue

        if once:
            start = _as_date(first_show_on)
            when = _as_date(day)

            if start is None or when is None:
                continue

            day = abs((when - start).days)

        rules.append({"after_days": int(day), "at": at})

    return rules


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_date(value):
    if isinstance(value, date):
        return value

    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


__all__ = [
    "LEGACY_DERIVED_FIELD_NAMES",
    "LEGACY_FIELD_NAMES",
    "ShowCreateSerializer",
    "ShowDaysSerializer",
    "ShowHostSerializer",
    "ShowInstanceSerializer",
    "ShowRebroadcastSerializer",
    "ShowSerializer",
]


class ShowEditSerializer(ShowCreateSerializer):
    """
    The same payload as a create, for the show named in the url.

    There is no separate vocabulary for an edit, and that is deliberate: the
    difference between the two is which show is being described, and the url already
    says. Sending the whole description of a show to change one thing about it is
    how the calendar works, and it is also what makes an edit safe - the rules, the
    nights and the end date are all restated, so nothing stale is left behind.
    """

    def as_legacy_payload(self) -> dict:
        payload = super().as_legacy_payload()
        view = self.context.get("view")
        pk = getattr(view, "kwargs", {}).get("pk") if view else None

        if pk is None:
            raise serializers.ValidationError({"id": "No show was named."})

        payload["add_show_id"] = int(pk)

        return payload


class ShowInstanceEditSerializer(serializers.Serializer):
    """
    One night taken out of a repeating show and given its own time.

    The calendar's dialog for a single cell posts the whole show form; this asks for
    the four things that describe that night, because the show itself is not being
    edited - the pattern stays, and this instance leaves it.
    """

    start_time = serializers.TimeField()
    duration = DurationField()
    first_show_on = serializers.DateField(required=False)
    timezone = serializers.CharField(required=False)
    description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )

    def as_legacy_payload(self, show: Show, instance: ShowInstance) -> dict:
        """The instance dialog's payload, filled in from the night being edited."""
        started = instance.starts_at

        return {
            "add_show_id": show.pk,
            "add_show_instance_id": instance.pk,
            "add_show_name": show.name,
            "add_show_instance_description": self.validated_data.get("description")
            or "",
            # The night itself, unless the caller named another - which is how a
            # moved cell differs from a renamed one.
            "add_show_start_date": self.validated_data.get("first_show_on")
            or started.date(),
            "add_show_start_time": self.validated_data["start_time"],
            "add_show_duration": self.validated_data["duration"],
            "add_show_timezone": self.validated_data.get("timezone")
            or _show_timezone(show)
            or "UTC",
            # An instance that leaves the pattern stops repeating, and the show's own
            # rules are not part of this request.
            "add_show_repeats": 0,
            "add_show_no_end": 0,
            "add_show_linked": int(show.linked),
            "add_show_record": 0,
            "add_show_rebroadcast": 0,
            "add_show_hosts": None,
            "cb_airtime_auth": 0,
            "cb_custom_auth": 0,
        }


class ExtendShowSerializer(serializers.Serializer):
    """How far into the calendar to build a show's nights."""

    until = serializers.DateField()


def _show_timezone(show: Show) -> str | None:
    rule = ShowDays.objects.filter(show=show).order_by("id").first()

    return rule.timezone if rule else None
