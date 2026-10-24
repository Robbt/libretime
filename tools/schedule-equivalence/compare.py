#!/usr/bin/env python3
"""Compare the rows the legacy and the Python builder produced.

    compare.py <php-dir> <python-dir>

Both directories hold the CSVs written by dump.sh. A table is only equal when
the same rows - and in the generated case the same primary keys, since those
follow insertion order - are present with the same values.
"""

import csv
import sys
from pathlib import Path

# Column used to match a row on the other side.
KEYS = {
    "instances.csv": ("show_id", "starts", "rebroadcast"),
    "show_days.csv": ("id",),
    "schedule.csv": ("show_id", "instance_starts", "position"),
    "prefs.csv": ("keystr",),
}

MAX_REPORTED = 15


def load(path: Path):
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    by_key = {}
    for row in rows:
        key = tuple(row[key_column] for key_column in KEYS[path.name])
        if key in by_key:
            raise SystemExit(
                f"{path}: duplicated key {key}, the comparison key is too weak - rows "
                f"would be silently collapsed"
            )
        by_key[key] = row

    return by_key, rows


def compare_table(name: str, php_dir: Path, py_dir: Path) -> int:
    php, php_rows = load(php_dir / name)
    py, py_rows = load(py_dir / name)

    problems = []

    if len(php_rows) != len(py_rows):
        problems.append(f"row count: php {len(php_rows)} != python {len(py_rows)}")

    for key in sorted(php.keys() - py.keys()):
        problems.append(f"missing in python: {key}")
    for key in sorted(py.keys() - php.keys()):
        problems.append(f"unexpected in python: {key}")

    for key in sorted(php.keys() & py.keys()):
        for column in php[key]:
            left, right = php[key][column], py[key][column]
            if left != right:
                problems.append(f"{key} {column}: php {left!r} != python {right!r}")

    if not problems:
        print(f"  {name:<15} {len(php_rows):>5} rows  OK")
        return 0

    print(f"  {name:<15} {len(php_rows):>5} rows  DIFFERS ({len(problems)} problems)")
    for problem in problems[:MAX_REPORTED]:
        print(f"      {problem}")
    if len(problems) > MAX_REPORTED:
        print(f"      ... {len(problems) - MAX_REPORTED} more")
    return len(problems)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2

    php_dir, py_dir = (Path(arg) for arg in sys.argv[1:])

    total = 0
    for name in sorted(KEYS):
        if not (php_dir / name).exists() or not (py_dir / name).exists():
            print(f"  {name:<15} missing dump")
            total += 1
            continue
        total += compare_table(name, php_dir, py_dir)

    if total:
        print(f"\nFAIL: {total} differences between the two builders")
        return 1

    print("\nOK: both builders produced the same rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
