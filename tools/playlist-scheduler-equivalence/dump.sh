#!/usr/bin/env bash
#
# Dump the rows the builders produced: schedule items with their instance, and
# the is_scheduled flag of every fixture file.
#
#   dump.sh <side>
set -euo pipefail

side="${1:?usage: dump.sh <side>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out/$side"
db="${AB_DB_CONTAINER:-libretime-postgres-1}"

mkdir -p "$out"
rm -f "$out"/*.csv

# Same two environments as ab.sh — the development containers, or this host with the
# database tools/equivalence-run.sh built. The queries are identical either way: the
# dumps have to be the same bytes for the comparison to mean anything.
if [ "${AB_MODE:-container}" = "local" ]; then
    dump() {
        timeout 120 env PGPASSWORD="$EQ_DB_PASSWORD" psql -h "$EQ_DB_HOST" -p "$EQ_DB_PORT" \
            -U "$EQ_DB_USER" -d "$EQ_DB_NAME" -q -v ON_ERROR_STOP=1 \
            -c "\copy ($1) to stdout with (format csv, header)" >"$out/$2"
    }
else
    dump() {
        timeout 120 docker exec "$db" psql -U libretime -d libretime -q -v ON_ERROR_STOP=1 \
            -c "\copy ($1) to stdout with (format csv, header)" >"$out/$2"
    }
fi

# last_scheduled and created hold the wall clock of whichever run wrote them.
dump "
select s.instance_id, s.position, s.starts, s.ends, s.clip_length,
       s.cue_in, s.cue_out, s.fade_in, s.fade_out, s.playout_status,
       s.broadcasted, s.media_item_played,
       coalesce(s.file_id, -1) as file_id, coalesce(s.stream_id, -1) as stream_id
from cc_schedule s
where s.instance_id between 8000 and 8099
order by s.instance_id, s.position" schedule.csv

dump "
select id, starts, ends, time_filled, description, record, rebroadcast,
       modified_instance, autoplaylist_built
from cc_show_instances where id between 8000 and 8099 order by id" instances.csv

dump "
select id, is_scheduled from cc_files
where id between 8200 and 8299 order by id" files.csv

wc -l "$out"/*.csv
