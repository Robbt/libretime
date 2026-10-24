#!/usr/bin/env bash
#
# Equivalence harness for the schedule builder port.
#
# Seeds a fixture state, runs the legacy PHP builder over it, dumps the rows,
# restores the very same state, runs the Python builder over it, dumps again and
# compares. Every step is bounded by a timeout and prints what it did, so a
# stuck builder fails the run instead of running forever.
#
#   ab.sh            full run: both builders, then compare
#   ab.sh php        seed + legacy builder + dump
#   ab.sh python     seed + python builder + dump
#
# Requires the live stack (postgres, legacy). The python side runs in the
# schedule development container, see README.md.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out"

# Both builders populate a full year.
until="${AB_UNTIL:-2026-12-31 00:00:00}"

legacy_container="${AB_LEGACY_CONTAINER:-libretime-legacy-1}"
py_container="${AB_PY_CONTAINER:-rt-sched-dev}"
db_container="${AB_DB_CONTAINER:-libretime-postgres-1}"

mode="${1:-all}"

step() { printf '\n--- %s\n' "$1"; }

psql() {
    timeout 120 docker exec -i "$db_container" psql -U libretime -d libretime \
        -v ON_ERROR_STOP=1 -q "$@"
}

# seed.sql is the reset mechanism: it deletes every row the harness owns and
# re-inserts them with explicit ids, then resets the sequences, so both builders
# start from state that differs in nothing but the wall clock.
seed() {
    psql < "$here/seed.sql"
    psql -tAc "select 'shows: '||count(*) from cc_show"
    psql -tAc "select 'show days: '||count(*) from cc_show_days"
}

mkdir -p "$out"

step "seed fixtures"
seed

if [[ "$mode" == "all" || "$mode" == "php" ]]; then
    step "run legacy builder"
    docker cp "$here/run-php.php" "$legacy_container:/tmp/run-php.php" > /dev/null
    timeout 300 docker exec -e AB_UNTIL="$until" "$legacy_container" php /tmp/run-php.php

    step "dump legacy rows"
    "$here/dump.sh" php
fi

if [[ "$mode" == "all" || "$mode" == "python" ]]; then
    step "re-seed fixture state"
    seed

    step "run python builder"
    docker cp "$here/run-py.py" "$py_container:/tmp/run-py.py" > /dev/null
    timeout 300 docker exec \
        -e DJANGO_SETTINGS_MODULE=libretime_api.settings.prod \
        -e LIBRETIME_CONFIG_FILEPATH=/tmp/ab-config.yml \
        -e AB_UNTIL="$until" \
        "$py_container" sh -c 'cd /srv/src/api && .venv/bin/python /tmp/run-py.py'

    step "dump python rows"
    "$here/dump.sh" python
fi

# Leave the database seeded rather than half populated.
step "re-seed fixture state"
seed

if [[ "$mode" == "all" ]]; then
    step "compare"
    python3 "$here/compare.py" "$out/php" "$out/python"
fi
