"""
Filtering, searching and ordering the collections.

Every parameter here is opt-in: a request that sends none of them gets the response it
got before, and that is deliberate. ``/api/v2/schedule`` is read by playout, which
parses a bare json array in the order the database returned it - so ordering is never
applied to a queryset as a side effect of this module. Where a view sets a default
ordering it says so, and only for lists no service consumes.

Three mechanisms, always together:

- filters: exact match, or a range for the fields where "between these two" is the
  question a screen actually asks. Range parameters follow the pattern the schedule
  collection already used - ``starts_after`` and ``starts_before``.
- ``search``: a case-insensitive substring across the few columns the legacy interface
  searches. Not full text: the library's search box has never been more than that, and
  pretending otherwise would need an index this schema does not have.
- ``ordering``: one field name, optionally prefixed with ``-``. Unrecognised names are
  ignored by the framework rather than turned into a database error, which is what
  makes it safe to widen the list later.

The viewsets say which fields they expose; nothing here has a default, because an
undocumented ``search_fields`` on a large table is a full scan waiting for the first
person to type in a box, and an ``ordering_fields`` list wider than the interface needs
is a promise about columns that can change.
"""

from __future__ import annotations

from collections.abc import Sequence

from django_filters import rest_framework as django_filters
from rest_framework.filters import BaseFilterBackend, OrderingFilter, SearchFilter

# The three backends, in the order DRF applies them: narrow, then match, then sort.
QUERY_BACKENDS: Sequence[type[BaseFilterBackend]] = [
    django_filters.DjangoFilterBackend,
    SearchFilter,
    OrderingFilter,
]


class Queryable:
    """Filter, search and order a collection by the fields the view names."""

    # Declared exactly as DRF declares it on GenericAPIView - an attribute holding a
    # sequence of filter classes, not a class variable. The distinction is not
    # cosmetic: typed as a ClassVar, every class that mixes this in is reported as an
    # incompatible override of the view it also inherits, and the message blames the
    # view rather than this line.
    filter_backends: Sequence[type[BaseFilterBackend]] = QUERY_BACKENDS


def range_filters(name: str, *, field: str | None = None) -> dict:
    """
    ``?<name>_after=`` and ``?<name>_before=`` for a timestamp column.

    Both bounds are inclusive, which is what a calendar asking for "this week" means
    and what the schedule collection's existing filters already do.
    """
    column = field or name

    return {
        f"{name}_after": django_filters.IsoDateTimeFilter(
            field_name=column, lookup_expr="gte"
        ),
        f"{name}_before": django_filters.IsoDateTimeFilter(
            field_name=column, lookup_expr="lte"
        ),
    }
