# Playlist scheduler equivalence harness

Checks that `libretime_api/schedule/services/scheduler.py` writes the same
`cc_schedule` rows as the legacy builder when it puts a playlist into a show
instance - same starts, ends, clip lengths, cues, fades, positions, statuses,
and the same `is_scheduled` flags on the files.

## Run it

```bash
tools/playlist-scheduler-equivalence/ab.sh            # both sides, then compare
tools/playlist-scheduler-equivalence/ab.sh php        # legacy side only
tools/playlist-scheduler-equivalence/ab.sh python     # python side only
```
### In CI

Same scenario, no containers. `run.sh` sets `AB_MODE=local`, and
`tools/equivalence-run.sh` (see `tools/EQUIVALENCE.md`) provides what the container mode
gets from the development stack: a database built by the api's own migrations, one config
file both sides read, and a RabbitMQ broker.

```bash
tools/equivalence-run.sh tools/playlist-scheduler-equivalence
```

On a workstation that means php 8.2 with `bcmath`, `yaml` and `pdo_pgsql`,
`make -C legacy vendor` in this checkout, `make -C api install`, and a postgres and a
broker the run can reach:

```bash
docker run -d --name eq-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:15
docker exec eq-pg psql -U postgres -c "CREATE USER libretime WITH PASSWORD 'libretime' CREATEDB;"
EQ_DB_SUPER_PASSWORD=postgres tools/equivalence-run.sh tools/playlist-scheduler-equivalence
```


The legacy side runs `Application_Model_ShowInstance::addPlaylistToShow()` - the
call the autoplaylist poll makes - from **this checkout**, mounted over the
application directory of the running image, because the image's own copy of the
legacy code can be years behind what is under review.

## The three cases

| instance | what it pins down |
| --- | --- |
| 8001 | an empty instance: the first item starts when the instance starts, later items start one crossfade before the previous ends |
| 8002 | an instance with content already scheduled: the appended playlist crosses into the last existing item |
| 8003 | a stream and a file: the stream gets the hardcoded half-second fade the legacy `retrieveMediaFiles()` gives it, whatever the playlist row says |
| 8004 | a **static block** in the playlist: its own rows, in position order |
| 8005 | a **dynamic block** in the playlist: criteria, then the fill - the whole chain, compared to what the legacy service picks |

`cc_show_instances.time_filled` is compared as well, which is what
`updateScheduleStatus()` leaves behind after the insert.

## Not covered, and why

* **The "show already started" branch**, which writes a filler row and starts at
  `now`. Both sides would have to run at the same instant for its rows to be
  comparable; they run seconds apart. `test_scheduler.py` covers it instead.
* **Randomly sorted dynamic blocks**, whose answer differs on every rebuild in
  both services. The fixtures use an ordered block so the choice itself is
  comparable; the random shape is covered by property tests in `test_fill.py`.

## A measured difference: the legacy side loses subsecond precision

`Application_Model_Playlist::getContents()` formats each row's length through
`LengthFormatter` for the interface, and that formatter rounds the seconds field
to one decimal. The value it returns is what gets scheduled. Measured against the
legacy service with a `00:04:58.75` clip and a `00:00:01.25` cue point:

```
stored in cc_playlistcontents   legacy schedules
  00:04:58.75                       00:04:58.8
  00:00:01.25                       00:00:01.2
```

The Python port schedules what the row holds. The rounding is a display artefact
of the legacy UI reaching a write path - up to fifty milliseconds per item - and
reproducing it would carry a defect into the new service rather than remove it.

So the fixtures here use values that are exact at one decimal, which keeps this
harness a byte-for-byte gate on the thing that actually matters: the crossfade
arithmetic, the ordering, and the resulting status of the instance.

## A legacy quirk the fixtures have to work around

`Application_Model_Playlist::getContents()` unions files, streams and blocks - and
joins each block to its **creator with an inner join**. A block whose
`creator_id` is null therefore makes the whole playlist return zero rows, silently:

```
block 8601, creator_id NULL   ->  playlist 8103 getContents(): 0 rows
block 8601, creator_id 8501   ->  playlist 8103 getContents(): 2 rows
```

The fixtures give both blocks a creator, so this harness compares the scheduling
rather than the bug. The port does not reproduce the join: it expands the block
whatever its creator. Worth reporting upstream - a show whose autoplaylist
contains an ownerless block plays nothing at all, with nothing in the logs.
