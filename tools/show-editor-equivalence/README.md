# Show editor equivalence harness

Runs the fixture scenarios from the legacy show-editor unit tests
(`legacy/tests/application/services/database/ShowServiceDbTest.php`) against the
legacy `Application_Service_ShowService`, against the python port, and against the
expectation files the project already commits.

    ./ab.sh            legacy, python, compare both ways
    ./ab.sh php        legacy side only
    ./ab.sh python     python side only
    ./ab.sh spec       legacy side against the committed expectations

### In CI

Same scenario, no containers. `run.sh` sets `AB_MODE=local`, and
`tools/equivalence-run.sh` (see `tools/EQUIVALENCE.md`) provides what the container mode
gets from the development stack: a database built by the api's own migrations, one config
file both sides read, and a RabbitMQ broker.

```bash
tools/equivalence-run.sh tools/show-editor-equivalence
```

On a workstation that means php 8.2 with `bcmath`, `yaml` and `pdo_pgsql`,
`make -C legacy vendor` in this checkout, `make -C api install`, and a postgres and a
broker the run can reach:

```bash
docker run -d --name eq-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:15
docker exec eq-pg psql -U postgres -c "CREATE USER libretime WITH PASSWORD 'libretime' CREATEDB;"
EQ_DB_SUPER_PASSWORD=postgres tools/equivalence-run.sh tools/show-editor-equivalence
```

One difference worth knowing before reading a failure: container mode clones the running
station's schema, local mode builds one from the api's migrations, and some constraints
never reached long-upgraded databases — which is where the cascades below went missing.
The ledger only excuses a difference that actually occurs, so the harness passes in both
environments: on a migrated schema php's own deletes cascade too, all 20 scenarios come
out identical, and the 18 that have upstream datasets match them as well.

## Why three legs

The python port agreeing with php would, on its own, only prove that both do the
same thing. The datasets under
`legacy/tests/application/services/database/datasets/` are the project's own
statement of what the editor is *supposed* to leave behind, so the legacy run is
checked against them first: `./ab.sh spec`. If that leg is not clean, the
comparison below it is comparing the wrong behaviour.

## Pieces

| file | role |
| --- | --- |
| `dump-scenarios.php` | prints `scenarios.json` from `ShowServiceData`, php's own payload fixtures, plus the per-scenario overrides and call sequence transcribed from the unit test — the part worth reviewing, because it is the only hand-written link to the test file |
| `scenarios.json` | what that produced; both sides read it, so they cannot disagree about what was covered |
| `seed.sql` | the fixture state of `seed_show_service.yml`: two preferences |
| `clean.sql` | empties the database and restarts the sequences |
| `run-php.php` | runs each scenario through the legacy service and dumps the six tables it writes |
| `run-py.py` | the same through the python port, including the same seed inserts |
| `compare.py` | row and column level diffing, in two modes |

Rows are dumped as `to_jsonb(...)::text` by both sides rather than as column lists:
`pdo_pgsql` returns a boolean as `"1"` or `""`, which turns "is the port correct"
into "who guessed the column type right". Letting Postgres render the row removes
that class of noise, and the two remaining normalizations in `compare.py` — a `T`
between date and time, `01:00` versus `01:00:00` — are formatting, each commented
where it is applied.

## Determinism

The fixture sets `shows_populated_until = 2044-02-07`, which is what the legacy
tests do and why this can be compared at all: instance creation stops at that date
rather than at "now", so both sides build the same six weekly instances, and the
delete scenarios can name instances by id — `deleteShow(3, true)` — because the
sequences restart with every scenario.

## Its own database

`ab.sh` creates `show_editor_harness` from the live schema and drops it at the end
of a full run. Not optional: `addUpdateShow` writes the global
`shows_populated_until` preference, and a station that woke up believing its
calendar was built to 2044 would stop populating shows. That is a self-inflicted
outage, not a test.

## Covered

20 scenarios, which is every dataset the legacy suite commits, and every service
method the editor reaches for:

- **create** — each repeat kind (none, weekly, bi-, tri-, quad-weekly, monthly on a
  date, monthly on a weekday), with an end date, with three repeat days, linked, and
  recorded with ten rebroadcasts.
- **delete** — one instance, an instance and all following, a whole show.
- **edit** — set an end date on an open-ended show, stop a show repeating, change its
  interval, add a repeat day, remove one, move one night out of the sequence, and
  build a show further into the calendar ("load more").

All 20 run on both sides and the rows are identical, with three documented
divergences — `compare.py` prints each one, with its reason, on every green run.

Two things the scenario list does not duplicate:

`test_changeRepeatDayUpdatesScheduleCorrectly` and
`test_removeFirstRepeatShowDayUpdatesScheduleCorrectly` are the same test. Not
similar: byte-identical bodies apart from their names, and byte-identical datasets.
One scenario here covers both, and the comment in `dump-scenarios.php` says so.

`test_unlinkLinkedShow.yml` is a dataset with no test. Nothing in the suite
references it, so its scenario cannot be transcribed without inventing the steps
that produced it — and inventing them, then comparing to it, would test the guess.

## The missing cascades, seen from both sides

`cc_show_days.show_id`, `cc_show_instances.show_id` and `cc_schedule.instance_id`
have no foreign key in the shipped schema. Only `cc_show_hosts` has one, with
`ON DELETE CASCADE`. So the legacy service's row deletions leave children behind:

    [delete_whole_show]            cc_show_days, cc_show_instances
    [edit_remove_first_repeat_day] cc_schedule

In the last one, an item stays queued in an hour that no longer exists, pointing at
an instance id that no longer exists — and the file keeps its `is_scheduled` lock, so
the library goes on refusing to move it.

The legacy suite cannot see any of this: `TestHelper::installTestDatabase()` builds
its database from the installer SQL, which has the cascades, and every dataset
therefore states that the children go. Upstream's expectation files are right and
the production schema is what is missing. The port deletes the children explicitly,
which makes it the side that agrees with the committed intent, and each of the three
is named in `compare.py`'s `DOCUMENTED` table rather than being quietly normalised
away.

## Seeding an item, and why not with the scheduler

`edit_remove_first_repeat_day` needs content inside an instance, because the point of
the scenario is what an edit does to it. The upstream test drives
`Application_Model_Scheduler::scheduleAfter()` to get it there; both legs of this
harness insert the rows directly instead — `seedScheduledItem()` in `run-php.php` and
`seed_scheduled_item()` in `run-py.py`, kept side by side on purpose. Going through
the scheduler would test the scheduler (whose port lives on another branch) rather
than the editor, and would make a difference between two schedulers show up as a
difference between two show editors.

The instance the item belongs to is passed by id in the scenario — `deleteShow(3,
true)` works the same way — because the harness database restarts its sequences per
scenario, which is also what lets the legacy tests name rows at all.
