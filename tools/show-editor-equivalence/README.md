# Show editor equivalence harness

Runs the fixture scenarios from the legacy show-editor unit tests
(`legacy/tests/application/services/database/ShowServiceDbTest.php`) against the
legacy `Application_Service_ShowService`, against the python port, and against the
expectation files the project already commits.

    ./ab.sh            legacy, python, compare both ways
    ./ab.sh php        legacy side only
    ./ab.sh python     python side only
    ./ab.sh spec       legacy side against the committed expectations

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
| `run-php.php` | runs each scenario through the legacy service and dumps five tables |
| `run-py.py` | the same through the python port — **lands with the port**, not yet here; until then `ab.sh` runs the `php` and `spec` legs only |
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

## Covered, and what is not

14 scenarios: create with every repeat kind (none, weekly, bi-, tri-, quad-weekly,
monthly-on-a-date, monthly-on-a-weekday), with an end date, with three repeat days,
linked, recorded with rebroadcasts; and delete (one instance, a whole show,
several instances and all following).

The edit scenarios in the same legacy test — editing a repeating instance, changing
a repeat day or repeat type, which also move the content in `cc_schedule` — need
the update path of the port, and are added with it.

## A difference that is not the port's

Against upstream's own dataset, `delete_whole_show` disagrees in production:

    [delete_whole_show] cc_show_days: 0 rows expected, 1 produced
    [delete_whole_show] cc_show_instances: 0 rows expected, 6 produced

`deleteShowInstances` soft-deletes instances (`modified_instance = true`) and
removes the `cc_show` row when none are left standing, relying on the database to
take the day rules and instance rows with it. In the shipped schema nothing does:
`cc_show_days.show_id` and `cc_show_instances.show_id` have no foreign key at all —
only `cc_show_hosts` has one, `ON DELETE CASCADE`. The legacy test passes because
`TestHelper::installTestDatabase()` builds its database from the installer SQL,
which does cascade; the schema an actual station upgrades to does not.

So the test suite and production disagree, and deleting a whole show leaves
orphaned day rules and instance rows on a real station. Reported upstream.

Decided, not yet built: when the port gets its delete path it will remove those
rows explicitly, matching the intent the committed dataset states rather than the
accident of a missing constraint — and `compare.py` will carry it as a named
expected divergence, so the choice stays visible instead of quietly making the two
sides differ.
