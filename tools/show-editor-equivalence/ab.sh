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
# Runs against its own database, created and dropped here. The legacy editor writes
# the global shows_populated_until preference, and a station that woke up believing
# its calendar was already built to 2044 would stop populating shows - which is a
# self-inflicted outage, not a test.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out"

db_container="${AB_DB_CONTAINER:-libretime-postgres-1}"
php_container="${AB_LEGACY_CONTAINER:-libretime-legacy-1}"
py_container="${AB_PY_CONTAINER:-rt-auth-dev}"

harness_db="${AB_HARNESS_DB:-show_editor_harness}"
live_db="${AB_LIVE_DB:-libretime}"

mode="${1:-all}"

step() { printf '\n--- %s\n' "$1"; }

mkdir -p "$out"

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
timeout 120 docker exec -i "$db_container" psql -U libretime -d "$harness_db" \
    -v ON_ERROR_STOP=1 -q < "$here/clean.sql"

# Both services read the same config file format, so both get one pointing at the
# harness database. The station's own config does not name a database at all - the
# default is what it uses - so the name has to be written in, not substituted.
timeout 60 docker exec "$php_container" cat /etc/libretime/config.yml > "$out/config.base.yml"

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

if [[ "$mode" == "all" || "$mode" == "php" || "$mode" == "spec" ]]; then
    step "legacy service"
    legacy_image="$(docker inspect "$php_container" --format '{{.Image}}')"
    app_dir="$(cd "$here/../../legacy/application" && pwd)"
    tests_dir="$(cd "$here/../../legacy/tests" && pwd)"
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
        > "$out/php"

    grep -c '^cc_' "$out/php" | xargs echo "  rows:"
fi

if [[ "$mode" == "spec" ]]; then
    step "compare against the datasets committed upstream"
    python3 "$here/compare.py" --spec "$out/php"
    exit $?
fi

if [[ "$mode" == "all" || "$mode" == "python" ]]; then
    if [[ ! -f "$here/run-py.py" ]]; then
        echo "no python side yet: run-py.py lands with the show editor port." >&2
        echo "Until then use: ./ab.sh php   or   ./ab.sh spec" >&2
        exit 3
    fi

    step "python port"
    for f in run-py.py scenarios.json clean.sql seed.sql; do
        timeout 60 docker exec -i "$py_container" sh -c "cat > /tools/$f" < "$here/$f"
    done
    timeout 60 docker exec -i "$py_container" sh -c 'cat > /tmp/ab-harness-config.yml' < "$out/api-config.yml"

    timeout 300 docker exec \
        -e DJANGO_SETTINGS_MODULE=libretime_api.settings.prod \
        -e LIBRETIME_CONFIG_FILEPATH=/tmp/ab-harness-config.yml \
        "$py_container" sh -c "cd /srv/src/api && timeout 240 .venv/bin/python /tools/run-py.py" \
        > "$out/python"

    grep -c '^cc_' "$out/python" | xargs echo "  rows:"
fi

if [[ "$mode" == "all" ]]; then
    step "compare the two runs"
    python3 "$here/compare.py" "$out/php" "$out/python"

    step "compare the legacy run against the upstream datasets"
    python3 "$here/compare.py" --spec "$out/php"
fi

step "drop the harness database"
timeout 60 docker exec "$db_container" psql -U libretime -d postgres -q \
    -c "DROP DATABASE IF EXISTS $harness_db" >/dev/null && echo "  dropped $harness_db"
