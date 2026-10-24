#!/usr/bin/env bash
#
# Criteria equivalence harness: seeds the fixture library and blocks, runs the
# legacy PHP compiler and the Python one over the same rows, and compares the
# file lists they return.
#
#   ab.sh            both sides and compare
#   ab.sh php        legacy side only
#   ab.sh python     python side only
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out"

php_container="${AB_LEGACY_CONTAINER:-libretime-legacy-1}"
py_container="${AB_PY_CONTAINER:-rt-blocks-dev}"
db_container="${AB_DB_CONTAINER:-libretime-postgres-1}"

mode="${1:-all}"

step() { printf '\n--- %s\n' "$1"; }

psql() {
    timeout 120 docker exec -i "$db_container" psql -U libretime -d libretime \
        -v ON_ERROR_STOP=1 -q "$@"
}

mkdir -p "$out"

step "seed fixtures"
psql < "$here/seed.sql"
psql -tAc "select 'blocks: '||count(*) from cc_block where id between 9500 and 9599"
psql -tAc "select 'files: '||count(*) from cc_files where id between 9000 and 9199"

if [[ "$mode" == "all" || "$mode" == "php" ]]; then
    step "run legacy compiler"

    # The legacy code is run from this checkout, mounted over the application
    # directory of the image the stack uses, with the image's vendor directory
    # left in place. Without that the comparison is silently against whatever
    # commit the image was built from - which is how one early run of this
    # harness reported criteria groups behaving the pre-#3318 way.
    legacy_image="$(docker inspect "$php_container" --format '{{.Image}}')"
    app_dir="$(cd "$here/../../legacy/application" && pwd)"
    host_md5="$(md5sum "$app_dir/models/Block.php" | cut -d' ' -f1)"

    # Reuse the configuration of the running legacy service, so both sides
    # resolve the station timezone and the database the same way.
    timeout 60 docker exec "$php_container" cat /etc/libretime/config.yml \
        > "$out/legacy-config.yml"

    timeout 300 docker run --rm --network libretime_default \
        -v "$app_dir:/var/www/html/application:ro" \
        -v "$here:/tools:ro" \
        -v "$out/legacy-config.yml:/etc/libretime/config.yml:ro" \
        -e LIBRETIME_CONFIG_FILEPATH=/etc/libretime/config.yml \
        "$legacy_image" sh -c "
            guest_md5=\$(md5sum /var/www/html/application/models/Block.php | cut -d' ' -f1)
            if [ \"\$guest_md5\" != '$host_md5' ]; then
                echo 'error: the legacy sources being run are not the ones under review' >&2
                exit 1
            fi
            php /tools/run-php.php" > "$out/php.txt"

    wc -l < "$out/php.txt" | xargs echo "blocks answered:"
fi

if [[ "$mode" == "all" || "$mode" == "python" ]]; then
    step "re-seed fixtures"
    psql < "$here/seed.sql"

    step "run python compiler"
    timeout 60 docker exec -i "$py_container" sh -c 'cat > /tmp/run-py-criteria.py' \
        < "$here/run-py.py"
    timeout 300 docker exec \
        -e DJANGO_SETTINGS_MODULE=libretime_api.settings.prod \
        -e LIBRETIME_CONFIG_FILEPATH=/tmp/ab-config.yml \
        "$py_container" sh -c 'cd /srv/src/api && .venv/bin/python /tmp/run-py-criteria.py' \
        > "$out/python.txt"

    wc -l < "$out/python.txt" | xargs echo "blocks answered:"
fi

if [[ "$mode" == "all" ]]; then
    step "compare"
    python3 "$here/compare.py" "$out/php.txt" "$out/python.txt"
fi
