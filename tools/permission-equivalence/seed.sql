-- Fixture station for the permission equivalence harness.
--
-- Every user type, and two of most objects: one owned by the host (9004) and one
-- owned by the admin (9002). The second owner is the point - with only one owner
-- in a table, a rule that asks "who owns everything in here?" looks identical to
-- one that asks "who owns this row?", and the difference is what is under test.
--
-- Ids live in 9000..9999 so clean.sql can remove them without touching anything.

BEGIN;

-- Same deletes as clean.sql, up front, so a run after a failed one starts fresh.
DELETE FROM podcast_episodes           WHERE id BETWEEN 9000 AND 9999;
DELETE FROM podcast                    WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_webstream               WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_blockcriteria           WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_blockcontents           WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_block                   WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_playlistcontents        WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_playlist                WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_files                   WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_schedule                WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_show_instances          WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_show_hosts              WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_show_rebroadcast        WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_show_days               WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_show                    WHERE id BETWEEN 9000 AND 9999;
DELETE FROM cc_subjs                   WHERE id BETWEEN 9000 AND 9999;

-- --------------------------------------------------------------- people ----
-- 9001 superadmin, 9002 admin, 9003 program manager, 9004 host of show 9101,
-- 9005 host of nothing, 9006 guest.
INSERT INTO cc_subjs (id, login, pass, type, first_name, last_name, email)
VALUES
    (9001, 'perm_superadmin', 'x', 'S', 'Super', 'Admin', 's@example.org'),
    (9002, 'perm_admin',      'x', 'A', 'Admin', 'User',  'a@example.org'),
    (9003, 'perm_manager',    'x', 'P', 'Pro',   'Manager', 'p@example.org'),
    (9004, 'perm_host_one',   'x', 'H', 'Host',  'One',   'h1@example.org'),
    (9005, 'perm_host_two',   'x', 'H', 'Host',  'Two',   'h2@example.org'),
    (9006, 'perm_guest',      'x', 'G', 'Guest', 'User',  'g@example.org');

-- --------------------------------------------------------------- shows ----
INSERT INTO cc_show (id, name, linked, is_linkable)
VALUES
    (9101, 'Hosted show',   false, true),
    (9102, 'Unhosted show', false, true);

INSERT INTO cc_show_days
    (id, first_show, last_show, start_time, timezone, duration, day,
     repeat_type, next_pop_date, show_id, record)
VALUES
    (9111, '2026-01-05', NULL, '01:00:00', 'UTC', '01:00', 0, 0, '2026-01-05', 9101, 0);

INSERT INTO cc_show_rebroadcast (id, day_offset, start_time, show_id)
VALUES (9121, '0', '03:00:00', 9101);

-- The host of 9101 and nobody else. cc_show_hosts is the whole of the legacy
-- per-show rule: Application_Model_User::canSchedule is "manager, or a row here".
INSERT INTO cc_show_hosts (id, show_id, subjs_id) VALUES (9131, 9101, 9004);

INSERT INTO cc_show_instances
    (id, show_id, starts, ends, time_filled, created, description, record,
     rebroadcast, modified_instance, autoplaylist_built)
VALUES
    (9201, 9101, '2030-01-05 01:00:00', '2030-01-05 02:00:00', '00:00:00',
     '2026-01-01 00:00:00', '', 0, 0, false, false),
    (9202, 9102, '2030-01-06 01:00:00', '2030-01-06 02:00:00', '00:00:00',
     '2026-01-01 00:00:00', '', 0, 0, false, false);

-- --------------------------------------------------------------- files ----
INSERT INTO cc_files (id, name, filepath, mime, file_exists, hidden,
                      is_scheduled, cuein, cueout, length, genre, utime, owner_id)
VALUES
    (9401, 'host file',   '/media/host.mp3',   'audio/mpeg', true, false, false,
     '00:00:00', '00:01:00', '00:01:00', 'Ambient', '2030-01-05 00:00:00', 9004),
    (9402, 'admin file',  '/media/admin.mp3',  'audio/mpeg', true, false, false,
     '00:00:00', '00:01:00', '00:01:00', 'Ambient', '2030-01-05 00:00:00', 9002),
    (9403, 'nobody file', '/media/nobody.mp3', 'audio/mpeg', true, false, false,
     '00:00:00', '00:01:00', '00:01:00', 'Ambient', '2030-01-05 00:00:00', NULL);

-- Content placed inside an instance: the row a host is allowed to change when
-- they host the show the instance belongs to.
INSERT INTO cc_schedule
    (id, starts, ends, clip_length, fade_in, fade_out, cue_in, cue_out,
     position, media_item_played, instance_id, file_id)
VALUES
    (9301, '2030-01-05 01:00:00', '2030-01-05 01:10:00', '00:10:00', '00:00:00', '00:00:00',
     '00:00:00', '00:10:00', 0, false, 9201, 9401),
    (9302, '2030-01-06 01:00:00', '2030-01-06 01:10:00', '00:10:00', '00:00:00', '00:00:00',
     '00:00:00', '00:10:00', 0, false, 9202, 9402);

-- ---------------------------------------------------------- playlists ----
INSERT INTO cc_playlist (id, name, description, length, utime, mtime, creator_id)
VALUES
    (9501, 'host playlist', '', '00:01:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00', 9004),
    (9502, 'admin playlist', '', '00:01:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00', 9002);

INSERT INTO cc_playlistcontents (id, playlist_id, file_id, type, position,
                                 cliplength, cuein, cueout, fadein, fadeout, trackoffset)
VALUES
    (9511, 9501, 9401, 0, 1, '00:01:00', '00:00:00', '00:01:00', '00:00:00', '00:00:00', 0),
    (9512, 9502, 9402, 0, 1, '00:01:00', '00:00:00', '00:01:00', '00:00:00', '00:00:00', 0);

-- ------------------------------------------------------- smart blocks ----
INSERT INTO cc_block (id, name, description, length, type, creator_id, utime, mtime)
VALUES
    (9601, 'host block', '', '00:01:00', 'string', 9004, '2026-01-01 00:00:00', '2026-01-01 00:00:00'),
    (9602, 'admin block', '', '00:01:00', 'string', 9002, '2026-01-01 00:00:00', '2026-01-01 00:00:00');

INSERT INTO cc_blockcontents (id, block_id, file_id, position, cliplength,
                              cuein, cueout, fadein, fadeout, trackoffset)
VALUES
    (9611, 9601, 9401, 1, '00:01:00', '00:00:00', '00:01:00', '00:00:00', '00:00:00', 0),
    (9612, 9602, 9402, 1, '00:01:00', '00:00:00', '00:01:00', '00:00:00', '00:00:00', 0);

INSERT INTO cc_blockcriteria (id, criteria, modifier, value, extra, criteriagroup, block_id)
VALUES
    (9621, 'artist_name', 'contains', 'Host', 'AND', 0, 9601),
    (9622, 'artist_name', 'contains', 'Admin', 'AND', 0, 9602);

-- -------------------------------------------------------- webstreams ----
INSERT INTO cc_webstream (id, name, description, url, length, mime, utime, mtime, creator_id)
VALUES
    (9701, 'host stream', '', 'http://example.org/host.mp3', '00:00:00', 'audio/mpeg',
     '2026-01-01 00:00:00', '2026-01-01 00:00:00', 9004),
    (9702, 'admin stream', '', 'http://example.org/admin.mp3', '00:00:00', 'audio/mpeg',
     '2026-01-01 00:00:00', '2026-01-01 00:00:00', 9002);

-- ---------------------------------------------------------- podcasts ----
INSERT INTO podcast (id, url, title, description, language, itunes_category, itunes_explicit, owner)
VALUES
    (9801, 'http://example.org/h.xml', 'Host podcast', '', 'en', 'Arts', 'explicit', 9004),
    (9802, 'http://example.org/a.xml', 'Admin podcast', '', 'en', 'Arts', 'explicit', 9002);

INSERT INTO podcast_episodes (id, podcast_id, file_id, episode_title, episode_guid,
                              episode_description, publication_date, download_url, created_at)
VALUES
    (9811, 9801, 9401, 'Host episode',  'perm-h-1', 'fixture',
     '2026-01-01 00:00:00', 'http://example.org/h1.mp3', '2026-01-01 00:00:00'),
    (9812, 9802, 9402, 'Admin episode', 'perm-a-1', 'fixture',
     '2026-01-01 00:00:00', 'http://example.org/a1.mp3', '2026-01-01 00:00:00');

COMMIT;
