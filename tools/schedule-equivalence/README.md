# Schedule builder equivalence harness

The Python port of the show instance generator
(`api/libretime_api/schedule/services/`) has to produce rows the playout
service cannot tell apart from the ones the legacy PHP builder produces. This
harness proves it, by running both over the same database state and diffing the
resulting rows.

## Run it

From the repository root, with the development stack up (`make dev`) and the
schedule worktree mounted inside the container used for the Python side:

```bash
tools/schedule-equivalence/ab.sh              # both builders, then compare
tools/schedule-equivalence/ab.sh php          # legacy only
tools/schedule-equivalence/ab.sh python       # python only
AB_UNTIL="2027-12-31 00:00:00" tools/schedule-equivalence/ab.sh
```

What it does, step by step, each step bounded by a timeout and printed:

1. `seed.sql` - wipe the schedule tables and write the fixture shows.
2. `run-php.php` in the legacy container - the legacy builder populates the
   calendar until `AB_UNTIL`.
3. `dump.sh php` - the rows it wrote, as CSV, into `out/php/`.
4. `seed.sql` again - the same state, including sequence values.
5. `run-py.py` in the API container - the Python builder, same horizon.
6. `dump.sh python`, then `compare.py` - diff of the two dumps.
7. `seed.sql` once more, leaving the database seeded instead of half populated.

Exit code is non zero when anything differs, and both run scripts fail loudly
when the `shows_populated_until` preference did not advance: the entry points
log and roll back instead of raising, so a run that generated nothing would
otherwise look like a successful one.

## Containers

| variable | default | used for |
| --- | --- | --- |
| `AB_LEGACY_CONTAINER` | `libretime-legacy-1` | PHP builder |
| `AB_PY_CONTAINER` | `rt-sched-dev` | Python builder |
| `AB_DB_CONTAINER` | `libretime-postgres-1` | seeding and dumps |

The Python side needs `psql`-reachable settings: `run-py.py` runs with
`DJANGO_SETTINGS_MODULE=libretime_api.settings.prod` and
`LIBRETIME_CONFIG_FILEPATH=/tmp/ab-config.yml`, where that file points at the
same database as the rest of the stack:

```yaml
general:
  public_url: http://nginx:8080
  api_key: some_secret_api_key
  secret_key: some_secret_key
database:
  host: postgres
rabbitmq:
  host: rabbitmq
playout:
  liquidsoap_host: liquidsoap
liquidsoap:
  server_listen_address: 0.0.0.0
stream:
  inputs: {}
  outputs: {}
```

## The fixture shows

`seed.sql` covers every repeat rule plus the cases where the legacy date
handling is not obvious. Each row of the fixture set is a real risk that was
found by reading and probing the PHP, not a synthetic edge case:

| show | rule | why it is in here |
| --- | --- | --- |
| A | weekly, UTC | baseline |
| B | weekly, Europe/Berlin 21:00 | instances keep the local wall clock, so their UTC instant moves by an hour across the transition |
| C | bi weekly, New York, `last_show` set | cut at the end date rather than the horizon |
| D | quad weekly, Tokyo | 28 day interval, far east offset |
| E | tri weekly, Berlin 02:30 | occurrences that land inside the spring forward gap |
| F | monthly, the 31st | months without 31 days are skipped |
| G | monthly, the 29th | February is skipped, both transitions crossed |
| H | monthly first Monday, New York | the occurrence is computed in UTC, so these instances air on Sunday evening local time |
| I | monthly fifth Thursday | months without a fifth Thursday are skipped |
| J | weekly, recorded, two rebroadcasts | same-show instances linked by `instance_id`, offsets and next-day rebroadcasts |
| K | weekly, linked show, 3 item template | content copied into every instance with crossfade overlap, subsecond clip lengths, `update_schedule_status` |
| L | weekly on two days of the same show | one show, two repeat rules |
| M | non repeating | must produce nothing on this path |

## What is compared

*   `out/*/instances.csv` every generated instance: id, show, `starts`, `ends`,
description, record, rebroadcast, `modified_instance`, `autoplaylist_built`,
`time_filled`, and the `instance_id` link.
*   `out/*/show_days.csv` the `next_pop_date` the builder writes back per repeating
day, which decides where the next run starts.
*   `out/*/schedule.csv` the copied items of the linked show, keyed by instance and
position: boundaries, clip length, fades, cues, status.
*   `out/*/prefs.csv` the `shows_populated_until` value written at the end.

The `id` column of instances is compared on purpose: equal ids mean the rows
were created in the same order.

Excluded, because they differ for a reason that is not a behavioural
difference: `cc_show_instances.created` and `last_scheduled` hold the wall
clock of whichever run happened, and `cc_schedule.id` follows the insert order
of a multi row statement, so the schedule rows are keyed by instance instead.

The harness owns the schedule tables while it runs and leaves the fixture shows
behind afterwards. To hand the database back to the application:

```bash
docker exec -i libretime-postgres-1 psql -U libretime -d libretime     < tools/schedule-equivalence/clean.sql
```

Without the `shows_populated_until` preference the next request populates the
calendar from now on, exactly like a fresh install.

## Probing the legacy side

`probe-date-semantics.php`, `probe-criteria.php` and `probe-date-edge.php` print
what the legacy service and PHP itself do for the tricky cases - `DatePeriod`
iteration across a transition, nonexistent local times, the monthly weekday
rule, the criteria the show day query really renders. They were used to derive
`services/_datetime.py` and are kept because they answer "is Python wrong or is
PHP weirder than expected" without reading the port.

```bash
docker cp tools/schedule-equivalence/probe-date-semantics.php \
    libretime-legacy-1:/tmp/ && docker exec libretime-legacy-1 php \
    /tmp/probe-date-semantics.php
```
