-- Remove the fixture station. Kept separate from seed.sql so ab.sh can leave the
-- database as it found it.
BEGIN;
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
COMMIT;
