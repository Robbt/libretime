"""Run the PYTHON show instance builder against the current fixture state.

Called by ab.sh with the API settings pointed at the same database the legacy
builder just ran against. The rows written are dumped and compared against the
legacy ones.
"""

import os
from datetime import datetime, timezone

import django

django.setup()

from django.db import connection  # noqa: E402

from libretime_api.schedule.services import populate_show_instances  # noqa: E402

until = datetime.strptime(
    os.environ.get("AB_UNTIL", "2026-12-31 00:00:00"), "%Y-%m-%d %H:%M:%S"
).replace(tzinfo=timezone.utc)

created = populate_show_instances(until)

with connection.cursor() as cur:
    cur.execute("select count(*) from cc_show_instances")
    total = cur.fetchone()[0]
    cur.execute("select valstr from cc_pref where keystr = 'shows_populated_until'")
    pref = cur.fetchone()[0]

print(f"python: created {created} instances, total {total}, populated_until = {pref}")

# populate_show_instances() logs and rolls back instead of raising, so a run
# that did nothing would otherwise look like a successful one.
if pref != until.strftime("%Y-%m-%d %H:%M:%S"):
    raise SystemExit(
        "python: populated-until preference was not updated, the run did not complete"
    )
