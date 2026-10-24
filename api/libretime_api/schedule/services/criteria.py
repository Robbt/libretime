"""
Smart block criteria, compiled into a queryset.

Port of the query construction in ``Application_Model_Block::getListofFilesMeetCriteria()``
(``legacy/application/models/Block.php``), together with the stored-criteria
reading of ``Application_Model_Block::getCriteria()`` and the limit parsing of
the same method.  Choosing *which* files a smart block contributes - the fill
algorithm, repeats, crossfades and the schedule rows - is deliberately not here:
this module answers one question, which files match, in a way that can be
checked against the legacy service file by file.

The rules that make this more than a dictionary lookup come from the way the
legacy form stores rows in ``cc_blockcriteria``, and are reproduced literally:

*   One row per criterion, grouped by ``criteriagroup``.  Rows in the same group
    are OR-ed, different groups of the same field are AND-ed, and different
    fields are AND-ed: ``(a OR b) AND (c OR d)``.  Letting the query chain them
    left to right would give ``((a OR b) AND c) OR d``, which is what the legacy
    code takes care to avoid.
*   The first row of a field that negates with ``!=`` or ``NOT ILIKE`` gets an
    ``OR col IS NULL`` - a file whose metadata is missing is not *different*
    from the value, it simply does not have one, and users expect it back.
*   ``is`` on ``length``, ``cuein`` and ``cueout`` becomes a text prefix match,
    because the user only sees a rounded interval.
*   ``bit_rate`` and ``sample_rate`` are stored in bits, entered in kbit.
*   ``before`` also matches files with no date at all, through
    ``COALESCE(col, DATE '-infinity')`` - negative infinity is before every
    date. ``after`` and ``between`` do not: the same coalesced value fails their
    comparison, and the equivalence harness checks exactly that.
*   ``owner_id`` compares the *login* of the owner, and ``filepath`` matches on
    the file name rather than the whole path.
*   Date values are relative to the station timezone (``general.timezone``, UTC
    unless configured) and are commonly stored as ``"7 days ago"``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from django.db.models import Expression, F, Func, Q, QuerySet, TextField, Value
from django.db.models.functions import Cast, Random

from ...storage.models import File
from ..models import SmartBlock, SmartBlockCriteria

logger = logging.getLogger(__name__)

UTC = timezone.utc

# cc_blockcriteria rows that are settings rather than conditions on a file.
LIMIT = "limit"
SORT = "sort"
REPEAT_TRACKS = "repeat_tracks"
OVERFLOW_TRACKS = "overflow_tracks"

# PHP DateTime::format letters the stored values may contain, for the now{...}
# placeholder.  Only the letters the legacy builder can produce are supported;
# they render without the locale dependent padding tricks, matching glibc.
PHP_DATE_FORMATS = {
    "d": "%d",  # day of month, leading zero
    "j": "%-d",  # day of month
    "m": "%m",  # month, leading zero
    "n": "%-m",  # month
    "Y": "%Y",  # four digit year
    "y": "%y",  # two digit year
    "H": "%H",  # 24h hour, leading zero
    "h": "%I",  # 12h hour, leading zero
    "i": "%M",  # minute
    "s": "%S",  # second
    "A": "%p",  # AM/PM
    "a": "%p",  # am/pm, PHP lowercases, %p is locale dependent
}

RELATIVE_DATE = re.compile(
    r"^\s*(?P<count>\d+)\s+(?P<unit>second|minute|hour|day|week|month|year)s?\s+ago\s*$",
    re.IGNORECASE,
)

MONTHS = {
    "second": timedelta(seconds=1),
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
}


class ModifierType(str, Enum):
    """Kind of a criteria field, as the legacy ``ModifierType`` knows them."""

    STRING = "s"
    NUMBER = "n"
    DATE = "d"
    TRACK_TYPE = "tt"


class UnsupportedCriteria(Exception):
    """
    A stored criterion this port cannot evaluate the way PHP would.

    Refusing is better than guessing: a smart block that quietly matched a
    different set of files than the legacy service would have chosen changes
    what a station plays without anybody noticing.
    """


@dataclass(frozen=True)
class Criterion:
    """One row of ``cc_blockcriteria`` that is a condition on a file."""

    key: str
    type: ModifierType
    # Django lookup base: a field of storage.File, or an annotation alias.
    lookup: str
    # Column name the legacy code used, only kept for messages.
    column: str = ""
    # Annotation needed instead of a plain field, e.g. the file name of a path.
    annotation: Expression | None = None
    # The interval columns, whose "is" becomes a text prefix match.
    is_interval: bool = False
    # bit_rate and sample_rate are entered in kbit and stored in bit.
    kilo: bool = False


def _file_name_of_path() -> Expression:
    """
    ``reverse(split_part(reverse(filepath), '/', 1))``, as PostgreSQL spells it.

    The legacy code writes that expression rather than a basename function
    because PostgreSQL has none, and matching the whole path instead would make
    ``filepath starts with "jazz"`` stop matching.
    """
    return Func(
        Func(
            Func("filepath", function="reverse"),
            Value("/"),
            Value(1),
            function="split_part",
        ),
        function="reverse",
        output_field=TextField(),
    )


# Criteria key -> what it means in Django terms. Mirrors BlockCriteria::allCriteria().
CRITERIA: dict[str, Criterion] = {
    c.key: c
    for c in (
        Criterion("album_title", ModifierType.STRING, "album_title", "album_title"),
        Criterion("artist_name", ModifierType.STRING, "artist_name", "artist_name"),
        Criterion("bit_rate", ModifierType.NUMBER, "bit_rate", "bit_rate", kilo=True),
        Criterion("bpm", ModifierType.NUMBER, "bpm", "bpm"),
        Criterion("composer", ModifierType.STRING, "composer", "composer"),
        Criterion("conductor", ModifierType.STRING, "conductor", "conductor"),
        Criterion("copyright", ModifierType.STRING, "copyright", "copyright"),
        Criterion("cuein", ModifierType.NUMBER, "cue_in", "cuein", is_interval=True),
        Criterion("cueout", ModifierType.NUMBER, "cue_out", "cueout", is_interval=True),
        Criterion("description", ModifierType.STRING, "description", "description"),
        Criterion("encoded_by", ModifierType.STRING, "encoded_by", "encoded_by"),
        Criterion("utime", ModifierType.DATE, "created_at", "utime"),
        Criterion("mtime", ModifierType.DATE, "updated_at", "mtime"),
        Criterion("lptime", ModifierType.DATE, "last_played_at", "lptime"),
        Criterion("genre", ModifierType.STRING, "genre", "genre"),
        Criterion("info_url", ModifierType.STRING, "info_url", "info_url"),
        Criterion("isrc_number", ModifierType.STRING, "isrc", "isrc_number"),
        Criterion("label", ModifierType.STRING, "label", "label"),
        Criterion("language", ModifierType.STRING, "language", "language"),
        Criterion("length", ModifierType.NUMBER, "length", "length", is_interval=True),
        Criterion("mime", ModifierType.STRING, "mime", "mime"),
        Criterion("mood", ModifierType.STRING, "mood", "mood"),
        # The legacy query joins cc_subjs and compares the login, not the id.
        Criterion("owner_id", ModifierType.STRING, "owner__username", "subj.login"),
        Criterion("replay_gain", ModifierType.NUMBER, "replay_gain", "replay_gain"),
        Criterion(
            "sample_rate",
            ModifierType.NUMBER,
            "sample_rate",
            "sample_rate",
            kilo=True,
        ),
        Criterion("track_title", ModifierType.STRING, "track_title", "track_title"),
        Criterion("track_number", ModifierType.NUMBER, "track_number", "track_number"),
        Criterion("year", ModifierType.NUMBER, "date", "year"),
        # cc_files.track_type_id is exposed as the library relation by the model.
        Criterion(
            "track_type_id", ModifierType.TRACK_TYPE, "library_id", "track_type_id"
        ),
        Criterion(
            "filepath",
            ModifierType.STRING,
            "file_name",
            "filepath",
            annotation=_file_name_of_path(),
        ),
    )
}

# Sort options of the smart block form, in the wording the UI uses.
# Sort options of the smart block form, in the wording the UI uses.
SORT_OPTIONS = (
    "newest",
    "oldest",
    "mostrecentplay",
    "leastrecentplay",
    "random",
)

DEFAULT_SORT = "random"


@dataclass
class BlockLimit:
    """
    How much of a smart block to use, as the legacy limit criteria means it.

    Attributes:
        time_seconds: Fill window in seconds.  A limit in items still has one,
            the legacy code uses a day, because it stops adding tracks once the
            window is over as well.
        items: Item limit, ``None`` when the block is limited by time.
        repeat_tracks: Reuse matched files to fill the window.
        overflow_tracks: Add a file that runs past the end of the window.
        sort: Sort option of the block.
    """

    time_seconds: int = 24 * 60 * 60
    items: int | None = None
    repeat_tracks: bool = False
    overflow_tracks: bool = False
    sort: str = DEFAULT_SORT

    @property
    def by_items(self) -> bool:
        return self.items is not None


@dataclass
class StoredCriteria:
    """
    The criteria rows of one block, in the shape the legacy code worked with.

    ``fields`` maps a criteria key to its rows, ordered by group, which is how
    ``getCriteria()`` returns them; ``settings`` holds the limit, sort and the
    two repeat/overflow switches.
    """

    fields: dict[str, list[SmartBlockCriteria]] = field(default_factory=dict)
    settings: dict[str, SmartBlockCriteria] = field(default_factory=dict)

    @classmethod
    def load(cls, block: SmartBlock) -> StoredCriteria:
        stored = cls()

        # orderByDbCriteria(): rows arrive ordered by the criteria name, which
        # keeps the generated SQL stable between runs.
        rows = SmartBlockCriteria.objects.filter(block=block).order_by(
            "criteria", "group", "pk"
        )

        for row in rows:
            if row.criteria in (LIMIT, SORT, REPEAT_TRACKS, OVERFLOW_TRACKS):
                stored.settings[row.criteria] = row
            elif row.criteria not in CRITERIA:
                raise UnsupportedCriteria(
                    f"unknown smart block criteria {row.criteria!r} on block {block.pk}"
                )
            else:
                stored.fields.setdefault(row.criteria, []).append(row)

        return stored

    def limit(self, show_limit: int | None = None) -> BlockLimit:
        """Port of the limit construction of ``getListofFilesMeetCriteria()``."""
        limit = BlockLimit()

        row = self.settings.get(LIMIT)
        if row is not None:
            modifier, value = row.condition, row.value
            if modifier == "items":
                # Still a day of window: the fill loop stops on whichever of the
                # two comes first, exactly like the legacy one.
                limit.time_seconds = 1440 * 60
                limit.items = int(float(value))
            elif modifier == "remaining":
                # Only a show instance knows what is remaining of itself.
                limit.time_seconds = show_limit if show_limit is not None else 60 * 60
                limit.items = None
            elif modifier == "hours":
                limit.time_seconds = int(float(value) * 60 * 60)
                limit.items = None
            else:
                limit.time_seconds = int(float(value) * 60)
                limit.items = None

        repeat = self.settings.get(REPEAT_TRACKS)
        if repeat is not None:
            limit.repeat_tracks = repeat.value == "1"

        overflow = self.settings.get(OVERFLOW_TRACKS)
        if overflow is not None:
            limit.overflow_tracks = overflow.value == "1"

        sort = self.settings.get(SORT)
        if sort is not None:
            limit.sort = sort.value

        return limit


def php_format(pattern: str, moment: datetime) -> str:
    """
    Render ``moment`` with a handful of PHP ``DateTime::format`` letters.

    Used by the ``now{...}`` placeholder a stored value may contain, which the
    legacy code substitutes with ``preg_replace_callback``.
    """
    out: list[str] = []
    escaped = False

    for char in pattern:
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char in PHP_DATE_FORMATS:
            out.append(moment.strftime(PHP_DATE_FORMATS[char]))
        else:
            out.append(char)

    return "".join(out)


def resolve_now(value: str, *, now: datetime, zone: Any) -> str:
    """
    Replace ``now{<php format>}`` occurrences, as ``resolveDate()`` does.

    ``now`` is the show start time when the block is filled inside a show, and
    the current instant otherwise, always shown in the station timezone.
    """
    if not isinstance(value, str) or "now{" not in value:
        return value

    moment = now.astimezone(zone)

    return re.sub(
        r"now\{(.*?)\}",
        lambda match: php_format(match.group(1), moment),
        value,
    )


def parse_date(value: str, *, now: datetime) -> datetime:
    """
    Read a stored date value: an absolute timestamp or the ``"N days ago"`` form.

    ``storeCriteriaIntoDb()`` converts absolute dates to UTC when saving them
    and stores relative ones as ``"<n> <unit> ago"``, which PHP then hands to
    ``DateTime``, in the default timezone of the service - UTC in every
    LibreTime image.
    """
    text = value.strip()

    match = RELATIVE_DATE.match(text)
    if match:
        count = int(match["count"])
        unit = match["unit"].lower()

        if unit in MONTHS:
            return now - MONTHS[unit] * count

        # Months and years are calendar arithmetic, and an impossible day of
        # month overflows into the following month instead of clamping, exactly
        # like PHP: the 31st of March minus one month is the 3rd of March, and
        # the 31st of May minus one month is the 1st of May.
        months = count * (12 if unit == "year" else 1)
        return shift_months(now, -months)

    parsed = _parse_timestamp(text)
    if parsed is None:
        raise UnsupportedCriteria(f"unparsable smart block date value {value!r}")

    return parsed


def _parse_timestamp(text: str) -> datetime | None:
    for candidate in (text, text.replace("T", " ")):
        try:
            moment = datetime.fromisoformat(candidate)
        except ValueError:
            continue

        return moment if moment.tzinfo else moment.replace(tzinfo=UTC)

    return None


def shift_months(moment: datetime, months: int) -> datetime:
    """
    ``moment`` moved by whole calendar months, overflowing like PHP does.

    Verified against the legacy service: 2026-03-31 minus one month answers
    2026-03-03, and 2026-05-31 minus one month answers 2026-05-01.
    """
    total = (moment.month - 1) + months
    year = moment.year + total // 12
    month = total % 12 + 1

    start = moment.replace(year=year, month=month, day=1)

    return start + timedelta(days=moment.day - 1)


def _scale_kilo(value: str) -> float | int:
    """192 kbit is stored as 192000, so the entered number has to follow."""
    number = float(value)

    return int(number * 1000) if number.is_integer() else number * 1000


def _coerce(value: str) -> Any:
    text = value.strip()

    try:
        return int(text)
    except ValueError:
        pass

    try:
        return float(text)
    except ValueError:
        return value


def build_condition(
    criterion: Criterion,
    modifier: str,
    value: str,
    extra: str | None,
    *,
    now: datetime,
    zone: Any,
    is_first_row: bool,
    annotations: dict[str, Expression],
) -> Q:
    """
    One stored row into a ``Q``, following the order the legacy code applies.

    ``annotations`` collects the expressions a condition needs that are not
    plain fields, so the caller can attach them to the queryset before
    filtering - the file name of a path, or the text of an interval.
    """
    base = criterion.lookup

    if criterion.annotation is not None:
        annotations[base] = criterion.annotation

    # Intervals compared for equality are matched as the rounded text the user
    # sees: 4:02.7 in the form, 0:04:02.761625 in the column.
    if criterion.is_interval and modifier == "is":
        alias = f"{base}_text"
        annotations[alias] = Cast(base, output_field=TextField())
        modifier = "starts with"

    text = resolve_now(value, now=now, zone=zone)

    prepared: Any = text
    prepared_extra: Any = extra

    if criterion.kilo:
        prepared = _scale_kilo(text)
        if extra not in (None, ""):
            prepared_extra = _scale_kilo(str(extra))

    if modifier in ("starts with", "ends with", "contains", "does not contain"):
        lookup = {
            "starts with": "istartswith",
            "ends with": "iendswith",
            "contains": "icontains",
            "does not contain": "icontains",
        }[modifier]

        positive = Q(**{f"{base}__{lookup}": prepared})

        if modifier != "does not contain":
            return positive

        return _negate(base, positive, keeps_nulls=is_first_row)

    if modifier in ("is", "is not"):
        if criterion.type is ModifierType.TRACK_TYPE:
            prepared = _track_type_id(prepared)

        positive = Q(**{f"{base}__exact": prepared})

        if modifier != "is not":
            return positive

        return _negate(base, positive, keeps_nulls=is_first_row)

    if modifier == "is greater than":
        return Q(**{f"{base}__gt": _number(prepared)})

    if modifier == "is less than":
        return Q(**{f"{base}__lt": _number(prepared)})

    if modifier == "is in the range":
        if prepared_extra in (None, ""):
            raise UnsupportedCriteria(
                f"{modifier} on {criterion.key} without an upper bound"
            )

        if criterion.type is ModifierType.DATE:
            # Absolute UTC timestamps, as storeCriteriaIntoDb() saves them: the
            # relative "N days ago" form is only produced for before, after and
            # between - and unlike those, this one drops files with no date,
            # because the legacy SQL does not coalesce here.
            lower = parse_date(str(prepared), now=now)
            upper = parse_date(str(prepared_extra), now=now)
        else:
            lower = _coerce(str(prepared))
            upper = _coerce(str(prepared_extra))

        return Q(**{f"{base}__range": (lower, upper)})

    # before, after and between keep the files that have no date at all, which
    # is what COALESCE(col, DATE '-infinity') does in the legacy SQL.
    #
    # It only works out that way for "before": negative infinity is before every
    # date, so a file that never aired is "played before 2020". For "after" and
    # "between" the same coalesced value fails the comparison and the file is
    # left out, which is what the legacy service does and what the equivalence
    # harness checks block by block.
    if modifier in ("before", "after", "between"):
        moment = parse_date(text, now=now)

        if modifier == "before":
            return Q(**{f"{base}__lte": moment}) | Q(**{f"{base}__isnull": True})

        if modifier == "after":
            return Q(**{f"{base}__gte": moment})

        if prepared_extra in (None, ""):
            raise UnsupportedCriteria(
                f"{modifier} on {criterion.key} without an end date"
            )

        return Q(
            **{
                f"{base}__range": (
                    moment,
                    parse_date(str(prepared_extra), now=now),
                )
            }
        )

    raise UnsupportedCriteria(f"unsupported smart block modifier {modifier!r}")


def _negate(base: str, positive: Q, *, keeps_nulls: bool) -> Q:
    """
    A negation that behaves like ``NOT (col <op> value)`` in SQL.

    Django renders it as ``NOT (col <op> value AND col IS NOT NULL)``, which
    keeps files whose column is null; in PostgreSQL the plain negation evaluates
    to null for those rows and drops them, and dropping them is what the legacy
    query does.

    ``keeps_nulls`` adds them back explicitly. That is the alternative the legacy
    code ORs in for the first negating row of a field: "genre is not ambient"
    should keep a track with no genre at all, because it is not *something other
    than* ambient, it simply has no genre.
    """
    condition = Q(**{f"{base}__isnull": False}) & ~positive

    if keeps_nulls:
        condition |= Q(**{f"{base}__isnull": True})

    return condition


def _number(value: Any) -> Any:
    return _coerce(str(value))


def _track_type_id(value: Any) -> int:
    try:
        return int(str(value).strip())
    except ValueError as exception:
        raise UnsupportedCriteria(
            f"track type criteria value {value!r} is not a track type id"
        ) from exception


def build_filter(
    stored: StoredCriteria,
    *,
    now: datetime | None = None,
    zone: Any = UTC,
) -> tuple[Q, dict[str, Expression]]:
    """
    Compile the criteria of a block into a ``Q`` and the annotations it needs.

    Groups are AND-ed across fields and within a field, OR-ed inside a group -
    the same shape the legacy code builds out of Propel criteria objects,
    including the way it starts a new group whenever the group number changes.
    """
    moment = now or datetime.now(UTC)
    annotations: dict[str, Expression] = {}

    result = Q()

    for key, rows in stored.fields.items():
        criterion = CRITERIA[key]

        # usort on criteria_group: stable for equal groups, since the rows are
        # already ordered by their primary key.
        ordered = sorted(
            rows, key=lambda row: row.group if row.group is not None else 0
        )

        groups: list[Q] = []
        group_filter: Q | None = None
        previous_group = None

        for index, row in enumerate(ordered):
            condition = build_condition(
                criterion,
                row.condition,
                row.value,
                row.extra,
                now=moment,
                zone=zone,
                is_first_row=index == 0,
                annotations=annotations,
            )

            group = row.group if row.group is not None else 0

            if index > 0 and group == previous_group and group_filter is not None:
                # Same group: another alternative of it.
                group_filter |= condition
            else:
                # A new group, which is AND-ed with the ones before it.
                if group_filter is not None:
                    groups.append(group_filter)

                group_filter = condition

            previous_group = group

        if group_filter is not None:
            groups.append(group_filter)

        # Q objects do not mutate, so the groups are collected first and only
        # then combined - the legacy code could extend a group after attaching
        # it, because Propel criteria are changed in place.
        field_filter = groups[0]
        for following in groups[1:]:
            field_filter &= following

        result &= field_filter

    return result, annotations


def files(
    block: SmartBlock,
    *,
    now: datetime | None = None,
    zone: Any = UTC,
    apply_sort: bool = True,
) -> QuerySet[File]:
    """
    Files matching a smart block, in the order its sort option asks for.

    The two filters every smart block carries are here as well: a file has to
    exist on disk and must not be hidden from the library.
    """
    stored = StoredCriteria.load(block)
    condition, annotations = build_filter(stored, now=now, zone=zone)

    queryset = File.objects.filter(exists=True, hidden=False)

    for alias, expression in annotations.items():
        queryset = queryset.annotate(**{alias: expression})

    queryset = queryset.filter(condition)

    if apply_sort:
        queryset = order(queryset, stored.limit().sort)

    return queryset


def order(queryset: QuerySet[File], sort: str) -> QuerySet[File]:
    """
    Apply one of the sort options of the smart block form.

    The null handling of the last-played sorts is written out because the legacy
    code writes it out: a file that never aired has no ``lptime``, and "most
    recently played" should not lead with those.
    """
    if sort == "newest":
        return queryset.order_by("-created_at")

    if sort == "oldest":
        return queryset.order_by("created_at")

    if sort == "mostrecentplay":
        return queryset.order_by(F("last_played_at").desc(nulls_last=True), "filepath")

    if sort == "leastrecentplay":
        return queryset.order_by(F("last_played_at").asc(nulls_first=True), "filepath")

    if sort == "random":
        return queryset.order_by(Random())

    logger.warning("unimplemented smart block sort %r, leaving it unordered", sort)

    return queryset


def file_ids(block: SmartBlock, **kwargs: Any) -> list[int]:
    """Ids of the matching files, in the order the block sorts them."""
    return list(files(block, **kwargs).values_list("id", flat=True))


def limit_of(block: SmartBlock, show_limit: int | None = None) -> BlockLimit:
    """The fill window and switches configured on a block."""
    return StoredCriteria.load(block).limit(show_limit)
