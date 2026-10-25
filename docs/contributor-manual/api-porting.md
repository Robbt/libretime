---
title: "Porting the legacy application to the API"
---

LibreTime is two applications over one PostgreSQL database: the legacy Zend 1 /
Propel 1 php application that serves the current interface, and a python Django
service that exposes `/api/v2` and runs background work. This page is the state of
play between them: what each side could do before the porting work started, what the
api can do now, what is still missing, and the tooling that keeps the claims
checkable.

It is written for contributors. For how a request authenticates, see
[API authentication](../developer-manual/api-authentication.md); for the php api that
stations integrate against today, see
[LibreTime API usage](../developer-manual/legacy-api.md).

## Where it started

`/api/v2` existed, and so did most of its shape: **32 model collections** covering
files, playlists, shows, smart blocks, podcasts, schedule rows, preferences, users and
history, plus a handful of read-only views. It was a database with a http interface.

What that meant in practice:

- **No person used it.** The api authenticated with a station-wide `Api-Key` header or
  HTTP basic auth. The php application's sessions - the thing a logged-in browser
  actually has - were invisible to it, and its CSRF tokens unusable, so the legacy
  interface made no calls to `/api/v2` at all: not one. Every write a person makes went
  through php, and continued to.

  A machine used it constantly - see [Who uses it
  today](#who-uses-it-today) - which is worth stating plainly before anything else on
  this page, because it decides what counts as a breaking change.

- **A host could create nothing.** `POST` was mapped to the `change` permission, so the
  entire `add_*` family that the roles grant was dead code. A host has `add_playlist`
  and no `change_playlist`, which meant posting a playlist was refused for exactly the
  role that is meant to make them.
- **Ownership was whole-table.** A user with `change_file` could edit any file, not the
  ones they own. The legacy application checks per object; the api checked per model.
- **A write was a row write, and rows are not features.** `POST /api/v2/shows` created
  a `cc_show` row: no repeat rules, no instances, a show that could never air.
  `DELETE /files/{id}` removed the row and left the bytes. Nothing published to
  RabbitMQ, so playout and the analyzer never heard about any of it.
- **No background work lived in python.** No worker, no beat schedule, no show
  population, no smart block generation, no autoplaylist building, no podcast import.
  All of it was php's `TaskManager` and cron.
- **Nothing was compared to php.** Not because anyone thought it unnecessary, but
  because there was no way to compare: the two halves had no shared fixture, and the
  legacy unit tests could not run against python code.

The consequence was that the port could not be _merged_ into anything: replacing a php
screen with an api call meant rewriting behaviour that nobody had written down, and the
behaviour written down was the code itself.

## Where it is

The rule the porting work follows is that **a write is only ported when the rows it
leaves are the rows php leaves** - not a similar idea of them, the same bytes. That is
what makes the difference between a rewrite that can be merged and one that becomes a
second opinion about what a station's calendar should look like.

### Who uses it today

Not the interface. **Playout** does, and playout is the service that goes to air:

| call                                                     | endpoint                                        | why                                                                                                                             |
| -------------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `list_schedule`                                          | `GET /schedule`                                 | the queue it is about to broadcast                                                                                              |
| `get_show`, `get_show_instance`                          | `GET /shows/{id}`, `/show-instances/{id}`       | which show a queued item belongs to                                                                                             |
| `get_file`, `download_file`                              | `GET /files/{id}`, `/files/{id}/download`       | the audio itself, via an `X-Accel-Redirect` to shared storage                                                                   |
| `update_file`                                            | `PATCH /files/{id}`                             | a recorded show writing its size and md5 back - the **only** write to `/api/v2` made by the running system outside a php screen |
| `get_webstream`                                          | `GET /webstreams/{id}`                          | what to tune to                                                                                                                 |
| `get_info`, `get_stream_preferences`, `get_stream_state` | `/info`, `/stream/preferences`, `/stream/state` | station settings and the on-air state it reports                                                                                |

Two edges run the other way, and both keep php necessary:

- the **analyzer** posts its results to `{public_url}/rest/media/{file_id}` - a php
  endpoint. An upload through `POST /api/v2/upload` creates the row and publishes the
  message; php writes the metadata, the duration and the cue points back, and moves the
  bytes out of `organize/`.
- the **api's own worker** calls the legacy `/api/` interface (`libretime_api_client.v1`)
  to trigger php's `TaskManager` - deliberately, as the watchdog that takes the calendar
  back if this worker stops extending it.

So the compatibility constraint on this port is not "somebody might be parsing this
json". It is: the schedule, instance and file rows that playout reads have to be the rows
php would have written, because playout queues audio from them and nothing between the
two checks whether a night is plausible.

### A person can sign in, and one login is enough

| endpoint                          | what it does                                               |
| --------------------------------- | ---------------------------------------------------------- |
| `POST /api/v2/session`            | signs in, writing a session row in the shape Zend reads    |
| `GET /api/v2/session`             | who the caller is                                          |
| `DELETE /api/v2/session`          | signs out, logging the session out for php too             |
| `GET /api/v2/session/permissions` | the caller's role and permission names, for drawing menus  |
| `GET /api/v2/session/csrf-token`  | the token the caller's own session expects back on a write |

The php application's `sessions` table is the single source of identity: the api reads
it to authenticate a browser, and writes it to sign somebody in. A session created at
`POST /api/v2/session` is accepted by a legacy screen, and a session created by a legacy
login authenticates api writes - proven against a running stack, in both directions.
Writes need the session's CSRF token, which is the same value the legacy pages embed in
their forms, read out of the same serialised payload. Sign-in accepts json only, so a
form on another site cannot make a victim's browser log in to an account the attacker
chose.

### A role means what it meant

`tools/permission-equivalence` runs every user against every object against every
action - 336 cases - through the php code that decides (`canSchedule`, `isUserType`,
the owner comparisons) and through the api's, and requires the answers to match. Two
bugs came out of building it: the dead `add_*` family, and a `filter(Q())` that made an
unknown role hold every permission. Object-level ownership (`change_own_playlist` and
friends) is now enforced per row, in both directions for child rows: a host may add
media to a show they host, and may not to one they don't.

Four documented divergences remain, all of them the legacy code's, all listed in the
harness: webstream permissions exclude the superadmin, and three cases where the api
refuses something php allows by accident.

### Library

`POST /api/v2/upload` mirrors `/rest/media`: extension filter from the same list php
uses, the disk-quota check against the same free-space call, a `cc_files` row owned by
the uploader with checksum and mime, bytes moved into `organize/<id>-<name>`, and a
message to the analyzer on RabbitMQ that is byte-identical to php's
(`tools/rabbitmq-equivalence`). A failed import leaves an `import_status=FAILED` row, as
php's does - the analyzer's report, not a vanished upload.

File reads, metadata edits and deletion - including the guard against deleting something
scheduled in the future, and removing the bytes - were already there and keep working,
now with ownership.

### The show calendar

The show editor is the largest single piece of legacy behaviour in the application
(`Application_Service_ShowService`, 1969 lines) and the one a station notices most when
it is wrong. It is ported end to end:

| endpoint                                                     | the calendar's action                                                                                                                                            |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/v2/shows`                                         | the "add show" dialog: show, repeat rules, rebroadcasts, hosts, and the instances inside the populated window                                                    |
| `PUT /api/v2/shows/{id}`                                     | the "edit show" dialog: adopts the nights that still belong, removes the ones the new rules no longer produce, moves queued content with the nights that survive |
| `POST /api/v2/shows/{id}/extend`                             | "load more": builds nights further out without moving the station's populated date                                                                               |
| `DELETE /api/v2/show-instances/{id}?scope=single\|following` | the calendar's two delete menu entries                                                                                                                           |
| `PATCH /api/v2/show-instances/{id}`                          | one night taken out of a repeating sequence and given its own time                                                                                               |

`tools/show-editor-equivalence` runs the twenty scenarios the legacy suite defines - every
repeat kind, an end date, three repeat days, a linked show, a recorded show with ten
rebroadcasts, three deletes and six edits - through php, through the port, and against
the expectation files the project already commits. All twenty produce identical rows
across the six tables the editor writes, with three documented exceptions, printed with
their reasons on every run.

Request bodies are the api's own vocabulary (`"duration": "1:30"`, `"week_days": ["fri"]`,
rebroadcasts counted in days) and the dialog's field names are accepted as an alias,
translated at the door: the service that writes rows reads exactly the payload the dialog
posts, so a show cannot mean one thing to a browser and another to an api client.

### Background work

| task                                              | replaces                                                                                          | runs                                                      |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `schedule.populate_shows`                         | the periodic top-up of the future calendar                                                        | every 30 min, when `general.show_instance_populator: api` |
| `schedule.clean_overbooked_schedule`              | the schedule's overbooked items, which php trims per instance while scheduling (`trimOverbooked`) | every 6 h                                                 |
| `core.clean_expired_sessions`                     | session rows past their lifetime                                                                  | every 6 h                                                 |
| `podcasts.import_episode`, `clean_failed_imports` | the podcast episode download the podcast screen triggers                                          | on demand / hourly                                        |
| `worker.legacy_trigger_task_manager`              | the watchdog, below                                                                               | every 5 min                                               |

Either side can own the calendar and neither can own it twice. `show_instance_populator`
picks the owner; when it says `api`, the python worker writes a heartbeat the php
`TaskManager` watches, and if the worker stops extending the horizon the legacy
application takes the job back. That is what makes the switch safe to flip on a running
station and flip back without a restore.

`tools/schedule-equivalence` compares a full populate run against php's, row for row,
including the timezone and DST arithmetic that decides which nights exist.

### On the branches above and below

Not everything is in one branch yet. On `feat/api-smart-block-criteria`, the smart block
criteria compiler (`Criteria.php`) is ported and compared against the php compiler over
the fixture set (`tools/criteria-equivalence`). On `feat/api-playlist-scheduler`, the
playlist scheduler, the dynamic-block filler and the autoplaylist builder are ported and
compared against php's schedule output
(`tools/playlist-scheduler-equivalence`), with `general.auto_playlist_builder` doing the
same handover dance as the populator.

## Browsing the api

Three pages, all generated from the code and none of them a second source of truth:

| URL                         | what it is                                                                                                                                                                                                                                                       |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/api/v2/schema`            | the OpenAPI 3.0 document, as YAML. Public on purpose — it is derived from serializers, viewsets and filter classes, so it contains no station data, and the application is open source. `test_public_document_leaks_no_station_secrets` keeps that claim honest. |
| `/api/v2/schema/swagger-ui` | interactive. **Try it out works for a signed-in browser**: the page is same-origin with the api, so the legacy session cookie rides along, and the csrf token is a documented header parameter (`GET /api/v2/session/csrf-token` returns it).                    |
| `/api/v2/schema/redoc`      | readable. Whole endpoint groups without collapsing panels — better for "what parameters does `/files` take".                                                                                                                                                     |

Services call it with the station key instead: `Authorization: Api-Key <general.api_key>`,
which is also in the document as a security scheme and needs no csrf token.

The javascript for both pages is loaded from a **pinned** jsdelivr version
(`SWAGGER_UI_VERSION`, `REDOC_VERSION` in the settings) rather than drf-spectacular's default
`@latest`, so a station's documentation page cannot change or break with no commit and no
release. The cost is honest and worth stating: a station with no internet access gets a page
that never finishes loading. Vendoring the assets would fix that at roughly 1.5 MB of
javascript and an exclusion for the large-file check, which has not seemed worth it yet.

For a branch, a pull request, or a machine with no stack running,
`tools/api-docs/index.html` renders the schema committed in the checkout:

```bash
python3 -m http.server 8000      # from the repository root
# http://localhost:8000/tools/api-docs/
# ?schema=http://localhost:8181/api/v2/schema   to read a running station's document
# ?api=http://localhost:8181                    to send calls there instead
```

Calls from that page are unauthenticated — a browser will not attach a station's cookie to a
cross-origin request — so use the station's own `/api/v2/schema/swagger-ui` for anything that
needs to be someone.

One use worth recommending to anyone weighing what to build next: open the document and read
it against the screen you use daily. It is the shortest route to the list in the next section,
because a page whose actions have no operations beside them is missing an endpoint, not a
button.

## What is still missing

The honest list, grouped by what kind of gap it is.

### Writes that have no api endpoint

These are the legacy screens a station uses that the api cannot yet serve. The row-level
collections underneath most of them exist, so what is missing is the _behaviour_ the
screen adds:

- **The analyzer's callback.** It reports to php's `/rest/media/{file_id}`. Until that
  result lands somewhere in the api - `PUT /files/{id}` with the metadata is close, but
  php's endpoint also moves the file and sets `import_status` - the upload pipeline is
  two applications wide, and the api cannot own an import on its own.
- **The radio page.** `stream/state` and `stream/preferences` are read-only. Switching
  between the schedule and a live input, stopping an input, and the microphone controls
  are playout RPC calls that no api view makes yet.
- **Bulk library operations.** Editing metadata across a selection, recalculating the
  schedule after a duration change, moving files between libraries in bulk, and the
  analyzer-driven repairs - replay gain, silencer values, cue values from silence
  detection.
- **Uploads from the server.** `PluploadController` and the library's import-from-directory
  path, which read a path on disk rather than a multipart body.
- **Show artwork.** `/rest/show-image` upload, list and delete. `image_path` is read by
  the api and written by php; `deleteShow` on the api therefore does not delete images,
  and says so in the code rather than inventing a path convention to delete by.
- **Passwords.** `UserSerializer` does not expose the password field at all, which is
  correct - and means the api cannot create a user who can sign in, change a password, or
  reset one. The legacy hash is md5, which is worth solving at the same time as porting
  it, not before.
- **Streams.** `cc_stream_setting` is written by php's stream settings screen; the api
  reads it.
- **Website and widget feeds.** `weekInfo`, `liveInfo`, `onAirLight`, `showLogo`,
  `itemHistoryFeed`, the RSS feeds. These are read-only and mostly map onto collections
  that exist, but each has a shape of its own that stations depend on.

### The machine callers, which is what actually keeps php running

Roughly a third of the legacy `/api/*` actions are called by LibreTime's own services,
not by people: `notifyMediaItemStartPlay`, `updateLiquidsoapStatus`, `updateSourceStatus`,
`uploadRecorded`, `recordedShows`, `getBootstrapInfo`, `mediaMonitorSetup`,
`registerComponent`, `rabbitmqDoPush`, `getStreamSetting`, `getStreamParameters`. Until
these have api equivalents - or a proxy - the php application cannot be turned off no
matter how complete `/api/v2` gets. They are the highest-value remaining work for that
reason, and the least visible to anyone using the interface.

### Services with no http surface

Ported behaviour that only the worker can reach today: rebuilding an autoplaylist on
demand, regenerating a smart block's contents, `recalculateSchedule`. A "now" button in an
interface needs these as endpoints.

### Deliberate divergences

Places where the port does something different on purpose, each marked in the code and,
where it shows in a row, kept in the relevant harness's ledger so it stays visible:

- **Errors are raised, not logged.** php catches an exception after rolling back, logs
  `Show creation failed`, and returns a show id. The api answers 400 or 500, and the
  transaction is rolled back either way. A caller being told is not a behaviour change;
  a caller being lied to is.
- **A bad field is refused before anything is written.** An unknown timezone or a
  missing weekday fails when the payload is read, naming the field, rather than after the
  show and its rules are written and the instance builder cannot resolve it.
- **Notifications happen after commit.** A save that rolled back must not tell playout to
  reload.
- **Orphans are deleted.** See below.
- **Upload filenames are basenamed.** php preserves directory parts of a filename for
  imports; over http, `../../etc/evil.mp3` is a path traversal attempt, so the api takes
  the name only. Documented as a security divergence, not a bug fix.
- **`POST` means `add`.** The permission mapping is corrected, so creating is what the
  roles say it should be.

### Upstream bugs found on the way, reported rather than fixed here

Fixing these in python while php still writes the same tables would make the two halves
disagree in production, so each is a report, listed here so they are not rediscovered:

- **Three missing cascades.** `cc_show_days.show_id`, `cc_show_instances.show_id` and
  `cc_schedule.instance_id` have no foreign key in the shipped schema. Deleting a show
  therefore leaves its repeat rules and instances pointing at an id that belongs to
  nobody; removing a repeat day leaves items queued in hours that no longer exist, whose
  files stay locked in the library. Every legacy dataset says the children go, because
  the test database is built from installer SQL _with_ the cascades - a bug the test suite
  is structurally unable to see. The port deletes them, matching the intent.
- **`Playlist::getContents()`** assumes a playlist has a creator; rows created by the
  analyzer have none.
- **Webstream permissions** exclude the superadmin role.
- **Session payloads carry the user's md5 password** in plain text inside the serialised
  session, where nothing needs it.
- **A recorded show's file size is never stored.** Playout patches
  `{"filesize": …, "md5": …}` onto `/api/v2/files/{id}` after recording; the api's field
  is `size` - `filesize` is its column name - and DRF ignores keys it does not know, so
  the request answers 200 and the row keeps `size = 0`. The md5 lands. Nothing raises:
  the api does not reject an unrecognised field, and playout catches the broad exception
  it would have gotten from a 400 and logs it. Found by reading the call sites rather
  than the endpoint, which is the argument for reading the call sites. The api now has a
  test pinning the patch that works (`storage/tests/views/test_file.py`); the playout
  change is one key name, and the open question is whether the api _should_ reject
  unknown fields so that the next one of these fails loudly instead of writing half a
  row.
- **Two identical legacy tests** (`changeRepeatDay` and `removeFirstRepeatShowDay` differ
  only in name, datasets included) and one orphaned dataset (`test_unlinkLinkedShow.yml`)
  no test references.

### Not going to be ported

The installer (`SetupController`), which docker and the package installers replaced; the
locale and per-user settings screens; the embeddable widget pages; the audio preview
player, which `/files/{id}/download` already covers.

## How a claim is checked

Six harnesses under `tools/`, all shaped the same way: run the fixture through the php
code, run it through the python code, diff at row and column level. Four of them add a
third leg - compare the php run against the expectation files the project commits -
because two implementations agreeing only proves they are wrong together, and the
datasets are the only statement of intent in the triangle. That leg is what found the
missing cascades.

| harness                                | what it compares                                      |
| -------------------------------------- | ----------------------------------------------------- |
| `tools/rabbitmq-equivalence`           | every message published, byte for byte                |
| `tools/schedule-equivalence`           | a full populate run: instances, blocks, schedule rows |
| `tools/criteria-equivalence`           | compiled smart block criteria → SQL conditions        |
| `tools/playlist-scheduler-equivalence` | playlist and block content placed into a show         |
| `tools/permission-equivalence`         | 336 allow/deny decisions                              |
| `tools/show-editor-equivalence`        | the show editor's 20 scenarios across 6 tables        |

Each creates its own database from the live schema and drops it afterwards. Not
optional: the show editor writes the global `shows_populated_until` preference, and a
development station that woke up believing its calendar was built to 2044 would stop
populating shows.

## The pattern, for the next one

1. Read the php service that implements the screen, and write down the tables it touches,
   the order it touches them in, and the messages it publishes.
2. Transcribe the legacy test scenarios into a harness - from the php test data classes,
   not from a description of them - and add the third leg against whatever the project
   already commits as expected output.
3. Run the php leg before writing python, and check it against upstream. If it disagrees,
   that is the finding, and it is written down before it is worked around.
4. Port. Where php's behaviour is surprising and visible in a row, reproduce it and say
   why in a comment. Where it is surprising and _harmful_, diverge, and put the divergence
   in the harness ledger rather than letting it become a silent difference.
5. Wire the endpoint last. The payload vocabulary is a design decision; the rows are a
   fact, and only the second one can be proved.
