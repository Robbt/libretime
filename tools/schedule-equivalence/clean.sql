-- Remove everything the harness wrote. The schedule tables are the harness's
-- own while it runs, so this is only meant for afterwards.
DELETE FROM cc_schedule;
DELETE FROM cc_show_instances;
DELETE FROM cc_show_rebroadcast;
DELETE FROM cc_show_days;
DELETE FROM cc_show;
DELETE FROM cc_pref WHERE keystr IN ('shows_populated_until', 'default_crossfade_duration');
