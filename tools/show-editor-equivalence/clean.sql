-- Empty the harness database, sequences included, so every scenario starts where
-- the legacy test starts and instance ids begin again at 1.
DELETE FROM cc_schedule;
DELETE FROM cc_blockcontents;
DELETE FROM cc_blockcriteria;
DELETE FROM cc_block;
DELETE FROM cc_playlistcontents;
DELETE FROM cc_playlist;
DELETE FROM cc_webstream;
DELETE FROM cc_show_hosts;
DELETE FROM cc_show_rebroadcast;
DELETE FROM cc_show_instances;
DELETE FROM cc_show_days;
DELETE FROM cc_show;
DELETE FROM cc_pref;

ALTER SEQUENCE cc_show_id_seq RESTART WITH 1;
ALTER SEQUENCE cc_show_days_id_seq RESTART WITH 1;
ALTER SEQUENCE cc_show_instances_id_seq RESTART WITH 1;
ALTER SEQUENCE cc_show_rebroadcast_id_seq RESTART WITH 1;
ALTER SEQUENCE cc_show_hosts_id_seq RESTART WITH 1;
ALTER SEQUENCE cc_schedule_id_seq RESTART WITH 1;
