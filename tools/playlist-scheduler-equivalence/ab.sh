#!/usr/bin/env bash
#
# Playlist scheduler equivalence harness: seeds instances and playlists, schedules
# them with the legacy builder and with the Python port, and compares the rows.
#
#   ab.sh            both sides and compare
#   ab.sh php        legacy side only
#   ab.sh python     python side only
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out"

php_container="${AB_LEGACY_CONTAINER:-libretime-legacy-1}"
py_container="${AB_PY_CONTAINER:-rt-playlists-dev}"
db_container="${AB_DB_CONTAINER:-libretime-postgres-1}"

mode="${1:-all}"

step() { printf '\n--- %s\n' "$1"; }

psql() {
    timeout 120 docker exec -i "$db_container" psql -U libretime -d libretime \
        -v ON_ERROR_STOP=1 -q "$@"
}

mkdir -p "$out"

if [[ "$mode" == "all" || "$mode" == "php" ]]; then
    step "seed fixtures"
    psql < "$here/seed.sql"

    step "run legacy builder"
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

    step "dump legacy rows"
    "$here/dump.sh" php
fi

if [[ "$mode" == "all" || "$mode" == "python" ]]; then
    step "re-seed fixtures"
    psql < "$here/seed.sql"

    step "run python builder"
    timeout 60 docker exec -i "$py_container" sh -c 'cat > /tmp/run-py-schedule.py' \
        < "$here/run-py.py"
    timeout 300 docker exec \
        -e DJANGO_SETTINGS_MODULE=libretime_api.settings.prod \
        -e LIBRETIME_CONFIG_FILEPATH=/tmp/ab-config.yml \
        "$py_container" sh -c 'cd /srv/src/api && .venv/bin/python /tmp/run-py-schedule.py'

    step "dump python rows"
    "$here/dump.sh" python
fi

if [[ "$mode" == "all" ]]; then
    step "compare"
    python3 "$here/compare.py" "$out/php" "$out/python"
fi
