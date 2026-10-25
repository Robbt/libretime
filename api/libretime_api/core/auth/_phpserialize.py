"""
The subset of PHP's ``serialize()`` that LibreTime sessions contain.

Only what is needed to read the identity out of ``sessions.data``. Positions are
tracked in bytes, because PHP's ``s:<n>:`` counts bytes, and the document is read
as utf-8 with the string values decoded at the end.

Anything outside the supported subset - objects of a class other than
``stdClass``, references, resources, a document with trailing bytes - raises.
A session payload that cannot be read completely must not be trusted partially:
the alternative is guessing, and a wrong guess here means handing somebody
another user's identity.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

# Zend_Auth stores the identity as a stdClass; nothing else is expected.
SUPPORTED_CLASSES = (b"stdClass",)


class PhpDecodeError(ValueError):
    """The payload is not PHP serialization this reader understands."""


class PhpEncodeError(ValueError):
    """The value has no php serialization this writer can produce faithfully."""


class PhpObject(dict):
    """
    A php object rather than a php array.

    php distinguishes ``stdClass`` from an array and so does the wire format,
    while both look like a mapping to python. Anything read as an object comes
    back from :func:`loads` as this class, so a payload that is decoded and
    encoded again keeps the shape it arrived with.
    """

    def __init__(
        self,
        mapping: Mapping[Any, Any] | Iterable[tuple[Any, Any]] | None = None,
        *,
        php_class: str = "stdClass",
    ) -> None:
        super().__init__(mapping or {})
        self.php_class = php_class


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.position = 0

    def read(self) -> object:
        value = self._value()

        if self.position != len(self.data):
            raise PhpDecodeError("trailing bytes after the session payload")

        return value

    # -- primitives ---------------------------------------------------------

    def _take(self, count: int) -> bytes:
        if count < 0 or self.position + count > len(self.data):
            raise PhpDecodeError("truncated payload")

        chunk = self.data[self.position : self.position + count]
        self.position += count

        return chunk

    def _until(self, marker: bytes) -> bytes:
        index = self.data.find(marker, self.position)

        if index == -1:
            raise PhpDecodeError(f"missing {marker!r}")

        chunk = self.data[self.position : index]
        self.position = index + len(marker)

        return chunk

    def _expect(self, char: bytes) -> None:
        if self._take(len(char)) != char:
            raise PhpDecodeError(f"expected {char!r}")

    # -- values -------------------------------------------------------------

    def _value(self) -> object:
        """
        Read one value.

        Every PHP type is ``<letter>:<body>;``, and reading the tag consumes the
        first colon, which is why the branches below do not expect it again.
        """
        # Every value starts with a one letter tag. Only "N" - null - is not
        # followed by a colon, so it has to be read before splitting on one,
        # otherwise the colon of the next value in the document is mistaken for
        # this one's.
        kind = self._take(1)

        if kind == b"N":
            self._expect(b";")

            return None

        self._expect(b":")

        if kind == b"b":
            digit = self._take(1)
            self._expect(b";")

            if digit not in (b"0", b"1"):
                raise PhpDecodeError(f"not a boolean: b:{digit.decode('latin-1')}")

            return digit == b"1"

        if kind == b"i":
            return int(self._until(b";"))

        if kind == b"d":
            text = self._until(b";").decode("ascii")

            if text in ("INF", "-INF", "NAN"):
                raise PhpDecodeError(f"non finite number {text}")

            return float(text)

        if kind == b"s":
            return self._string()

        if kind == b"a":
            return self._mapping()

        if kind == b"O":
            return self._object()

        raise PhpDecodeError(f"unsupported PHP type {kind!r}")

    def _string(self) -> str:
        length = int(self._until(b":"))
        self._expect(b'"')
        raw = self._take(length)
        self._expect(b'";')

        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PhpDecodeError("string is not valid utf-8") from error

    def _mapping(self) -> object:
        count = int(self._until(b":"))
        self._expect(b"{")

        # An empty PHP array carries no keys to decide by, and is a list to
        # anyone reading it from outside PHP.
        if count == 0:
            self._expect(b"}")

            return []

        entries, numeric_keys = self._entries(count)

        return self._finish(entries, numeric_keys)

    def _object(self) -> object:
        name_length = int(self._until(b":"))
        self._expect(b'"')
        name = self._take(name_length)
        self._expect(b'":')
        count = int(self._until(b":"))
        self._expect(b"{")

        if name not in SUPPORTED_CLASSES:
            raise PhpDecodeError(f"unsupported PHP class {name.decode('latin-1')!r}")

        entries, numeric_keys = self._entries(count)

        if not numeric_keys:
            self._expect(b"}")

            return PhpObject(entries, php_class=name.decode("latin-1"))

        return self._finish(entries, numeric_keys)

    def _entries(self, count: int) -> tuple[dict, list]:
        entries: dict = {}
        numeric_keys: list[int] = []

        for _ in range(count):
            key = self._value()
            value = self._value()

            if not isinstance(key, (str, int)):
                raise PhpDecodeError("array key is not a string or an integer")

            entries[key] = value

            if isinstance(key, int):
                numeric_keys.append(key)

        return entries, numeric_keys

    def _finish(self, entries: dict, numeric_keys: list[int]) -> object:
        self._expect(b"}")

        # A PHP array holding the keys 0..n-1 in order is a list anywhere else.
        # An associative array, and an empty one, are not.
        if numeric_keys and len(numeric_keys) == len(entries):
            if numeric_keys == list(range(len(numeric_keys))):
                return [entries[index] for index in numeric_keys]

        return entries


def loads(payload: str) -> object:
    """Decode a PHP ``serialize()`` document, strictly."""
    if not payload:
        raise PhpDecodeError("empty session payload")

    return _Reader(payload.encode("utf-8")).read()


def dumps(value: Any) -> str:
    """
    Encode a value the way PHP's ``serialize()`` would.

    Only what a LibreTime session holds: null, booleans, integers, strings,
    arrays and stdClass. A value outside that has no faithful encoding here, and
    inventing one - a date as an integer, an object as an array - would write a
    session the legacy application reads differently from the one the api meant.
    """
    return "".join(_encode(value))


def _encode(value: Any) -> Iterator[str]:
    # None and bool before the numbers they resemble: in python True is an int,
    # and in php false is not null.
    if value is None:
        yield "N;"
        return

    if isinstance(value, bool):
        yield f"b:{1 if value else 0};"
        return

    if isinstance(value, int):
        yield f"i:{value};"
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise PhpEncodeError(f"php would write {value} as a word, not a number")

        # php writes a float with at most 14 significant digits and no trailing
        # fraction, so 2.0 becomes "2" and 1.5 stays "1.5". Anything that needs an
        # exponent it spells differently ("d:1.0E-6"), so stop rather than write a
        # number the legacy application would read back as something else.
        text = f"{value:.14g}"
        if "e" in text or "E" in text:
            raise PhpEncodeError(f"cannot write {value!r} the way php does")

        yield f"d:{text};"
        return

    if isinstance(value, str):
        # The length prefix counts bytes, which is why a multi-byte character
        # has to be measured in utf-8 and not len().
        yield f's:{len(value.encode("utf-8"))}:"{value}";'
        return

    if isinstance(value, PhpObject):
        yield from _encode_object(value)
        return

    if isinstance(value, Mapping):
        yield from _encode_entries(value.items(), count=len(value), kind="a")
        return

    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray, tuple)
    ):
        # str and bytes are sequences of things nobody means as a php array. A
        # tuple is refused rather than quietly turned into one.
        yield from _encode_entries(enumerate(value), count=len(value), kind="a")
        return

    raise PhpEncodeError(f"cannot write {type(value).__name__} to a php session")


def _encode_object(value: PhpObject) -> Iterator[str]:
    name = value.php_class.encode("utf-8")

    yield f'O:{len(name)}:"{value.php_class}":{len(value)}:{{'

    for key, item in value.items():
        yield from _encode_key(key)
        yield from _encode(item)

    yield "}"


def _encode_entries(items, *, count: int, kind: str) -> Iterator[str]:
    yield f"{kind}:{count}:{{"

    for key, item in items:
        yield from _encode_key(key)
        yield from _encode(item)

    yield "}"


def _encode_key(key: Any) -> Iterator[str]:
    if isinstance(key, str):
        yield from _encode(key)
        return

    if isinstance(key, int) and not isinstance(key, bool):
        yield f"i:{key};"
        return

    raise PhpEncodeError(
        f"php array keys are strings or integers, not {type(key).__name__}"
    )
