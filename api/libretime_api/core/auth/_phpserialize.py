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

# Zend_Auth stores the identity as a stdClass; nothing else is expected.
SUPPORTED_CLASSES = (b"stdClass",)


class PhpDecodeError(ValueError):
    """The payload is not PHP serialization this reader understands."""


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
