# Permission equivalence harness

Compares **who may change or delete which object** between the legacy
application and `/api/v2`, over a fixture station with every user type and
objects owned by different people.

    ./ab.sh            both sides and compare
    ./ab.sh php        legacy side only
    ./ab.sh python     api side only

### In CI

Same scenario, no containers. `run.sh` sets `AB_MODE=local`, and
`tools/equivalence-run.sh` (see `tools/EQUIVALENCE.md`) provides what the container mode
gets from the development stack: a database built by the api's own migrations, one config
file both sides read, and a RabbitMQ broker.

```bash
tools/equivalence-run.sh tools/permission-equivalence
```

On a workstation that means php 8.2 with `bcmath`, `yaml` and `pdo_pgsql`,
`make -C legacy vendor` in this checkout, `make -C api install`, and a postgres and a
broker the run can reach:

```bash
docker run -d --name eq-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:15
docker exec eq-pg psql -U postgres -c "CREATE USER libretime WITH PASSWORD 'libretime' CREATEDB;"
EQ_DB_SUPER_PASSWORD=postgres tools/equivalence-run.sh tools/permission-equivalence
```

## How each side is asked

`cases.txt` is the single source of truth: one `<user> <kind>:<id> <action>` per
line. Both sides read it and print `<case> <allow|deny>`, so the two cannot drift
apart in what they covered.

The legacy side (`run-php.php`) calls the predicates the application itself uses.
The composition of those predicates into a decision is written out next to the
citation of every call site, because that composition is what a reviewer has to
check:

| rule                                                          | objects                                                                                                | legacy code                                                                                                                                  |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| admin or program manager (`isUserType([S,A,P])`)              | show, show days, show instance, show rebroadcast, show host                                            | `ScheduleController.php:219` `:698`, `ScheduleController.php:110` `:142`                                                                     |
| that, or the owner of the object                              | file, playlist, playlist content, smart block, block content, block criteria, podcast, podcast episode | `StoredFile.php:399`, `Playlist.php:986` + `playlistsNotOwnedByUser`, `Block.php:1019` + `blocksNotOwnedByUser`, `LibraryController.php:352` |
| that, or a host of the show the row belongs to                | schedule row (content placed in a show instance)                                                       | `User.php:59` `canSchedule`, `Scheduler.php:146`, `CcSubjs::isHostOfShowInstance`                                                            |
| admin or program manager **without** superadmin, or the owner | webstream                                                                                              | `Webstream.php:97`                                                                                                                           |

The api side (`run-py.py`) asks `IsSystemTokenOrUser.has_permission` and
`has_object_permission` with the real viewsets, the real `GROUPS` role lists and
the real `get_owner()` implementations.

## Deliberate divergence

**Webstreams and the superadmin.** `Application_Model_Webstream::deleteStreams`
checks `isUserType([UTYPE_ADMIN, UTYPE_PROGRAM_MANAGER])` while every sibling
checks `[UTYPE_SUPERADMIN, UTYPE_ADMIN, UTYPE_PROGRAM_MANAGER]`. A superadmin is
therefore not an admin there, and can only delete webstreams they created. It
reads as a copy-paste slip rather than a decision, and `compare.py` lists it as an
expected difference so the harness stays green while the api treats a superadmin
as an admin everywhere.

## What is out of scope

Page access - which menus and screens a role sees - is `legacy/application/configs/ACL.php`,
a role-by-controller-by-action list with no per-row notion at all. Its api
counterpart is the `view_*` half of `permission_constants.py`, and the two already
agree in shape. Two differences worth knowing, neither touched here:

- a guest may not open the library, playlist or podcast page (`ACL.php` grants
  those to `H`), but does hold `view_file`, `view_playlist` and `view_podcast`,
  so the api answers a guest's read where the legacy ui would not;
- `canSchedule` also decides whether the showbuilder _renders_ a row as editable
  (`ShowBuilder.php:102`), which is a user interface question rather than a
  permission one.
