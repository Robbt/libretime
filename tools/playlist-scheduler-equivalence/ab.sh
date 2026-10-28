#!/usr/bin/env bash
#
# Playlist scheduler equivalence harness: seeds instances and playlists, schedules
# them with the legacy builder and with the Python port, and compares the rows.
#
#   ab.sh            both sides and compare
#   ab.sh php        legacy side only
#   ab.sh python     python side only
#
# Two environments, same scenario (see tools/EQUIVALENCE.md):
#
#   AB_MODE=container  the development stack: postgres, legacy and the api venv are
#                      three running containers.
#   AB_MODE=local      everything on this host — how CI runs it, through
#                      tools/equivalence-run.sh, which provides the database, the
#                      shared config file and the interpreters.
#
# The seeding happens inside each branch below rather than once at the top: both sides
# must start from the identical fixture state, not from whatever the other side left
# behind. The legacy builder writes into cc_schedule and updates time_filled, so a python
# run on the leftovers would differ for reasons that have nothing to do with the port.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out"

ab_mode="${AB_MODE:-container}"

php_container="${AB_LEGACY_CONTAINER:-libretime-legacy-1}"
py_container="${AB_PY_CONTAINER:-rt-playlists-dev}"
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

    run_php() {
        # Runs this checkout's application code by construction — container mode has to
        # mount over an image to get that, and verify the mount took.
        mkdir -p "$EQ_OUT/logs"
        timeout 300 env LEGACY_ROOT="$(cd "$legacy_root" && pwd)" \
            LIBRETIME_LOG_DIR="$EQ_OUT/logs" \
            "$php" "$here/run-php.php"
  }

    run_py() {
        timeout 300 env DJANGO_SETTINGS_MODULE=libretime_api.settings.prod \
            sh -c 'cd "$1" && "$2" "$3"' _ "$(cd "$api_root" && pwd)" "$py" "$here/run_python_playlist_scheduler.py"
  }
else
    psql() {
        timeout 120 docker exec -i "$db_container" psql -U libretime -d libretime \
            -v ON_ERROR_STOP=1 -q "$@"
  }

    run_php() {
        # The legacy code runs from this checkout, mounted over the image's
        # application directory, so the comparison is against the code under review
        # rather than whatever the image was built from.
        legacy_image="$(docker inspect "$php_container" --format '{{.Image}}')"
        app_dir="$(cd "$here/../../legacy/application" && pwd)"

        timeout 60 docker exec "$php_container" cat /etc/libretime/config.yml \
      > "$out/legacy-config.yml"

        timeout 300 docker run --rm --network libretime_default \
            -v "$app_dir:/var/www/html/application:ro" \
            -v "$here:/tools:ro" \
            -v "$out/legacy-config.yml:/etc/libretime/config.yml:ro" \
            -e LIBRETIME_CONFIG_FILEPATH=/etc/libretime/config.yml \
            "$legacy_image" php /tools/run-php.php
  }

    run_py() {
        timeout 60 docker exec -i "$py_container" sh -c 'cat > /tmp/run-py-schedule.py' \
      < "$here/run_python_playlist_scheduler.py"
        timeout 300 docker exec \
            -e DJANGO_SETTINGS_MODULE=libretime_api.settings.prod \
            -e LIBRETIME_CONFIG_FILEPATH=/tmp/ab-config.yml \
            "$py_container" sh -c 'cd /srv/src/api && .venv/bin/python /tmp/run-py-schedule.py'
  }
fi

mkdir -p "$out"

if [[ "$mode" == "all" || "$mode" == "php" ]]; then
    step "seed fixtures"
    psql -f $here/seed.sql

    step "run legacy builder"
    run_php

    step "dump legacy rows"
    "$here/dump.sh" php
fi

if [[ "$mode" == "all" || "$mode" == "python" ]]; then
    step "re-seed fixtures"
    psql -f $here/seed.sql

    step "run python builder"
    run_py

    step "dump python rows"
    "$here/dump.sh" python
fi

if [[ "$mode" == "all" ]]; then
    step "compare"
    "$py" "$here/compare_playlist_scheduler.py" "$out/php" "$out/python"
fi
