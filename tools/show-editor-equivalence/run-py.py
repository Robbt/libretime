"""
Run every fixture scenario through the python show editor and dump the result.

The same scenarios, the same fixture state and the same dump query as
``run-php.php`` - see that file and the README for why each is shaped the way it
is.
"""

import json
import sys
from datetime import date

import django

django.setup()

from django.db import connection  # noqa: E402

from libretime_api.schedule.services.show import (  # noqa: E402
    ShowForm,
    delete_show_instance,
    edit_repeating_instance,
    extend_show_instances,
    save_show,
)

# Steps the port does not implement yet. They are named rather than silently
# skipped, so a run says what it did not cover.
NOT_YET: set[str] = set()

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
    (
        "cc_schedule",
        "select id, starts, ends, file_id, clip_length, fade_in, fade_out, cue_in, "
        "cue_out, instance_id, playout_status from cc_schedule order by id",
    ),
]


def run_script(name: str) -> None:
    with open(f"/tools/{name}", encoding="utf-8") as handle:
        script = handle.read()

    for statement in script.split(";"):
        if statement.strip():
            connection.cursor().execute(statement)


def seed_scheduled_item(instance_id: int) -> None:
    """
    The same two inserts as ``seedScheduledItem`` in run-php.php.

    The upstream test drives the scheduler for this, because that is how a person
    puts an item into a show; the harness seeds it directly instead, since the
    behaviour under test is what an edit does to an item that is already there.
    Keep the two in step - a difference between them would show up as a difference
    between the runs, which is the wrong answer to be chasing.
    """
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO cc_files (id, track_title, mime, length, cuein, cueout)"
        " VALUES (1, 'seeded item', 'audio/mpeg', '00:04:32', '00:00:00', '00:04:32')"
    )
    cursor.execute("SELECT setval(pg_get_serial_sequence('cc_files', 'id'), 2, false)")
    cursor.execute(
        "INSERT INTO cc_schedule (starts, ends, instance_id, file_id, clip_length,"
        " fade_in, fade_out, cue_in, cue_out, position, playout_status, broadcasted)"
        " SELECT i.starts, i.starts + INTERVAL '00:04:32', i.id, 1, '00:04:32',"
        " '00:00:00.5', '00:00:00.5', '00:00:00', '00:04:32', 0, 1, 0"
        " FROM cc_show_instances i WHERE i.show_id ="
        " (SELECT show_id FROM cc_show_instances WHERE id = %s) AND i.rebroadcast = 0"
        " ORDER BY i.id",
        [instance_id],
    )


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

        steps = [step["call"] for step in scenario["steps"]]
        missing = sorted({step for step in steps if step in NOT_YET})

        if missing:
            blocks.append(
                "\n".join([f"### {scenario['name']}", f"#not-yet|{','.join(missing)}"])
            )
            continue

        payload = scenario["payload"]
        is_update = False
        show_id = None

        for step in scenario["steps"]:
            call = step["call"]

            # An edit posts the same payload back with the fields the user changed.
            payload.update(step.get("set") or {})

            if call == "addUpdateShow":
                show, _created = save_show(
                    ShowForm.from_legacy_payload(payload), is_update=is_update
                )
                show_id = show.pk
                is_update = True
            elif call == "deleteShow":
                args = step["args"]
                # The calendar's two menu entries, exactly as php gets them: the
                # instance to remove, and whether it is only that one.
                delete_show_instance(args[0], single_instance=len(args) > 1 and bool(args[1]))
            elif call == "editRepeatingShowInstance":
                edit_repeating_instance(step["data"])
            elif call == "delegateInstanceCreation":
                extend_show_instances(
                    show_id,
                    date.fromisoformat(step["end"]),
                    fill_instances=bool(step.get("fill")),
                )
            elif call == "seedSchedule":
                seed_scheduled_item(step["instance"])

        blocks.append("\n".join([f"### {scenario['name']}", *dump()]))

    sys.stdout.write("\n".join(blocks) + "\n")

    return 0


sys.exit(main())
