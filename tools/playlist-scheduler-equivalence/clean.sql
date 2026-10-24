-- Remove everything the playlist scheduler harness wrote.
DELETE FROM cc_schedule WHERE instance_id BETWEEN 8000 AND 8099;
DELETE FROM cc_show_instances WHERE id BETWEEN 8000 AND 8099;
DELETE FROM cc_playlistcontents WHERE playlist_id BETWEEN 8100 AND 8199;
DELETE FROM cc_playlist WHERE id BETWEEN 8100 AND 8199;
DELETE FROM cc_webstream WHERE id BETWEEN 8300 AND 8399;
DELETE FROM cc_files WHERE id BETWEEN 8200 AND 8299;
DELETE FROM cc_subjs WHERE id BETWEEN 8500 AND 8599;
DELETE FROM cc_show_days WHERE show_id BETWEEN 8400 AND 8499;
DELETE FROM cc_show WHERE id BETWEEN 8400 AND 8499;
DELETE FROM cc_pref WHERE keystr IN ('default_crossfade_duration', 'default_fade_in', 'default_fade_out');
