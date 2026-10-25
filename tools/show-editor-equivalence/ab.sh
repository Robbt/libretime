#!/usr/bin/env bash
#
# Show editor equivalence harness.
#
# Three-way by design: the legacy service runs the scenarios the legacy unit tests
# describe; that output is checked against the expected datasets committed upstream
# (tests/application/services/database/datasets/*.yml); and the python port has to
# agree with the legacy run. The middle leg is what makes the rest worth anything -
# without it, the port agreeing with php would only prove both are wrong the same
# way, and the upstream datasets are the only statement of intent in the triangle.
#
#   ab.sh            php, python, compare, and the upstream check
#   ab.sh php        legacy side only
#   ab.sh python     python side only
#   ab.sh spec       legacy side against the upstream datasets
#
# Two environments, same scenarios (see tools/EQUIVALENCE.md):
#
#   AB_MODE=container  the development stack. Builds its own database by cloning the
#                      schema of the running station, and reaches php, python and psql
#                      through containers.
#   AB_MODE=local      how CI runs it, through tools/equivalence-run.sh, which has
#                      already built a database from the repository's installer SQL
#                      and written the one config file both sides read.
#
# Runs against its own database either way. The legacy editor writes the global
# shows_populated_until preference, and a station that woke up believing its calendar
# was already built to 2044 would stop populating shows - which is a self-inflicted
# outage, not a test.
#
# The two modes do not start from the identical schema, and that is worth knowing
# before reading a failure: cloning a station's schema carries the constraints that
# station accumulated, while the installer SQL is what a new install gets. Some
# upgrades never added their foreign key, so on a station cc_show_days.show_id and
# cc_schedule.instance_id have none, and the installer schema cascades
# cc_show_instances.show_id. The port deletes its children explicitly and is therefore
# correct under both; compare.py's ledger is one-directional (a documented difference
# that does not occur is not a failure), so neither environment is wrong here.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out"

ab_mode="${AB_MODE:-container}"

db_container="${AB_DB_CONTAINER:-libretime-postgres-1}"
php_container="${AB_LEGACY_CONTAINER:-libretime-legacy-1}"
py_container="${AB_PY_CONTAINER:-rt-auth-dev}"

harness_db="${AB_HARNESS_DB:-show_editor_harness}"
live_db="${AB_LIVE_DB:-libretime}"

legacy_root="${EQ_LEGACY_ROOT:-$here/../../legacy}"
# The scenario payloads are the legacy unit tests' own (ShowServiceData.php), so the
# fixture is upstream's rather than invented here.
tests_dir="$(cd "$here/../../legacy/tests" && pwd)"
api_root="${EQ_API_ROOT:-$here/../../api}"
py="${EQ_PY:-python3}"
php="${EQ_PHP:-php}"

mode="${1:-all}"

step() { printf '\n--- %s\n' "$1"; }

mkdir -p "$out"

if [ "$ab_mode" = "local" ]; then
    : "${EQ_DB_NAME:?AB_MODE=local needs EQ_DB_NAME (set by tools/equivalence-run.sh)}"
    : "${LIBRETIME_CONFIG_FILEPATH:?AB_MODE=local needs the shared config file}"
    [ -f "$legacy_root/vendor/autoload.php" ] || {
        echo "legacy dependencies not installed: run make -C legacy vendor" >&2
        exit 1
    }

    psql() {
        timeout 120 env PGPASSWORD="$EQ_DB_PASSWORD" psql -h "$EQ_DB_HOST" \
            -p "$EQ_DB_PORT" -U "$EQ_DB_USER" -d "$EQ_DB_NAME" \
            -v ON_ERROR_STOP=1 -q "$@"
    }

    # Nothing to build and nothing to drop: the driver created the database before
    # calling this and removes it afterwards, which is also how a CI runner gets a
    # schema without having a station to copy one from.
    prepare_database() { psql -f "$here/clean.sql" >/dev/null; }
    drop_database() { printf '  left to tools/equivalence-run.sh (%s)\n' "$EQ_DB_NAME"; }

    # No md5 guard, and that is not an omission: container mode mounts this checkout
    # over an image built from another commit, so it has to prove the mount took.
    # Here the checkout *is* the code path, with no image in between.
    run_php() {
        mkdir -p "$EQ_OUT/logs"
        timeout 300 env LEGACY_ROOT="$(cd "$legacy_root" && pwd)" \
            LEGACY_TESTS_DIR="$tests_dir" \
            AB_FIXTURES="$here" \
            LIBRETIME_LOG_DIR="$EQ_OUT/logs" \
            "$php" "$here/run-php.php" >"$out/php"
    }

    run_py() {
        timeout 300 env DJANGO_SETTINGS_MODULE=libretime_api.settings.prod \
            AB_FIXTURES="$here" \
            sh -c 'cd "$1" && timeout 240 "$2" "$3"' _ "$(cd "$api_root" && pwd)" "$py" "$here/run-py.py" \
            >"$out/python"
    }
else
    psql() {
        timeout 120 docker exec -i "$db_container" psql -U libretime -d libretime \
            -v ON_ERROR_STOP=1 -q "$@"
    }

    prepare_database() {
        step "harness database ($harness_db)"
        # Rebuilt every run rather than reused: loading the schema into a database that
        # already has it fails on every constraint, and a run that prints errors and then
        # carries on is a run nobody trusts.
        timeout 60 docker exec "$db_container" psql -U libretime -d postgres -q \
            -c "DROP DATABASE IF EXISTS $harness_db" >/dev/null
        timeout 60 docker exec "$db_container" createdb -U libretime "$harness_db"

        # Schema only: an empty station, so instance ids start at 1 the way the legacy
        # tests assume, and none of the real station's preferences are in reach.
        timeout 180 docker exec "$db_container" sh -c \
            "pg_dump -U libretime -d $live_db --schema-only | psql -U libretime -d $harness_db -q" \
            >/dev/null
        psql_harness -f $here/clean.sql

        # Both services read the same config file format, so both get one pointing at
        # the harness database. The station's own config does not name a database at
        # all - the default is what it uses - so the name has to be written in, not
        # substituted.
        timeout 60 docker exec "$php_container" cat /etc/libretime/config.yml >"$out/config.base.yml"

        python3 - "$out" "$harness_db" <<'PY'
import pathlib
import re
import sys

out, db = sys.argv[1], sys.argv[2]
base = pathlib.Path(out, "config.base.yml").read_text()

if re.search(r"^database:\s*$", base, re.M):
    base = re.sub(r"^database:\s*$", f"database:\n  name: {db}", base, count=1, flags=re.M)
elif re.search(r"^database:\s*name:", base, re.M):
    base = re.sub(r"(^database:\s*name:\s*).*", rf"\g<1>{db}", base, count=1, flags=re.M)
else:
    sys.exit("the legacy config has no 'database:' section; its format changed")

pathlib.Path(out, "legacy-config.yml").write_text(base)
pathlib.Path(out, "api-config.yml").write_text(base)
print(f"  configs point at {db}")
PY
    }

    psql_harness() {
        timeout 120 docker exec -i "$db_container" psql -U libretime -d "$harness_db" \
            -v ON_ERROR_STOP=1 -q "$@"
    }

    drop_database() {
        step "drop the harness database"
        timeout 60 docker exec "$db_container" psql -U libretime -d postgres -q \
            -c "DROP DATABASE IF EXISTS $harness_db" >/dev/null && echo "  dropped $harness_db"
    }

    run_php() {
        legacy_image="$(docker inspect "$php_container" --format '{{.Image}}')"
        app_dir="$(cd "$here/../../legacy/application" && pwd)"
        want="$(md5sum "$here/../../legacy/application/services/ShowService.php" | cut -d' ' -f1)"

        timeout 300 docker run --rm --network libretime_default \
            -v "$app_dir:/var/www/html/application:ro" \
            -v "$tests_dir:/var/www/tests:ro" \
            -v "$here:/tools:ro" \
            -v "$out/legacy-config.yml:/etc/libretime/config.yml:ro" \
            -e LIBRETIME_CONFIG_FILEPATH=/etc/libretime/config.yml \
            "$legacy_image" sh -c \
            "md5sum /var/www/html/application/services/ShowService.php | cut -d' ' -f1 \
               | grep -q '$want' || { echo 'mounted application code is not this checkout' >&2; exit 1; } \
             ; php /tools/run-php.php" \
            >"$out/php"
    }

    run_py() {
        for f in run-py.py scenarios.json clean.sql seed.sql; do
            timeout 60 docker exec -i "$py_container" sh -c "cat > /tools/$f" <"$here/$f"
        done
        timeout 60 docker exec -i "$py_container" sh -c 'cat > /tmp/ab-harness-config.yml' <"$out/api-config.yml"

        timeout 300 docker exec \
            -e DJANGO_SETTINGS_MODULE=libretime_api.settings.prod \
            -e LIBRETIME_CONFIG_FILEPATH=/tmp/ab-harness-config.yml \
            "$py_container" sh -c "cd /srv/src/api && timeout 240 .venv/bin/python /tools/run-py.py" \
            >"$out/python"
    }
fi

prepare_database

if [[ "$mode" == "all" || "$mode" == "php" || "$mode" == "spec" ]]; then
    step "legacy service"
    run_php

    grep -c '^cc_' "$out/php" | xargs echo "  rows:"
fi

if [[ "$mode" == "spec" ]]; then
    step "compare against the datasets committed upstream"
    status=0
    "$py" "$here/compare.py" --spec "$out/php" || status=$?
    drop_database
    exit "$status"
fi

if [[ "$mode" == "all" || "$mode" == "python" ]]; then
    if [[ ! -f "$here/run-py.py" ]]; then
        echo "no python side yet: run-py.py lands with the show editor port." >&2
        echo "Until then use: ./ab.sh php   or   ./ab.sh spec" >&2
        exit 3
    fi

    step "python port"
    run_py

    grep -c '^cc_' "$out/python" | xargs echo "  rows:"
fi

if [[ "$mode" == "all" ]]; then
    step "compare the two runs"
    status=0
    "$py" "$here/compare.py" "$out/php" "$out/python" || status=$?

    step "compare the legacy run against the upstream datasets"
    spec_status=0
    "$py" "$here/compare.py" --spec "$out/php" || spec_status=$?

    drop_database

    if [ $status -ne 0 ] || [ $spec_status -ne 0 ]; then
        exit 1
    fi
    exit 0
fi

drop_database
