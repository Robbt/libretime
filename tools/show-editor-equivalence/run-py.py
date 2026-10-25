"""
Run every fixture scenario through the python show editor and dump the result.

The same scenarios, the same fixture state and the same dump query as
``run-php.php`` - see that file and the README for why each is shaped the way it
is.
"""

import json
import sys

import django

django.setup()

from django.db import connection  # noqa: E402

from libretime_api.schedule.services.show import ShowForm, save_show  # noqa: E402

# Steps the port does not implement yet. They are named rather than silently
# skipped, so a run says what it did not cover.
NOT_YET = {"deleteShow", "editRepeatingShowInstance"}

TABLES = [
    ("cc_show", "select * from cc_show order by id"),
    ("cc_show_days", "select * from cc_show_days order by id"),
    (
        "cc_show_instances",
        "select id, starts, ends, show_id, record, rebroadcast, instance_id, "
        "modified_instance from cc_show_instances order by id",
    ),
    ("cc_show_rebroadcast", "select * from cc_show_rebroadcast order by id"),
    ("cc_show_hosts", "select * from cc_show_hosts order by id"),
]


def run_script(name: str) -> None:
    with open(f"/tools/{name}", encoding="utf-8") as handle:
        script = handle.read()

    for statement in script.split(";"):
        if statement.strip():
            connection.cursor().execute(statement)


def dump() -> list[str]:
    lines = []

    for table, projection in TABLES:
        with connection.cursor() as cursor:
            cursor.execute(f"select to_jsonb(t)::text from ({projection}) t")  # noqa: S608
            for (payload,) in cursor.fetchall():
                lines.append(f"{table}|{payload}")

    return lines


def main() -> int:
    with open("/tools/scenarios.json", encoding="utf-8") as handle:
        scenarios = json.load(handle)

    blocks = []

    for scenario in scenarios:
        run_script("clean.sql")
        run_script("seed.sql")

        steps = [step[0] for step in scenario["steps"]]
        missing = sorted({step for step in steps if step in NOT_YET})

        if missing:
            blocks.append(
                "\n".join([f"### {scenario['name']}", f"#not-yet|{','.join(missing)}"])
            )
            continue

        payload = scenario["payload"]
        is_update = False

        for step in scenario["steps"]:
            if step[0] == "addUpdateShow":
                save_show(ShowForm.from_legacy_payload(payload), is_update=is_update)
                is_update = True

        blocks.append("\n".join([f"### {scenario['name']}", *dump()]))

    sys.stdout.write("\n".join(blocks) + "\n")

    return 0


sys.exit(main())
