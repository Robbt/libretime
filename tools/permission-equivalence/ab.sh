#!/usr/bin/env bash
#
# Permission equivalence harness: seeds a fixture station, asks the legacy
# application and the api who may change or delete each object, and compares.
#
#   ab.sh            both sides and compare
#   ab.sh php        legacy side only
#   ab.sh python     api side only
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out"

php_container="${AB_LEGACY_CONTAINER:-libretime-legacy-1}"
py_container="${AB_PY_CONTAINER:-rt-auth-dev}"
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

    step "ask the legacy application"
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
fi

if [[ "$mode" == "all" || "$mode" == "python" ]]; then
    step "re-seed fixtures"
    psql < "$here/seed.sql"

    step "ask the api"
    timeout 60 docker exec -i "$py_container" sh -c 'cat > /tmp/run-py-permissions.py' \
        < "$here/run-py.py"
    # The same cases.txt path as on the php side, so the two cannot disagree
    # about what was covered.
    timeout 30 docker exec "$py_container" mkdir -p /tools
    timeout 60 docker exec -i "$py_container" sh -c 'cat > /tools/cases.txt' < "$here/cases.txt"
    timeout 300 docker exec \
        -e DJANGO_SETTINGS_MODULE=libretime_api.settings.prod \
        -e LIBRETIME_CONFIG_FILEPATH=/tmp/ab-config.yml \
        "$py_container" sh -c 'cd /srv/src/api && timeout 240 .venv/bin/python /tmp/run-py-permissions.py' \
        > "$out/python"

    wc -l < "$out/python" | xargs echo "  verdicts:"
fi

if [[ "$mode" == "all" ]]; then
    step "compare"
    python3 "$here/compare.py" "$out/php" "$out/python"
fi

step "clean up fixtures"
psql < "$here/clean.sql"
