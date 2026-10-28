#!/usr/bin/env python3
"""Compare the rows the legacy builder and the Python one wrote.

    compare_playlist_scheduler.py <php-dir> <python-dir>

Times are compared as written, including their microseconds: the legacy insert
formats them with DEFAULT_MICROTIME_FORMAT and the port keeps the same
precision, so a difference in the crossfade arithmetic shows up immediately.
"""

import csv
import sys
from pathlib import Path

TABLES = {
    "schedule.csv": ("instance_id", "position"),
    "instances.csv": ("id",),
    "files.csv": ("id",),
}

MAX_REPORTED = 12


def load(directory: Path, name: str) -> dict[tuple, dict]:
    with (directory / name).open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    key = TABLES[name]

    return {tuple(row[column] for column in key): row for row in rows}


def compare(name: str, php_dir: Path, py_dir: Path) -> int:
    php, python = load(php_dir, name), load(py_dir, name)

    problems = []

    for key in sorted(set(php) - set(python)):
        problems.append(f"missing in python: {key}")
    for key in sorted(set(python) - set(php)):
        problems.append(f"unexpected in python: {key}")

    for key in sorted(set(php) & set(python)):
        for column in php[key]:
            if php[key][column] != python[key][column]:
                problems.append(
                    f"{key} {column}: php {php[key][column]!r} != "
                    f"python {python[key][column]!r}"
                )

    if not problems:
        print(f"  {name:<15} {len(php):>4} rows  OK")

        return 0

    print(f"  {name:<15} {len(php):>4} rows  DIFFERS ({len(problems)} problems)")
    for problem in problems[:MAX_REPORTED]:
        print(f"      {problem}")

    return len(problems)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)

        return 2

    php_dir, py_dir = (Path(argument) for argument in sys.argv[1:])

    total = sum(compare(name, php_dir, py_dir) for name in TABLES)

    if total:
        print(f"\nFAIL: {total} differences")

        return 1

    print("\nOK: both builders wrote the same rows")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
