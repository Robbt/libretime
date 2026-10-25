-- The fixture state of the legacy unit test, tests/application/services/database/
-- datasets/seed_show_service.yml: two preferences and nothing else.
--
-- shows_populated_until is what makes the comparison exact. Instance creation
-- stops at that date instead of at "now", so both sides build the same six
-- weekly instances, and the delete scenarios can name instances by id - which is
-- what the legacy tests do, and only works from an empty database with fresh
-- sequences.
INSERT INTO cc_pref (keystr, valstr) VALUES ('shows_populated_until', '2044-02-07 00:00:00');
INSERT INTO cc_pref (keystr, valstr) VALUES ('timezone', 'UTC');
