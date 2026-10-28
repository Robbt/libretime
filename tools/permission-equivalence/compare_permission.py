#!/usr/bin/env python3
"""
Compare the two verdict files line by line.

Both sides print ``<user> <kind> <id> <action>|<allow|deny>|<why>``, and only the
first two fields are compared: the third says how each side reached its answer and is there
to read when the answers differ.

Differences that are known and wanted are listed below with the reason, counted
separately, and printed - a harness that silently forgives is worse than none.
"""

import sys
from pathlib import Path

# (user, kind) pairs where the api is meant to disagree with the legacy answer,
# with the reason. See "Deliberate divergence" in README.md.
EXPECTED = {
    (9001, "webstream"): (
        "Application_Model_Webstream::deleteStreams leaves UTYPE_SUPERADMIN out of "
        "its manager list; the api treats a superadmin as an admin, as everywhere "
        "else. Report upstream."
    ),
}


def load(path: Path) -> dict[str, str]:
    verdicts = {}

    for line in path.read_text().splitlines():
        if "|" not in line:
            continue

        case, verdict, _why = line.split("|")
        verdicts[case.strip()] = verdict.strip()

    return verdicts


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <php-file> <python-file>", file=sys.stderr)

        return 2

    php = load(Path(sys.argv[1]))
    api = load(Path(sys.argv[2]))

    missing = sorted(set(php) - set(api))
    extra = sorted(set(api) - set(php))

    if missing or extra:
        print("the two sides did not cover the same cases")
        for case in missing[:10]:
            print(f"  only in the legacy run: {case}")
        for case in extra[:10]:
            print(f"  only in the api run:    {case}")

        return 1

    mismatches = []
    forgiven = []

    for case, legacy in php.items():
        mine = api[case]

        if legacy == mine:
            continue

        user, kind, _obj_id, _action = case.split()
        reason = EXPECTED.get((int(user), kind))

        if reason is not None:
            forgiven.append((case, legacy, mine, reason))
        else:
            mismatches.append((case, legacy, mine))

    print(f"{len(php)} cases compared")
    print(f"{len(php) - len(mismatches) - len(forgiven)} agree")
    print(f"{len(forgiven)} differ as expected")

    if forgiven:
        for case, legacy, mine, reason in sorted(forgiven)[:6]:
            print(f"  {case}: legacy {legacy}, api {mine}")
        print(f"  reason: {sorted({r for _, _, _, r in forgiven})[0]}")

    if mismatches:
        print(f"\n{len(mismatches)} UNEXPECTED DIFFERENCES", file=sys.stderr)

        # Grouped, because the interesting question is which rule is wrong rather
        # than which of 336 lines differs.
        by_pattern: dict[tuple, list] = {}

        for case, legacy, mine in mismatches:
            user, kind, obj_id, action = case.split()
            by_pattern.setdefault((user, kind, action, legacy, mine), []).append(obj_id)

        for (user, kind, action, legacy, mine), obj_ids in sorted(by_pattern.items()):
            ids = ", ".join(obj_ids[:4])
            more = f" (+{len(obj_ids) - 4} more)" if len(obj_ids) > 4 else ""
            print(
                f"  user {user} {kind} [{ids}] {action}{more}: "
                f"legacy {legacy}, api {mine}",
                file=sys.stderr,
            )

        return 1

    print("all other cases agree")

    return 0


sys.exit(main())
