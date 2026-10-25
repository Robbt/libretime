#!/usr/bin/env bash
#
# Dump the rows the builders produced, as CSV, for compare.py.
#
#   dump.sh <side>
#
# Volatile columns are left out on purpose:
#
#   cc_show_instances.created / last_scheduled   stamped with the wall clock of
#                                                whichever run happened last
#   cc_schedule.id                               insertion order detail; the
#                                                rows are keyed by their
#                                                instance and position instead
set -euo pipefail

side="${1:?usage: dump.sh <side>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/out/$side"

mkdir -p "$out"
rm -f "$out"/*.csv

# Same two environments as ab.sh: the development containers, or this host with the
# database tools/equivalence-run.sh built. The queries below are identical either way —
# the dumps have to be the same bytes for the comparison to mean anything.
if [ "${AB_MODE:-container}" = "local" ]; then
    psql_dump() {
        local file="$1" query="$2"
        timeout 120 env PGPASSWORD="$EQ_DB_PASSWORD" psql -h "$EQ_DB_HOST" -p "$EQ_DB_PORT" \
            -U "$EQ_DB_USER" -d "$EQ_DB_NAME" -v ON_ERROR_STOP=1 -q \
            -c "\copy ($query) to stdout with (format csv, header)" > "$out/$file"
  }
else
    psql_dump() {
        local file="$1" query="$2"
        timeout 120 docker exec "${AB_DB_CONTAINER:-libretime-postgres-1}" \
            psql -U libretime -d libretime -v ON_ERROR_STOP=1 -q \
            -c "\copy ($query) to stdout with (format csv, header)" > "$out/$file"
  }
fi

# Instance identity: which show, when it airs, which flags it carries, and for
# rebroadcasts and linked shows, which instance it was derived from
# (instance_id).
psql_dump instances.csv "
select id, show_id, starts, ends, description, record, rebroadcast,
       modified_instance, autoplaylist_built, time_filled,
       coalesce(instance_id, -1) as instance_id
from cc_show_instances
order by show_id, starts, ends, rebroadcast, id"

# The bookkeeping column the builder writes back per repeating day.
psql_dump show_days.csv "
select id, show_id, day, repeat_type, timezone, start_time, duration,
       next_pop_date, last_show
from cc_show_days
order by id"

# Content of the linked show, keyed by instance rather than by schedule id.
psql_dump schedule.csv "
select i.show_id, i.starts as instance_starts, s.position,
       s.starts, s.ends, s.clip_length, s.fade_in, s.fade_out,
       s.cue_in, s.cue_out, s.playout_status, s.broadcasted,
       coalesce(s.file_id, -1) as file_id,
       coalesce(s.stream_id, -1) as stream_id,
       coalesce(s.media_item_played::int, -1) as media_item_played
from cc_schedule s
join cc_show_instances i on i.id = s.instance_id
order by i.show_id, i.starts, s.position"

psql_dump prefs.csv "
select keystr, valstr from cc_pref
where keystr in ('shows_populated_until', 'default_crossfade_duration')
order by keystr"

wc -l "$out"/*.csv
