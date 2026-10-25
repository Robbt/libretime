"""
Writing a session in php's serialization.

The expected strings below are what php's own ``serialize()`` produced for these
values, taken from the running station rather than from this implementation - the
one thing worth being exact about is whether the two agree, and a test written
against my reading of the format would only prove I read it consistently.

The encoder exists so the api can create a session the legacy application accepts
without a second login; see ``libretime_api.core.auth.session_store``.
"""

import pytest

from ....core.auth._phpserialize import (
    PhpEncodeError,
    PhpObject,
    dumps,
    loads,
)
from .php_session_test import LOGGED_IN


# Values, then php's answer for each.
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "N;"),
        (True, "b:1;"),
        (False, "b:0;"),
        (0, "i:0;"),
        (-17, "i:-17;"),
        (1.5, "d:1.5;"),
        (2.0, "d:2;"),
        ("hello", 's:5:"hello";'),
        ("", 's:0:"";'),
        ("éé", 's:4:"éé";'),
        (["x", "y"], 'a:2:{i:0;s:1:"x";i:1;s:1:"y";}'),
        ({"a": 1, "b": 2}, 'a:2:{s:1:"a";i:1;s:1:"b";i:2;}'),
        ({}, "a:0:{}"),
        ([], "a:0:{}"),
        ({3: "x", 1: "y"}, 'a:2:{i:3;s:1:"x";i:1;s:1:"y";}'),
        (
            {"k": [1, "two", None, False]},
            'a:1:{s:1:"k";a:4:{i:0;i:1;i:1;s:3:"two";i:2;N;i:3;b:0;}}',
        ),
        (
            PhpObject({"id": 2, "login": "spike", "email": None}),
            'O:8:"stdClass":3:{s:2:"id";i:2;s:5:"login";s:5:"spike";s:5:"email";N;}',
        ),
    ],
)
def test_php_writes_it_this_way(value, expected):
    assert dumps(value) == expected


def test_a_session_written_then_read_is_the_session_that_was_written():
    """
    The real payload from a logged in browser, encoded again by this code, byte for
    byte. php itself round-trips this string unchanged, so any difference here is
    this encoder's, not the format's.
    """
    assert dumps(loads(LOGGED_IN)) == LOGGED_IN


def test_an_object_stays_an_object():
    decoded = loads(LOGGED_IN)

    storage = decoded["libretime"]["storage"]

    assert isinstance(storage, PhpObject)
    assert storage["login"] == "api_spike"


@pytest.mark.parametrize(
    "value",
    [
        b"bytes have no php string counterpart here",
        {"key": object()},
        {"key": b"x"},
        {1.5: "a float is not a php array key"},
        float("nan"),
        float("inf"),
        1e-9,  # php would spell this d:1.0E-9
        (1, 2),  # a tuple is a sequence, but a surprising thing to find in a session
    ],
)
def test_what_cannot_be_written_is_refused(value):
    with pytest.raises(PhpEncodeError):
        dumps(value)


def test_a_tuple_is_refused_rather_than_becoming_an_array():
    """
    An empty tuple would be encoded as an empty array; the point of refusing is
    that nothing in a session should be a tuple in the first place.
    """
    with pytest.raises(PhpEncodeError):
        dumps(("a", "b"))
