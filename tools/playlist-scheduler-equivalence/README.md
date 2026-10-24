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

`cc_show_instances.time_filled` is compared as well, which is what
`updateScheduleStatus()` leaves behind after the insert.

## Not covered, and why

* **The "show already started" branch**, which writes a filler row and starts at
  `now`. Both sides would have to run at the same instant for its rows to be
  comparable; they run seconds apart. `test_scheduler.py` covers it instead.
* **Playlists containing smart blocks**, which have to be evaluated first.

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
