-- Fixture state for the playlist scheduler equivalence harness.
--
-- Show instances and playlists that the legacy builder and the Python port each
-- schedule into, after which the cc_schedule rows are compared.
--
-- Instances start far enough in the future that the "show already started"
-- branch is not taken: it depends on the wall clock, and the two runs happen
-- seconds apart with a filler row in between, which nothing but a property test
-- can compare fairly. That branch is covered by the pytest suite instead.

BEGIN;

DELETE FROM cc_schedule WHERE instance_id BETWEEN 8000 AND 8099;
DELETE FROM cc_show_instances WHERE id BETWEEN 8000 AND 8099;
DELETE FROM cc_playlistcontents WHERE playlist_id BETWEEN 8100 AND 8199;
DELETE FROM cc_playlist WHERE id BETWEEN 8100 AND 8199;
DELETE FROM cc_webstream WHERE id BETWEEN 8300 AND 8399;
DELETE FROM cc_subjs WHERE id BETWEEN 8500 AND 8599;
DELETE FROM cc_files WHERE id BETWEEN 8200 AND 8299;
DELETE FROM cc_playlistcontents WHERE playlist_id BETWEEN 8100 AND 8199;
DELETE FROM cc_show_days WHERE show_id BETWEEN 8400 AND 8499;
DELETE FROM cc_show WHERE id BETWEEN 8400 AND 8499;
DELETE FROM cc_blockcriteria WHERE block_id BETWEEN 8600 AND 8699;
DELETE FROM cc_blockcontents WHERE block_id BETWEEN 8600 AND 8699;
DELETE FROM cc_block WHERE id BETWEEN 8600 AND 8699;
DELETE FROM cc_pref WHERE keystr IN ('default_crossfade_duration', 'default_fade_in', 'default_fade_out');

-- --------------------------------------------------------------- files ----

INSERT INTO cc_files (id, name, filepath, mime, file_exists, hidden, is_scheduled,
                      cuein, cueout, length, genre, utime)
VALUES
    (8201, 'f1', '/media/f1.mp3', 'audio/mpeg', true, false, false,
     '00:00:00', '00:10:00', '00:10:00', 'Ambient', '2030-01-05 00:00:00'),
    (8202, 'f2', '/media/f2.mp3', 'audio/mpeg', true, false, false,
     '00:00:01.2', '00:05:00', '00:05:00', 'Ambient', '2030-01-04 00:00:00'),
    (8203, 'f3', '/media/f3.mp3', 'audio/mpeg', true, false, true,
     '00:00:00', '00:02:30.5', '00:02:30.5', 'Techno', '2030-01-03 00:00:00'),
    (8204, 'f4', '/media/f4.mp3', 'audio/mpeg', true, false, false,
     '00:00:00', '00:00:45', '00:00:45', 'Ambient', '2030-01-02 00:00:00');

-- --------------------------------------------------------------- stream ----

-- creator_id is not null in the real schema, even though the model allows it.
INSERT INTO cc_subjs (id, login, pass, first_name, last_name, email)
VALUES (8501, 'streamer', '', 'Stream', 'Owner', 'stream@example.org');

INSERT INTO cc_webstream (id, name, description, url, length, mime, utime, mtime, creator_id)
VALUES
    (8301, 'jazz stream', 'nightly', 'https://example.org/jazz', '00:20:00',
     'audio/mpeg', '2030-01-01 00:00:00', '2030-01-01 00:00:00', 8501);

-- ------------------------------------------------------------- playlists ---

-- Three files, subsecond and fractional cue points included.
INSERT INTO cc_playlist (id, name, description, length, utime, mtime)
VALUES (8101, 'three files', '', '00:17:30', '2030-01-01 00:00:00', '2030-01-01 00:00:00');

INSERT INTO cc_playlistcontents
    (playlist_id, type, position, file_id, cliplength, cuein, cueout, fadein, fadeout, trackoffset)
VALUES
    (8101, 0, 0, 8201, '00:10:00', '00:00:00', '00:10:00', '00:00:01', '00:00:01', 0),
    (8101, 0, 1, 8202, '00:04:58.8', '00:00:01.2', '00:05:00', '00:00:00.5', '00:00:02', 0),
    (8101, 0, 2, 8203, '00:02:30.5', '00:00:00', '00:02:30.5', '00:00:01', '00:00:01', 0);

-- A stream and a file, to show the hardcoded stream fade.
INSERT INTO cc_playlist (id, name, description, length, utime, mtime)
VALUES (8102, 'stream and file', '', '00:22:30', '2030-01-01 00:00:00', '2030-01-01 00:00:00');

INSERT INTO cc_playlistcontents
    (playlist_id, type, position, file_id, stream_id, cliplength, cuein, cueout, fadein, fadeout, trackoffset)
VALUES
    (8102, 1, 0, NULL, 8301, '00:20:00', '00:00:00', '00:00:00', '00:00:03', '00:00:03', 0),
    (8102, 0, 1, 8204, NULL, '00:00:45', '00:00:00', '00:00:45', '00:00:01', '00:00:01', 0);

-- ---------------------------------------------------------------- blocks ----

-- A static block: its own rows, in position order.
-- creator_id is not decoration: getContents() of a PLAYLIST joins the block's
-- creator with an inner join, so a block with no creator makes the whole
-- playlist contribute zero rows - measured, and a legacy bug worth reporting
-- rather than reproducing.
INSERT INTO cc_block (id, name, description, length, type, creator_id, utime, mtime)
VALUES (8601, 'static pair', '', '00:03:00', 'static', 8501,
        '2030-01-01 00:00:00', '2030-01-01 00:00:00');

INSERT INTO cc_blockcontents (block_id, file_id, position, cliplength, cuein, cueout, fadein, fadeout, trackoffset)
VALUES
    (8601, 8202, 0, '00:04:58.8', '00:00:01.2', '00:05:00', '00:00:01', '00:00:01', 0),
    (8601, 8204, 1, '00:00:45', '00:00:00', '00:00:45', '00:00:01', '00:00:01', 0);

INSERT INTO cc_playlist (id, name, description, length, utime, mtime)
VALUES (8103, 'static block playlist', '', '00:05:43.8', '2030-01-01 00:00:00', '2030-01-01 00:00:00');

INSERT INTO cc_playlistcontents
    (playlist_id, type, position, block_id, cliplength, cuein, cueout, fadein, fadeout, trackoffset)
VALUES (8103, 2, 0, 8601, '00:05:43.8', '00:00:00', '00:00:00', '00:00:01', '00:00:01', 0);

-- A dynamic block, ordered rather than random, so the answer is comparable.
INSERT INTO cc_block (id, name, description, length, type, creator_id, utime, mtime)
VALUES (8602, 'short ambient', '', '00:03:00', 'dynamic', 8501,
        '2030-01-01 00:00:00', '2030-01-01 00:00:00');

INSERT INTO cc_blockcriteria (block_id, criteria, modifier, value, extra, criteriagroup)
VALUES
    (8602, 'genre', 'is', 'Ambient', NULL, 0),
    (8602, 'sort', 'N/A', 'newest', NULL, 0),
    (8602, 'limit', 'minutes', '6', NULL, 0),
    (8602, 'repeat_tracks', 'N/A', '0', NULL, 0),
    (8602, 'overflow_tracks', 'N/A', '0', NULL, 0);

INSERT INTO cc_playlist (id, name, description, length, utime, mtime)
VALUES (8104, 'dynamic block playlist', '', '00:06:00', '2030-01-01 00:00:00', '2030-01-01 00:00:00');

INSERT INTO cc_playlistcontents
    (playlist_id, type, position, block_id, cliplength, cuein, cueout, fadein, fadeout, trackoffset)
VALUES (8104, 2, 0, 8602, '00:06:00', '00:00:00', '00:00:00', '00:00:01', '00:00:01', 0);

-- ---------------------------------------------------------------- shows ----

INSERT INTO cc_show (id, name, linked, is_linkable, has_autoplaylist, autoplaylist_repeat,
                     override_intro_playlist, override_outro_playlist)
VALUES
    (8401, 'empty build target', false, true, true, false, false, false),
    (8402, 'append target', false, true, true, false, false, false),
    (8403, 'stream target', false, true, true, false, false, false),
    (8404, 'static block target', false, true, true, false, false, false),
    (8405, 'dynamic block target', false, true, true, false, false, false);

INSERT INTO cc_show_days (show_id, first_show, start_time, timezone, duration, day, repeat_type, next_pop_date, record)
VALUES
    (8401, '2031-01-01', '00:00:00', 'UTC', '02:00', 1, 0, '2031-01-01', 0),
    (8402, '2031-01-01', '00:00:00', 'UTC', '02:00', 2, 0, '2031-01-01', 0),
    (8403, '2031-01-01', '00:00:00', 'UTC', '02:00', 3, 0, '2031-01-01', 0),
    (8404, '2031-01-01', '00:00:00', 'UTC', '02:00', 4, 0, '2031-01-01', 0),
    (8405, '2031-01-01', '00:00:00', 'UTC', '02:00', 5, 0, '2031-01-01', 0);

-- ------------------------------------------------------------- instances ---

INSERT INTO cc_show_instances
    (id, show_id, starts, ends, time_filled, created, description, record,
     rebroadcast, modified_instance, autoplaylist_built)
VALUES
    (8001, 8401, '2031-06-02 00:00:00', '2031-06-02 02:00:00', '00:00:00',
     '2030-01-01 00:00:00', '', 0, 0, false, false),
    (8002, 8402, '2031-06-03 00:00:00', '2031-06-03 02:00:00', '00:00:00',
     '2030-01-01 00:00:00', '', 0, 0, false, false),
    (8003, 8403, '2031-06-04 00:00:00', '2031-06-04 02:00:00', '00:00:00',
     '2030-01-01 00:00:00', '', 0, 0, false, false),
    (8004, 8404, '2031-06-05 00:00:00', '2031-06-05 02:00:00', '00:00:00',
     '2030-01-01 00:00:00', '', 0, 0, false, false),
    (8005, 8405, '2031-06-06 00:00:00', '2031-06-06 02:00:00', '00:00:00',
     '2030-01-01 00:00:00', '', 0, 0, false, false);

-- One item already scheduled in the append target, so the appended playlist has
-- something to crossfade into.
INSERT INTO cc_schedule
    (starts, ends, file_id, clip_length, fade_in, fade_out, cue_in, cue_out,
     instance_id, position, playout_status, broadcasted, media_item_played)
VALUES
    ('2031-06-03 00:00:00', '2031-06-03 00:15:00', 8204, '00:15:00',
     '00:00:01', '00:00:01', '00:00:00', '00:15:00', 8002, 0, 1, 0, false);

-- ------------------------------------------------------------------ prefs ---

-- The overlap the fixtures are computed against, plus the defaults a dynamic
-- block would use.
INSERT INTO cc_pref (keystr, valstr) VALUES ('default_crossfade_duration', '1.5');
INSERT INTO cc_pref (keystr, valstr) VALUES ('default_fade_in', '0.5');
INSERT INTO cc_pref (keystr, valstr) VALUES ('default_fade_out', '0.5');

COMMIT;
