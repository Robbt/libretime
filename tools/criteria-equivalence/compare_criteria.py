#!/usr/bin/env python3
"""Compare the file lists the two criteria compilers returned.

    compare_criteria.py <php-file> <python-file>

Each file holds "<block id>|<comma separated file ids>" lines, in the order the
compiler produced them - the order is part of the answer, because a smart block
feeds a schedule in that order.
"""

import sys
from pathlib import Path

MAX_REPORTED = 20


def load(path: Path) -> dict[str, str]:
    answers = {}

    for line in path.read_text().splitlines():
        if "|" not in line:
            continue
        block, files = line.split("|", 1)
        answers[block] = files

    return answers


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)

        return 2

    php = load(Path(sys.argv[1]))
    python = load(Path(sys.argv[2]))

    problems = []

    for block in sorted(set(php) - set(python)):
        problems.append(f"block {block}: missing in python")
    for block in sorted(set(python) - set(php)):
        problems.append(f"block {block}: unexpected in python")

    for block in sorted(set(php) & set(python)):
        if php[block] == python[block]:
            continue

        want, got = php[block].split(","), python[block].split(",")
        problems.append(
            f"block {block}: php {len(want)} files, python {len(got)} files"
        )
        problems.append(f"    php    {php[block][:160]}")
        problems.append(f"    python {python[block][:160]}")

    if not problems:
        print(f"OK: both compilers returned the same files for {len(php)} blocks")

        return 0

    print(f"DIFFERS ({len(problems)} problems)")
    for problem in problems[:MAX_REPORTED]:
        print(f"  {problem}")
    if len(problems) > MAX_REPORTED:
        print(f"  ... {len(problems) - MAX_REPORTED} more")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
