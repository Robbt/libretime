#!/usr/bin/env python3
"""
Compare two runs of the show editor, or a run against the datasets committed
upstream.

Both dump files hold, per scenario, the five tables the legacy unit tests assert
on. Differences are reported per row and per column, because "the weekly scenario
differs" is not an actionable sentence; "cc_show_instances id=4 ends: php
2044-01-22 01:00:00, port 2044-01-22 00:59:59" is.

    compare.py out/php out/python     the port against the legacy service
    compare.py --spec out/php         the legacy run against upstream's expectation

The second one is the check that the harness itself is honest: the files under
legacy/tests/application/services/database/datasets/ are what the project says the
editor is supposed to produce, so a harness that reproduces them is asking the
right questions.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

SPEC_DIR = Path(
    __file__
).resolve().parent.parent.parent / "legacy/tests/application/services/database/datasets"

# Values Postgres and the committed yaml write differently for the same thing.
# Each rule here is a formatting difference, never a semantic one - if a rule ever
# hides a real difference, delete it and let the comparison complain.
CLOCK = re.compile(r"^\d+:\d{1,2}(:\d{1,2})?(\.\d+)?$")


def canon(value: object) -> str:
    if value is None:
        return "NULL"

    if isinstance(value, bool):
        return "true" if value else "false"

    text = str(value).strip()

    if text.lower() in ("null", "none", "~"):
        return "NULL"

    # json renders a timestamp with a T between date and time; the yaml datasets
    # spell it with a space. Same instant.
    if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text):
        text = text.replace("T", " ")

    # json renders an interval as 01:00:00, the yaml datasets sometimes as 01:00.
    # Compare both as clock text.
    if CLOCK.match(text):
        parts = text.split(".")
        hh, mm, ss = (parts[0].split(":") + ["0", "0"])[:3]

        return f"{int(hh):02d}:{int(mm):02d}:{int(ss):02d}" + (
            f".{parts[1]}" if len(parts) > 1 else ""
        )

    return text


def parse_dump(path: Path) -> dict[str, dict[str, list[dict[str, str]]]]:
    """``### scenario`` blocks of ``table|col=value;...`` lines."""
    scenarios: dict[str, dict[str, list[dict[str, str]]]] = {}
    current: dict[str, list[dict[str, str]]] | None = None

    for line in path.read_text().splitlines():
        line = line.strip()

        if not line:
            continue

        if line.startswith("### "):
            current = {}
            scenarios[line[4:]] = current

            continue

        if current is None or "|" not in line:
            continue

        table, payload = line.split("|", 1)
        row = {column: canon(value) for column, value in json.loads(payload).items()}

        current.setdefault(table, []).append(row)

    return scenarios


def parse_spec(name: str) -> dict[str, list[dict[str, str]]]:
    """A committed yaml dataset, into the same shape as a parsed dump."""
    raw = yaml.safe_load((SPEC_DIR / name).read_text())
    tables = {}

    for table, rows in raw.items():
        # A table listed with nothing under it is the test saying "no rows here",
        # which is an assertion, not an absent table.
        tables[table] = [
            {column: canon(value) for column, value in row.items()} for row in (rows or [])
        ]

    return tables


def compare_tables(
    label: str, want: dict[str, list[dict[str, str]]], got: dict[str, list[dict[str, str]]]
) -> list[str]:
    problems = []
    names = sorted(set(want) | set(got))

    for table in names:
        want_rows = want.get(table, [])
        got_rows = got.get(table, [])

        if len(want_rows) != len(got_rows):
            problems.append(
                f"{label} {table}: {len(want_rows)} rows expected, {len(got_rows)} produced"
            )

        for index, (left, right) in enumerate(zip(want_rows, got_rows)):
            key = left.get("id") or right.get("id") or str(index)

            for column in sorted(set(left) | set(right)):
                a, b = left.get(column), right.get(column)

                if a is None:
                    problems.append(f"{label} {table} id={key} {column}: absent in the first")
                elif b is None:
                    problems.append(f"{label} {table} id={key} {column}: absent in the second")
                elif a != b:
                    problems.append(
                        f"{label} {table} id={key} {column}: '{a}' vs '{b}'"
                    )

    return problems


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(__doc__, file=sys.stderr)

        return 2

    spec_mode = sys.argv[1] == "--spec"

    if spec_mode:
        runs = parse_dump(Path(sys.argv[2]))
        problems = []
        checked = skipped = 0

        scenarios = {s["name"]: s for s in load_scenarios()}

        for name, tables in runs.items():
            spec = scenarios.get(name, {}).get("spec")

            if spec is None:
                skipped += 1
                continue

            checked += 1
            problems += compare_tables(f"[{name}]", parse_spec(spec), tables)

        print(f"{checked} scenarios compared with upstream datasets, {skipped} without one")

        return report(problems)

    want = parse_dump(Path(sys.argv[1]))
    got = parse_dump(Path(sys.argv[2]))

    if set(want) != set(got):
        print(
            f"different scenario sets: only in the first {set(want) - set(got)}, "
            f"only in the second {set(got) - set(want)}",
            file=sys.stderr,
        )

        return 1

    problems = []

    for name in want:
        problems += compare_tables(f"[{name}]", want[name], got[name])

    print(f"{len(want)} scenarios compared")

    return report(problems)


def load_scenarios():
    """The scenario list, for its spec filename per scenario."""
    return json.loads(Path(__file__).with_name("scenarios.json").read_text())


def report(problems: list[str]) -> int:
    if not problems:
        print("identical")

        return 0

    print(f"\n{len(problems)} differences", file=sys.stderr)

    for problem in problems[:60]:
        print(f"  {problem}", file=sys.stderr)

    if len(problems) > 60:
        print(f"  ... and {len(problems) - 60} more", file=sys.stderr)

    return 1


sys.exit(main())
