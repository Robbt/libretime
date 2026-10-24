"""
Smart block criteria, compiled and matched against real rows.

Every expectation here is what the legacy ``Block::getListofFilesMeetCriteria()``
does, including the parts that look accidental: negations that keep files with
no metadata, ``is`` on an interval matched as rounded text, dates in the past
that still include files which never aired, bit rates entered in kbit.
``tools/criteria-equivalence/`` checks the same statements against the running
legacy service; these tests are what CI keeps.
"""

from datetime import datetime, timedelta, timezone

import pytest
from model_bakery import baker

from ....core.models import User
from ....storage.models import File, Library
from ...models import SmartBlock, SmartBlockCriteria
from ...services.criteria import (
    BlockLimit,
    StoredCriteria,
    UnsupportedCriteria,
    file_ids,
    limit_of,
    parse_date,
    resolve_now,
    shift_months,
)

UTC = timezone.utc

# Fixed clock for every relative date in this module.
NOW = datetime(2026, 3, 31, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _database(db):
    """Every test here reads and writes real rows."""
    yield


def make_file(name: str = "track", **fields) -> File:
    """
    A file that exists on disk and is visible in the library.

    Both are the defaults a smart block filters on, so a fixture that left them
    unset would match nothing and look like a broken compiler.
    """
    fields.setdefault("exists", True)
    fields.setdefault("hidden", False)
    fields.setdefault("mime", "audio/mpeg")
    fields.setdefault("name", name)
    fields.setdefault("filepath", f"/media/{name}.mp3")

    return File.objects.create(**fields)


def make_block(
    *criteria,
    sort: str = "newest",
    limit: str | None = None,
    limit_modifier: str = "hours",
    repeat_tracks: int = 0,
    overflow_tracks: int = 0,
) -> SmartBlock:
    """
    A block with the given criteria rows.

    ``criteria`` items are ``(field, modifier, value)`` or
    ``(field, modifier, value, extra, group)`` tuples, mirroring the columns of
    cc_blockcriteria. The sort, limit and the two switches are always stored,
    like ``storeCriteriaIntoDb()`` does.
    """
    block = SmartBlock.objects.create(name="test block")

    for row in criteria:
        field, modifier, value = row[0], row[1], row[2]
        extra = row[3] if len(row) > 3 else None
        group = row[4] if len(row) > 4 else 0

        SmartBlockCriteria.objects.create(
            block=block,
            criteria=field,
            condition=modifier,
            value=value,
            extra=extra,
            group=group,
        )

    SmartBlockCriteria.objects.create(
        block=block, criteria="sort", condition="N/A", value=sort
    )
    SmartBlockCriteria.objects.create(
        block=block,
        criteria="limit",
        condition=limit_modifier,
        value=limit if limit is not None else "1",
    )
    SmartBlockCriteria.objects.create(
        block=block, criteria="repeat_tracks", condition="N/A", value=str(repeat_tracks)
    )
    SmartBlockCriteria.objects.create(
        block=block,
        criteria="overflow_tracks",
        condition="N/A",
        value=str(overflow_tracks),
    )

    return block


def matching(block: SmartBlock) -> list[int]:
    return file_ids(block, now=NOW)


# ---------------------------------------------------------------------------
# string modifiers
# ---------------------------------------------------------------------------


def test_contains_matches_substring_case_insensitively() -> None:
    ambient = make_file("a", genre="Ambient")
    other = make_file("b", genre="Techno")

    block = make_block(("genre", "contains", "amb"))

    assert matching(block) == [ambient.id]
    assert other.id not in matching(block)


def test_does_not_contain_keeps_files_without_that_metadata() -> None:
    """NOT ILIKE on a null column is null, so the legacy code asks for it too."""
    techno = make_file("a", genre="Techno")
    no_genre = make_file("b", genre=None)
    ambient = make_file("c", genre="Ambient")

    block = make_block(("genre", "does not contain", "amb"))

    assert sorted(matching(block)) == sorted([techno.id, no_genre.id])
    assert ambient.id not in matching(block)


def test_is_not_keeps_files_without_that_metadata() -> None:
    no_label = make_file("a", label=None)
    other = make_file("b", label="Moot")
    nada = make_file("c", label="Nada")

    block = make_block(("label", "is not", "Nada"))

    assert sorted(matching(block)) == sorted([no_label.id, other.id])
    assert nada.id not in matching(block)


def test_two_negations_of_the_same_field_do_not_both_keep_nulls() -> None:
    """
    The IS NULL alternative is added to the first row of a field only.

    Two negations in one group are ``(a != x OR col IS NULL) AND (a != y)``, so
    a file without the field is dropped by the second group - which is what the
    legacy chaining of Propel criteria objects produces.
    """
    no_label = make_file("a", label=None)
    make_file("b", label="Nada")

    first = make_block(("label", "is not", "Nada", None, 0))
    both = make_block(
        ("label", "is not", "Nada", None, 0),
        ("label", "is not", "Moot", None, 1),
    )

    assert no_label.id in matching(first)
    assert no_label.id not in matching(both)


def test_is_is_exact_and_case_sensitive() -> None:
    exact = make_file("a", mood="Calm")
    make_file("b", mood="calm")

    block = make_block(("mood", "is", "Calm"))

    assert matching(block) == [exact.id]


def test_starts_with_and_ends_with() -> None:
    start = make_file("a", track_title="Morning Show")
    end = make_file("b", track_title="Late Night")

    assert matching(make_block(("track_title", "starts with", "morn"))) == [start.id]
    assert matching(make_block(("track_title", "ends with", "night"))) == [end.id]


def test_percent_in_a_value_is_not_a_wildcard() -> None:
    """The legacy code escapes % by hand; this escapes it the same way."""
    make_file("a", description="top 100")
    percent = make_file("b", description="100% pure")

    block = make_block(("description", "contains", "100%"))

    assert matching(block) == [percent.id]


# ---------------------------------------------------------------------------
# numbers, intervals and scaling
# ---------------------------------------------------------------------------


def test_bit_rate_and_sample_rate_are_entered_in_kbit() -> None:
    high = make_file("a", bit_rate=320000, sample_rate=44100)
    make_file("b", bit_rate=128000, sample_rate=22050)

    assert matching(make_block(("bit_rate", "is greater than", "200"))) == [high.id]
    assert matching(make_block(("bit_rate", "is", "320"))) == [high.id]
    assert matching(make_block(("sample_rate", "is in the range", "40", "50"))) == [
        high.id
    ]


def test_is_on_an_interval_matches_the_rendered_text() -> None:
    """
    ``length is 00:04:02`` matches a 4:02.761625 track.

    The legacy code turns the modifier into a prefix match on the text of the
    interval, because the user only ever sees the rounded value.
    """
    four_minutes = make_file(
        "a", length=timedelta(minutes=4, seconds=2, milliseconds=761625 / 1000)
    )
    make_file("b", length=timedelta(minutes=5))

    block = make_block(("length", "is", "00:04:02"))

    assert matching(block) == [four_minutes.id]


def test_intervals_compare_as_durations_for_other_modifiers() -> None:
    short = make_file("a", length=timedelta(minutes=2))
    make_file("b", length=timedelta(minutes=30))

    assert matching(make_block(("length", "is less than", "00:10:00"))) == [short.id]


def test_cue_in_uses_the_same_text_rule() -> None:
    cued = make_file("a", cue_in=timedelta(seconds=1, milliseconds=500))

    assert matching(make_block(("cuein", "is", "00:00:01"))) == [cued.id]


def test_year_is_compared_as_stored_text() -> None:
    nineties = make_file("a", date="1994")
    make_file("b", date="2021")

    assert matching(make_block(("year", "is in the range", "1990", "1999"))) == [
        nineties.id
    ]


# ---------------------------------------------------------------------------
# relations
# ---------------------------------------------------------------------------


def test_owner_id_matches_the_login_not_the_id() -> None:
    dj = baker.make(User, username="dj-one")
    other = baker.make(User, username="announcer")

    mine = make_file("a", owner=dj)
    make_file("b", owner=other)
    make_file("c", owner=None)

    assert matching(make_block(("owner_id", "contains", "dj"))) == [mine.id]
    assert matching(make_block(("owner_id", "is", "announcer"))) != [mine.id]


def test_track_type_is_the_library_id() -> None:
    music = baker.make(Library, name="Music", description="")
    sweeps = baker.make(Library, name="Sweeps", description="")

    track = make_file("a", library=music)
    make_file("b", library=sweeps)

    assert matching(make_block(("track_type_id", "is", str(music.pk)))) == [track.id]
    assert matching(make_block(("track_type_id", "is not", str(music.pk)))) != [
        track.id
    ]


def test_file_name_criteria_match_the_basename() -> None:
    """
    The legacy query compares the file name, not the whole path.

    ``reverse(split_part(reverse(filepath), '/', 1))`` is the basename, so a
    folder name is invisible to this criterion while the file name matches from
    either end.
    """
    inside = make_file("a", filepath="/media/jazz/01-saxophone.mp3")
    make_file("b", filepath="/media/rock/02-guitar.mp3")

    assert matching(make_block(("filepath", "starts with", "01-sax"))) == [inside.id]
    assert matching(make_block(("filepath", "contains", "saxophone"))) == [inside.id]
    assert matching(make_block(("filepath", "ends with", "phone.mp3"))) == [inside.id]

    # The folder is not part of the value being compared.
    assert matching(make_block(("filepath", "starts with", "jazz"))) == []
    assert matching(make_block(("filepath", "contains", "/media/"))) == []


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------


def test_before_and_after_use_absolute_timestamps() -> None:
    old = make_file("a", created_at=datetime(2025, 1, 1, tzinfo=UTC))
    make_file("b", created_at=datetime(2026, 3, 1, tzinfo=UTC))

    assert matching(make_block(("utime", "before", "2025-06-01 00:00:00"))) == [old.id]
    assert matching(make_block(("utime", "after", "2026-01-01 00:00:00"))) != [old.id]


def test_relative_dates_count_back_from_the_given_instant() -> None:
    recent = make_file("a", last_played_at=NOW - timedelta(days=2))
    make_file("b", last_played_at=NOW - timedelta(days=20))

    block = make_block(("lptime", "after", "7 days ago"))

    assert matching(block) == [recent.id]


def test_only_before_keeps_files_that_never_aired() -> None:
    """
    COALESCE(lptime, DATE '-infinity') answers differently per modifier.

    Negative infinity is before every date, so "played before 2020" keeps a file
    that never aired. The same value fails "played after ..." and "played
    between ...", so those drop it. The equivalence harness agrees with the
    legacy service on all three, which is the only reason this is written out.
    """
    never = make_file("a", last_played_at=None)

    assert never.id in matching(make_block(("lptime", "before", "2020-01-01 00:00:00")))
    assert never.id not in matching(
        make_block(("lptime", "after", "2020-01-01 00:00:00"))
    )
    assert never.id not in matching(
        make_block(("lptime", "between", "2020-01-01 00:00:00", "2021-01-01 00:00:00"))
    )


def test_between_is_inclusive_on_both_ends() -> None:
    inside = make_file("a", created_at=datetime(2026, 1, 15, tzinfo=UTC))
    make_file("b", created_at=datetime(2025, 12, 31, tzinfo=UTC))

    block = make_block(
        ("utime", "between", "2026-01-01 00:00:00", "2026-02-01 00:00:00")
    )

    assert matching(block) == [inside.id]


def test_is_in_the_range_compares_timestamps() -> None:
    inside = make_file("a", created_at=datetime(2026, 1, 15, tzinfo=UTC))
    make_file("b", created_at=datetime(2024, 1, 15, tzinfo=UTC))

    block = make_block(
        ("utime", "is in the range", "2026-01-01 00:00:00", "2026-02-01 00:00:00")
    )

    assert matching(block) == [inside.id]


def test_now_placeholder_is_resolved_in_the_station_timezone() -> None:
    old = make_file("a", created_at=datetime(2025, 1, 1, tzinfo=UTC))
    make_file("b", created_at=datetime(2026, 3, 31, 18, 0, tzinfo=UTC))

    block = make_block(("utime", "before", "now{Y-m-d} 00:00:00"))

    # NOW is 2026-03-31 12:00, so the placeholder becomes 2026-03-31 and only
    # the file uploaded that evening is left out.
    assert matching(block) == [old.id]


# ---------------------------------------------------------------------------
# groups
# ---------------------------------------------------------------------------


def test_same_group_is_or_and_different_groups_are_and() -> None:
    alpha = make_file("a", description="alpha", genre="Ambient")
    beta = make_file("b", description="beta", genre="Ambient")
    gamma = make_file("c", description="gamma", genre="Techno")
    make_file("d", description="alpha", genre="Techno")

    block = make_block(
        ("description", "is", "alpha", None, 0),
        ("description", "is", "beta", None, 0),
        ("genre", "is", "Ambient", None, 1),
    )

    assert sorted(matching(block)) == sorted([alpha.id, beta.id])
    assert gamma.id not in matching(block)


def test_or_group_anded_with_another_group() -> None:
    """
    ``alpha AND (beta OR gamma)``, which left to right would get wrong.

    Chained without groups the query becomes ``(alpha AND beta) OR gamma``, and
    the file that only says gamma would come back. This is the case the legacy
    code groups explicitly for, and the order the rows were saved in must not
    matter either way.
    """
    alpha_beta = make_file("a", description="alpha beta")
    alpha_gamma = make_file("b", description="alpha gamma")
    make_file("c", description="beta gamma")
    make_file("d", description="gamma")

    and_first = make_block(
        ("description", "contains", "alpha", None, 0),
        ("description", "contains", "beta", None, 1),
        ("description", "contains", "gamma", None, 1),
    )
    or_first = make_block(
        ("description", "contains", "beta", None, 1),
        ("description", "contains", "gamma", None, 1),
        ("description", "contains", "alpha", None, 0),
    )

    expected = sorted([alpha_beta.id, alpha_gamma.id])

    assert sorted(matching(and_first)) == expected
    assert sorted(matching(or_first)) == expected


def test_contradicting_groups_of_one_field_match_nothing() -> None:
    make_file("a", genre="Ambient")

    block = make_block(
        ("genre", "is", "Ambient", None, 0),
        ("genre", "is", "Techno", None, 1),
    )

    assert matching(block) == []


def test_different_fields_are_anded() -> None:
    both = make_file("a", genre="Ambient", mood="Calm")
    make_file("b", genre="Ambient", mood="Energetic")

    block = make_block(("genre", "is", "Ambient"), ("mood", "is", "Calm"))

    assert matching(block) == [both.id]


# ---------------------------------------------------------------------------
# the filters every block carries, and ordering
# ---------------------------------------------------------------------------


def test_missing_and_hidden_files_never_match() -> None:
    visible = make_file("a", genre="Ambient")
    make_file("b", genre="Ambient", exists=False)
    make_file("c", genre="Ambient", hidden=True)

    block = make_block(("genre", "is", "Ambient"))

    assert matching(block) == [visible.id]


def test_sort_newest_and_oldest() -> None:
    old = make_file("a", created_at=datetime(2020, 1, 1, tzinfo=UTC))
    new = make_file("b", created_at=datetime(2026, 1, 1, tzinfo=UTC))

    assert matching(make_block(sort="newest")) == [new.id, old.id]
    assert matching(make_block(sort="oldest")) == [old.id, new.id]


def test_sort_by_last_played_keeps_files_that_never_aired_last() -> None:
    played = make_file("a", last_played_at=datetime(2026, 1, 1, tzinfo=UTC))
    make_file("b", last_played_at=datetime(2025, 1, 1, tzinfo=UTC))
    never = make_file("c", last_played_at=None)

    most = matching(make_block(sort="mostrecentplay"))
    least = matching(make_block(sort="leastrecentplay"))

    assert most[0] == played.id
    assert most[-1] == never.id
    assert least[0] == never.id


# ---------------------------------------------------------------------------
# limit parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("modifier", "value", "expected"),
    [
        ("items", "5", BlockLimit(time_seconds=24 * 60 * 60, items=5)),
        ("hours", "1.5", BlockLimit(time_seconds=5400)),
        ("minutes", "30", BlockLimit(time_seconds=1800)),
        ("remaining", "0", BlockLimit(time_seconds=3600)),
    ],
)
def test_limits(modifier: str, value: str, expected: BlockLimit) -> None:
    block = make_block(limit=value, limit_modifier=modifier)

    limit = limit_of(block)

    assert limit.time_seconds == expected.time_seconds
    assert limit.items == expected.items


def test_remaining_uses_the_show_window_when_there_is_one() -> None:
    block = make_block(limit_modifier="remaining")

    assert limit_of(block, show_limit=900).time_seconds == 900


def test_repeat_and_overflow_switches() -> None:
    block = make_block(repeat_tracks=1, overflow_tracks=1)

    limit = limit_of(block)

    assert limit.repeat_tracks is True
    assert limit.overflow_tracks is True
    assert limit.sort == "newest"


# ---------------------------------------------------------------------------
# refusal
# ---------------------------------------------------------------------------


def test_unknown_criteria_is_refused() -> None:
    block = SmartBlock.objects.create(name="bogus")
    SmartBlockCriteria.objects.create(
        block=block, criteria="not_a_field", condition="is", value="x"
    )

    with pytest.raises(UnsupportedCriteria):
        file_ids(block, now=NOW)


def test_unknown_modifier_is_refused() -> None:
    block = make_block(("genre", "sounds like", "Ambient"))

    with pytest.raises(UnsupportedCriteria):
        file_ids(block, now=NOW)


def test_relative_date_without_an_upper_bound_is_refused() -> None:
    block = make_block(("utime", "between", "2026-01-01 00:00:00"))

    with pytest.raises(UnsupportedCriteria):
        file_ids(block, now=NOW)


def test_track_type_value_has_to_be_an_id() -> None:
    block = make_block(("track_type_id", "is", "Music"))

    with pytest.raises(UnsupportedCriteria):
        file_ids(block, now=NOW)


def test_unparsable_date_is_refused() -> None:
    block = make_block(("utime", "before", "the day before yesterday"))

    with pytest.raises(UnsupportedCriteria):
        file_ids(block, now=NOW)


# ---------------------------------------------------------------------------
# date helpers, checked against the legacy service
# ---------------------------------------------------------------------------


def test_month_arithmetic_overflows_the_way_php_does() -> None:
    """Verified against PHP: 2026-03-31 minus one month is 2026-03-03."""
    assert shift_months(datetime(2026, 3, 31, 12), -1) == datetime(2026, 3, 3, 12)
    assert shift_months(datetime(2026, 5, 31, 12), -1) == datetime(2026, 5, 1, 12)
    assert shift_months(datetime(2026, 1, 31, 12), 1) == datetime(2026, 3, 3, 12)
    assert shift_months(datetime(2026, 3, 31, 12), -3) == datetime(2025, 12, 31, 12)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("7 days ago", datetime(2026, 3, 24, 12, tzinfo=UTC)),
        ("1 day ago", datetime(2026, 3, 30, 12, tzinfo=UTC)),
        ("2 weeks ago", datetime(2026, 3, 17, 12, tzinfo=UTC)),
        ("3 months ago", datetime(2025, 12, 31, 12, tzinfo=UTC)),
        ("1 year ago", datetime(2025, 3, 31, 12, tzinfo=UTC)),
        ("12 hours ago", datetime(2026, 3, 31, 0, tzinfo=UTC)),
        ("2026-01-01 00:00:00", datetime(2026, 1, 1, tzinfo=UTC)),
        ("2026-01-01T00:00:00", datetime(2026, 1, 1, tzinfo=UTC)),
    ],
)
def test_parse_date(value: str, expected: datetime) -> None:
    assert parse_date(value, now=NOW) == expected


def test_resolve_now_formats_like_php() -> None:
    assert resolve_now("now{Y-m-d}", now=NOW, zone=UTC) == "2026-03-31"
    assert resolve_now("now{d/m/Y H:i:s}", now=NOW, zone=UTC) == "31/03/2026 12:00:00"
    assert resolve_now("plain", now=NOW, zone=UTC) == "plain"


def test_block_without_settings_rows_uses_the_defaults() -> None:
    block = SmartBlock.objects.create(name="bare")
    SmartBlockCriteria.objects.create(
        block=block, criteria="genre", condition="is", value="Ambient"
    )

    limit = limit_of(block)

    assert limit.time_seconds == 24 * 60 * 60
    assert limit.items is None
    assert limit.sort == "random"
    assert StoredCriteria.load(block).fields.keys() == {"genre"}
