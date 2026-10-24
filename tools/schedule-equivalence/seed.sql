-- Fixture state for the schedule builder equivalence harness.
--
-- Both the legacy PHP builder and the Python builder run against exactly this
-- state and their output rows are compared. Every repeat rule the legacy
-- builder knows about is represented, plus the cases where its date handling
-- is not obvious (DST gap, DST transition, weekday rolls back a day, months
-- without the day of month).
--
-- ids are explicit so that both runs start from the same sequence values.

BEGIN;

-- cc_schedule -> cc_show_instances -> cc_show, delete in that order.
DELETE FROM cc_schedule;
DELETE FROM cc_show_instances;
DELETE FROM cc_show_rebroadcast;
DELETE FROM cc_show_days;
DELETE FROM cc_show;

-- ---------------------------------------------------------------- shows ----

-- Weekly, plain: the baseline.
INSERT INTO cc_show (id, name, linked, is_linkable) VALUES (101, 'A weekly utc', false, true);
-- Weekly across the Europe/Berlin spring transition: instances keep the local
-- wall clock, so their absolute length changes.
INSERT INTO cc_show (id, name, linked, is_linkable) VALUES (102, 'B weekly berlin', false, true);
-- Bi weekly with a hard end date: exercises the last_show cut.
INSERT INTO cc_show (id, name, linked, is_linkable) VALUES (103, 'C biweekly ny', false, true);
-- Quad weekly far east.
INSERT INTO cc_show (id, name, linked, is_linkable) VALUES (104, 'D quadweekly tokyo', false, true);
-- Weekly starting inside the DST gap (02:30 does not exist on 2026-03-29).
INSERT INTO cc_show (id, name, linked, is_linkable) VALUES (105, 'E triweekly gap', false, true);
-- Monthly on the 31st: months without 31 days must be skipped.
INSERT INTO cc_show (id, name, linked, is_linkable) VALUES (106, 'F monthly 31', false, true);
-- Monthly on the 29th, crossing both transitions.
INSERT INTO cc_show (id, name, linked, is_linkable) VALUES (107, 'G monthly 29', false, true);
-- Monthly on the first Monday, created in New York: PHP computes the weekday
-- occurrence in UTC, so these instances air on Sunday evening local time.
INSERT INTO cc_show (id, name, linked, is_linkable) VALUES (108, 'H monthly 1st mon ny', false, true);
-- Monthly on the fifth Thursday: months without a fifth Thursday are skipped.
INSERT INTO cc_show (id, name, linked, is_linkable) VALUES (109, 'I monthly 5th thu', false, true);
-- Recorded show with rebroadcasts.
INSERT INTO cc_show (id, name, linked, is_linkable) VALUES (110, 'J rebroadcast', false, true);
-- Linked show: its instances get the content of the most recent instance.
INSERT INTO cc_show (id, name, linked, is_linkable) VALUES (111, 'K linked', true, false);
-- One show with two repeating days.
INSERT INTO cc_show (id, name, linked, is_linkable) VALUES (112, 'L two days', false, true);
-- Non repeating: must not generate anything on this path.
INSERT INTO cc_show (id, name, linked, is_linkable) VALUES (113, 'M nonrepeating', false, true);

-- ------------------------------------------------------------- show days ---
-- columns: id, first_show, last_show, start_time, timezone, duration, day,
--          repeat_type, next_pop_date, show_id, record
INSERT INTO cc_show_days
    (id, first_show, last_show, start_time, timezone, duration, day, repeat_type, next_pop_date, show_id, record)
VALUES
    (201, '2026-01-05', NULL,       '01:00:00', 'UTC',              '01:00', 0, 0, '2026-01-05', 101, 0),
    (202, '2026-01-07', NULL,       '21:00:00', 'Europe/Berlin',    '02:00', 3, 0, '2026-01-07', 102, 0),
    (203, '2026-01-02', '2026-06-26','22:00:00', 'America/New_York', '01:30', 4, 1, '2026-01-02', 103, 0),
    (204, '2026-01-04', NULL,       '09:30:00', 'Asia/Tokyo',       '00:45', 6, 5, '2026-01-04', 104, 0),
    (205, '2026-02-25', NULL,       '02:30:00', 'Europe/Berlin',    '00:30', 2, 4, '2026-02-25', 105, 0),
    (206, '2026-01-31', NULL,       '17:00:00', 'UTC',              '01:00', 1, 2, '2026-01-31', 106, 0),
    (207, '2026-01-29', NULL,       '23:30:00', 'Europe/Berlin',    '01:00', 5, 2, '2026-01-29', 107, 0),
    (208, '2026-01-05', NULL,       '20:00:00', 'America/New_York', '02:00', 0, 3, '2026-01-05', 108, 0),
    (209, '2026-01-29', NULL,       '20:00:00', 'UTC',              '01:00', 4, 3, '2026-01-29', 109, 0),
    (210, '2026-01-06', NULL,       '14:00:00', 'UTC',              '02:00', 1, 0, '2026-01-06', 110, 1),
    (211, '2026-01-07', NULL,       '12:00:00', 'UTC',              '03:00', 3, 0, '2026-01-07', 111, 0),
    (212, '2026-01-05', NULL,       '10:00:00', 'UTC',              '01:00', 0, 0, '2026-01-05', 112, 0),
    (213, '2026-01-08', NULL,       '10:00:00', 'UTC',              '01:00', 3, 0, '2026-01-08', 112, 0),
    (214, '2026-02-03', NULL,       '20:00:00', 'UTC',              '01:00', 2, -1, NULL,       113, 0);

SELECT setval('cc_show_days_id_seq', 300, true);
SELECT setval('cc_show_id_seq', 200, true);

-- ----------------------------------------------------------- rebroadcasts ---

INSERT INTO cc_show_rebroadcast (id, day_offset, start_time, show_id) VALUES
    (301, '0', '23:30:00', 110),
    (302, '1', '10:00:00', 110);

SELECT setval('cc_show_rebroadcast_id_seq', 400, true);

-- ------------------------------------------- linked show template content ---

-- The instance that aired before the populated-until preference, holding the
-- content every new instance of show 111 is filled with. Clip lengths carry
-- subsecond precision so the stored values show whether the truncation
-- behaviour of the legacy builder was preserved.
INSERT INTO cc_show_instances
    (id, show_id, starts, ends, time_filled, created, description, record,
     rebroadcast, modified_instance, autoplaylist_built)
VALUES
    (401, 111, '2025-12-31 12:00:00', '2025-12-31 15:00:00', '00:00:06.875',
     '2025-12-01 00:00:00', '', 0, 0, false, false);

SELECT setval('cc_show_instances_id_seq', 500, true);

INSERT INTO cc_schedule
    (id, starts, ends, clip_length, fade_in, fade_out, cue_in, cue_out,
     instance_id, position, playout_status, broadcasted)
VALUES
    (501, '2025-12-31 12:00:00',   '2025-12-31 12:00:03.250', '00:00:03.250',
     '00:00:01', '00:00:01', '00:00:00.5', '00:00:00.5', 401, 0, 1, 0),
    (502, '2025-12-31 12:00:03.250', '2025-12-31 12:00:05.750', '00:00:02.5',
     '00:00:01', '00:00:01', '00:00:00.5', '00:00:00.5', 401, 1, 1, 0),
    (503, '2025-12-31 12:00:04.750', '2025-12-31 12:00:05.875', '00:00:01.125',
     '00:00:01', '00:00:01', '00:00:00.5', '00:00:00.5', 401, 2, 1, 0);

SELECT setval('cc_schedule_id_seq', 600, true);

-- ---------------------------------------------------------------- prefs ----

DELETE FROM cc_pref WHERE keystr IN ('shows_populated_until', 'default_crossfade_duration');

-- Both builders are asked to populate until 2026-12-31, so this run has a full
-- year of work to do.
INSERT INTO cc_pref (keystr, valstr) VALUES ('shows_populated_until', '2026-01-01 00:00:00');
-- Fractional crossfade, so the copied items do not land on whole seconds.
INSERT INTO cc_pref (keystr, valstr) VALUES ('default_crossfade_duration', '1.5');

COMMIT;
