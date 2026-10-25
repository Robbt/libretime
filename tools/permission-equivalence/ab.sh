#!/usr/bin/env bash
#
# Permission equivalence harness: seeds a fixture station, asks the legacy
# application and the api who may change or delete each object, and compares.
#
#   ab.sh            both sides and compare
#   ab.sh php        legacy side only
#   ab.sh python     api side only
#
# Two environments, same 336 questions (see tools/EQUIVALENCE.md):
#
#   AB_MODE=container  the development stack: postgres, legacy and the api venv are
#                      three running containers.
#   AB_MODE=local      how CI runs it, through tools/equivalence-run.sh, which provides
#                      the database, the one config file both sides read, and the
#                      interpreters.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out"

ab_mode="${AB_MODE:-container}"

php_container="${AB_LEGACY_CONTAINER:-libretime-legacy-1}"
py_container="${AB_PY_CONTAINER:-rt-auth-dev}"
db_container="${AB_DB_CONTAINER:-libretime-postgres-1}"

legacy_root="${EQ_LEGACY_ROOT:-$here/../../legacy}"
api_root="${EQ_API_ROOT:-$here/../../api}"
py="${EQ_PY:-python3}"
php="${EQ_PHP:-php}"

mode="${1:-all}"

step() { printf '\n--- %s\n' "$1"; }

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

    # Both sides read cases.txt out of this directory. Container mode has to copy it to
    # /tools because it copies the script to /tmp as well, which is why run-py.py takes
    # the directory from AB_FIXTURES; php resolves it against __DIR__ and needs nothing.
    run_php() {
        mkdir -p "$EQ_OUT/logs"
        timeout 300 env LEGACY_ROOT="$(cd "$legacy_root" && pwd)" \
            LIBRETIME_LOG_DIR="$EQ_OUT/logs" \
            "$php" "$here/run-php.php" > "$out/php"

        wc -l < "$out/php" | xargs echo "  verdicts:"
  }

    run_py() {
        timeout 300 env DJANGO_SETTINGS_MODULE=libretime_api.settings.prod \
            AB_FIXTURES="$here" \
            sh -c 'cd "$1" && timeout 240 "$2" "$3"' _ "$(cd "$api_root" && pwd)" "$py" "$here/run-py.py" \
      > "$out/python"

        wc -l < "$out/python" | xargs echo "  verdicts:"
  }
else
    psql() {
        timeout 120 docker exec -i "$db_container" psql -U libretime -d libretime \
            -v ON_ERROR_STOP=1 -q "$@"
  }

    run_php() {
        # The legacy code runs from this checkout rather than from whatever the image
        # was built from; the guard below proves which file answered.
        legacy_image="$(docker inspect "$php_container" --format '{{.Image}}')"
        app_dir="$(cd "$here/../../legacy/application" && pwd)"
        want="$(md5sum "$app_dir/models/User.php" | cut -d' ' -f1)"

        timeout 60 docker exec "$php_container" cat /etc/libretime/config.yml \
      > "$out/legacy-config.yml"

        timeout 300 docker run --rm --network libretime_default \
            -v "$app_dir:/var/www/html/application:ro" \
            -v "$here:/tools:ro" \
            -v "$out/legacy-config.yml:/etc/libretime/config.yml:ro" \
            -e LIBRETIME_CONFIG_FILEPATH=/etc/libretime/config.yml \
            "$legacy_image" sh -c \
            "md5sum /var/www/html/application/models/User.php | cut -d' ' -f1 | grep -q '$want' || { echo 'mounted application code does not match this checkout' >&2; exit 1; }; php /tools/run-php.php" \
      > "$out/php"

        wc -l < "$out/php" | xargs echo "  verdicts:"
  }

    run_py() {
        timeout 60 docker exec -i "$py_container" sh -c 'cat > /tmp/run-py-permissions.py' \
      < "$here/run-py.py"
        # The same cases.txt path as on the php side, so the two cannot disagree
        # about what was covered.
        timeout 30 docker exec "$py_container" mkdir -p /tools
        timeout 60 docker exec -i "$py_container" sh -c 'cat > /tools/cases.txt' < "$here/cases.txt"
        timeout 300 docker exec \
            -e DJANGO_SETTINGS_MODULE=libretime_api.settings.prod \
            -e LIBRETIME_CONFIG_FILEPATH=/tmp/ab-config.yml \
            -e AB_FIXTURES=/tools \
            "$py_container" sh -c 'cd /srv/src/api && timeout 240 .venv/bin/python /tmp/run-py-permissions.py' \
      > "$out/python"

        wc -l < "$out/python" | xargs echo "  verdicts:"
  }
fi

mkdir -p "$out"

if [[ "$mode" == "all" || "$mode" == "php" ]]; then
    step "seed fixtures"
    psql -f $here/seed.sql

    step "ask the legacy application"
    run_php
fi

if [[ "$mode" == "all" || "$mode" == "python" ]]; then
    step "re-seed fixtures"
    psql -f $here/seed.sql

    step "ask the api"
    run_py
fi

if [[ "$mode" == "all" ]]; then
    step "compare"
    "$py" "$here/compare.py" "$out/php" "$out/python"
fi

step "clean up fixtures"
psql -f $here/clean.sql
