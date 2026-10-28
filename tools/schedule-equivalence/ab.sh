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
# Two environments, same scenario (see tools/EQUIVALENCE.md):
#
#   AB_MODE=container  the default, for the development stack: postgres, legacy and
#                      the api venv are three running containers, named below.
#   AB_MODE=local      everything on this host — the way CI runs it, via
#                      tools/equivalence-run.sh, which provides EQ_DB_NAME, the shared
#                      config file and the interpreter paths. Nothing is mounted and
#                      nothing is copied into a container, because a runner has no
#                      station to reach into.
#
# The scenario is identical in both: what changes is only how the three moving parts
# are addressed. Keeping one script rather than two is deliberate — a CI harness that
# drifted from the one developers run locally would stop being the same comparison.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out"

# Both builders populate a full year.
until="${AB_UNTIL:-2026-12-31 00:00:00}"

ab_mode="${AB_MODE:-container}"

legacy_container="${AB_LEGACY_CONTAINER:-libretime-legacy-1}"
py_container="${AB_PY_CONTAINER:-rt-sched-dev}"
db_container="${AB_DB_CONTAINER:-libretime-postgres-1}"

# In local mode the repository layout is the only address available.
legacy_root="${EQ_LEGACY_ROOT:-$here/../../legacy}"
api_root="${EQ_API_ROOT:-$here/../../api}"
py="${EQ_PY:-python3}"
php="${EQ_PHP:-php}"

mode="${1:-all}"

step() { printf '\n--- %s\n' "$1"; }

if [ "$ab_mode" = "local" ]; then
    : "${EQ_DB_NAME:?AB_MODE=local needs EQ_DB_NAME (set by tools/equivalence-run.sh)}"
    : "${LIBRETIME_CONFIG_FILEPATH:?AB_MODE=local needs the shared config file}"
    [ -f "$legacy_root/application/models/Show.php" ] || {
        echo "legacy app not found at $legacy_root" >&2
        exit 1
  }
    [ -f "$legacy_root/vendor/autoload.php" ] || {
        echo "legacy dependencies not installed: run make -C legacy vendor" >&2
        exit 1
  }

    psql() {
        timeout 120 env PGPASSWORD="$EQ_DB_PASSWORD" psql -h "$EQ_DB_HOST" \
            -p "$EQ_DB_PORT" -U "$EQ_DB_USER" -d "$EQ_DB_NAME" \
            -v ON_ERROR_STOP=1 -q "$@"
  }

    run_php() {
        # run-php.php resolves the app through LEGACY_ROOT, so the checkout under test
        # is the code that runs — no mounting, and no possibility of testing the image
        # instead of the branch.
        #
        # LIBRETIME_LOG_DIR because the legacy entry point this harness calls catches
        # exceptions, rolls the transaction back and only logs a warning — and its logger
        # wants /var/log/libretime, which a CI runner does not have. Pointing it at the
        # artefacts directory both stops the harness dying inside its own error handling
        # and leaves the swallowed message where a reviewer can read it.
        mkdir -p "$EQ_OUT/logs"
        timeout 300 env LEGACY_ROOT="$(cd "$legacy_root" && pwd)" AB_UNTIL="$until" \
            LIBRETIME_LOG_DIR="$EQ_OUT/logs" \
            "$php" "$here/run-php.php"
  }

    run_py() {
        # django.setup() resolves libretime_api relative to the working directory, the
        # same way manage.py does.
        timeout 300 env DJANGO_SETTINGS_MODULE=libretime_api.settings.prod AB_UNTIL="$until" \
            sh -c 'cd "$1" && "$2" "$3"' _ "$(cd "$api_root" && pwd)" "$py" "$here/run_python_schedule.py"
  }
else
    psql() {
        timeout 120 docker exec -i "$db_container" psql -U libretime -d libretime \
            -v ON_ERROR_STOP=1 -q "$@"
  }

    run_php() {
        docker cp "$here/run-php.php" "$legacy_container:/tmp/run-php.php" > /dev/null
        timeout 300 docker exec -e AB_UNTIL="$until" "$legacy_container" php /tmp/run-php.php
  }

    run_py() {
        docker cp "$here/run_python_schedule.py" "$py_container:/tmp/run_python_schedule.py" > /dev/null
        timeout 300 docker exec \
            -e DJANGO_SETTINGS_MODULE=libretime_api.settings.prod \
            -e LIBRETIME_CONFIG_FILEPATH=/tmp/ab-config.yml \
            -e AB_UNTIL="$until" \
            "$py_container" sh -c 'cd /srv/src/api && .venv/bin/python /tmp/run_python_schedule.py'
  }
fi

# seed.sql is the reset mechanism: it deletes every row the harness owns and
# re-inserts them with explicit ids, then resets the sequences, so both builders
# start from state that differs in nothing but the wall clock.
seed() {
    psql -f $here/seed.sql
    psql -tAc "select 'shows: '||count(*) from cc_show"
    psql -tAc "select 'show days: '||count(*) from cc_show_days"
}

mkdir -p "$out"

step "seed fixtures"
seed

if [[ "$mode" == "all" || "$mode" == "php" ]]; then
    step "run legacy builder"
    run_php

    step "dump legacy rows"
    "$here/dump.sh" php
fi

if [[ "$mode" == "all" || "$mode" == "python" ]]; then
    step "re-seed fixture state"
    seed

    step "run python builder"
    run_py

    step "dump python rows"
    "$here/dump.sh" python
fi

# Leave the database seeded rather than half populated.
step "re-seed fixture state"
seed

if [[ "$mode" == "all" ]]; then
    step "compare"
    "${EQ_PY:-python3}" "$here/compare_schedule.py" "$out/php" "$out/python"
fi
