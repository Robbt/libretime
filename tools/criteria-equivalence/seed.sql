-- Fixture state for the smart block criteria equivalence harness.
--
-- One library, one set of blocks. Every block is one behaviour of the legacy
-- criteria compiler, and both builders are run over the same rows.
--
-- ids are in a range nothing else uses, and the rows are deleted first so the
-- file is idempotent.

BEGIN;

DELETE FROM cc_blockcriteria WHERE block_id BETWEEN 9500 AND 9599;
DELETE FROM cc_blockcontents WHERE block_id BETWEEN 9500 AND 9599;
DELETE FROM cc_block WHERE id BETWEEN 9500 AND 9599;
DELETE FROM cc_files WHERE id BETWEEN 9000 AND 9199;
DELETE FROM cc_track_types WHERE id BETWEEN 9200 AND 9299;
DELETE FROM cc_subjs WHERE id BETWEEN 9100 AND 9199;

-- --------------------------------------------------------------- owners ----

INSERT INTO cc_subjs (id, login, pass, first_name, last_name, email)
VALUES
    (9101, 'dj-one', '', 'DJ', 'One', 'dj@example.org'),
    (9102, 'announcer', '', 'Ann', 'Ouncer', 'ann@example.org');

-- ------------------------------------------------------------- libraries ---

INSERT INTO cc_track_types (id, code, type_name, description, visibility, analyze_cue_points)
VALUES
    (9201, 'music', 'Music', '', true, false),
    (9202, 'sweeps', 'Sweeps', '', true, false);

-- ---------------------------------------------------------------- files ----
-- A library spread across the columns the criteria can talk about. Nulls are
-- deliberate: negations have to be shown to keep or drop them exactly as the
-- legacy query does.

INSERT INTO cc_files
    (id, name, filepath, mime, file_exists, hidden, owner_id, track_type_id,
     genre, mood, label, artist_name, album_title, track_title, description,
     year, track_number, bpm, bit_rate, sample_rate, length, cuein, cueout,
     utime, mtime, lptime)
VALUES
    -- the two reference tracks of the ambient shelf
    (9001, 'one', '/media/jazz/01-saxophone.mp3', 'audio/mpeg', true, false, 9101, 9201,
     'Ambient', 'Calm', 'Moot', 'River Ensemble', 'Field Studies', 'Morning',
     'alpha beta', '1994', 3, 90, 320000, 44100,
     '00:04:02.761625', '00:00:01.5', '00:04:00',
     '2020-01-01 00:00:00', '2020-01-01 00:00:00', '2026-01-15 00:00:00'),
    (9002, 'two', '/media/jazz/02-piano.mp3', 'audio/mpeg', true, false, 9102, 9201,
     'ambient', 'energetic', 'Nada', 'Second Artist', 'Night Hours', 'Late Night',
     'beta gamma', '2005', 11, 128, 128000, 22050,
     '00:30:00', '00:00:00', '00:29:00',
     '2026-03-31 18:00:00', '2026-03-31 18:00:00', '2025-01-01 00:00:00'),
    -- a techno track and a file with almost no metadata at all
    (9003, 'three', '/media/techno/03-machine.mp3', 'audio/mpeg', true, false, NULL, 9202,
     'Techno', NULL, NULL, NULL, NULL, 'Machine', 'gamma', '1999', NULL, NULL,
     192000, 44100, '00:02:00', NULL, NULL,
     '2025-12-31 00:00:00', '2025-12-31 00:00:00', NULL),
    (9004, 'four', '/media/misc/bare.wav', 'audio/wav', true, false, NULL, NULL,
     NULL, NULL, NULL, NULL, NULL, 'Bare', NULL, NULL, NULL, NULL,
     NULL, NULL, '00:01:00', '00:00:01', '00:00:59',
     '2019-06-01 00:00:00', '2019-06-01 00:00:00', NULL),
    -- a 100%-pure description, to show % is not a wildcard
    (9005, 'five', '/media/misc/percent.mp3', 'audio/mpeg', true, false, 9101, 9201,
     'Ambient', 'Calm', 'Nada', 'Percent Artist', 'Pure', 'One Hundred',
     '100% pure', '2021', 1, 140, 256000, 48000,
     '00:05:30', '00:00:00', '00:05:29',
     '2026-01-10 00:00:00', '2026-01-10 00:00:00', '2026-03-30 00:00:00'),
    -- the two relative-date tracks: recent and old airings
    (9006, 'six', '/media/misc/recent.mp3', 'audio/mpeg', true, false, 9102, 9201,
     'Techno', 'Calm', 'Moot', 'Recent Artist', 'Now', 'Recently Played',
     'alpha', '2024', 7, 100, 320000, 44100,
     '00:03:00', '00:00:00', '00:02:59',
     now() - interval '2 days', now() - interval '2 days', now() - interval '2 days'),
    (9007, 'seven', '/media/misc/ancient.mp3', 'audio/mpeg', true, false, 9102, 9201,
     'Techno', 'Calm', 'Moot', 'Ancient Artist', 'Then', 'Long Ago',
     'gamma', '1988', 2, 70, 96000, 8000,
     '00:00:45', '00:00:00', '00:00:44',
     now() - interval '20 days', now() - interval '20 days', now() - interval '20 days'),
    -- invisible ones: hidden from the library, and gone from disk
    (9008, 'eight', '/media/misc/hidden.mp3', 'audio/mpeg', true, true, 9101, 9201,
     'Ambient', 'Calm', 'Moot', 'Hidden Artist', 'Away', 'Hidden Track',
     'alpha beta gamma', '2020', 4, 90, 320000, 44100,
     '00:04:00', '00:00:00', '00:03:59',
     '2026-02-01 00:00:00', '2026-02-01 00:00:00', NULL),
    (9009, 'nine', '/media/misc/deleted.mp3', 'audio/mpeg', false, false, 9101, 9201,
     'Ambient', 'Calm', 'Moot', 'Deleted Artist', 'Gone', 'Missing File',
     'alpha delta', '2020', 5, 320000, 90, 44100,
     '00:04:00', '00:00:00', '00:03:59',
     '2026-02-02 00:00:00', '2026-02-02 00:00:00', NULL);

-- --------------------------------------------------------------- blocks ----
-- columns: id, name, type, length, criteriaspread
INSERT INTO cc_block (id, name, description, length, type)
VALUES
    (9501, 'genre contains amb', '', '00:10:00', 'dynamic'),
    (9502, 'genre does not contain amb', '', '00:10:00', 'dynamic'),
    (9503, 'label is not Nada', '', '00:10:00', 'dynamic'),
    (9504, 'label is not Nada and is not Moot', '', '00:10:00', 'dynamic'),
    (9505, 'mood is Calm exactly', '', '00:10:00', 'dynamic'),
    (9506, 'title starts with morn', '', '00:10:00', 'dynamic'),
    (9507, 'title ends with night', '', '00:10:00', 'dynamic'),
    (9508, 'description contains 100%', '', '00:10:00', 'dynamic'),
    (9509, 'bit rate greater than 200', '', '00:10:00', 'dynamic'),
    (9510, 'bit rate is 320', '', '00:10:00', 'dynamic'),
    (9511, 'sample rate in the range 40 50', '', '00:10:00', 'dynamic'),
    (9512, 'length is 00:04:02', '', '00:10:00', 'dynamic'),
    (9513, 'length less than ten minutes', '', '00:10:00', 'dynamic'),
    (9514, 'cuein is 00:00:01', '', '00:10:00', 'dynamic'),
    (9515, 'year between 1990 and 1999', '', '00:10:00', 'dynamic'),
    (9516, 'owner contains dj', '', '00:10:00', 'dynamic'),
    (9517, 'owner is announcer', '', '00:10:00', 'dynamic'),
    (9518, 'track type is Music', '', '00:10:00', 'dynamic'),
    (9519, 'track type is not Music', '', '00:10:00', 'dynamic'),
    (9520, 'file name starts with 01-sax', '', '00:10:00', 'dynamic'),
    (9521, 'file name contains saxophone', '', '00:10:00', 'dynamic'),
    (9522, 'file name starts with jazz', '', '00:10:00', 'dynamic'),
    (9523, 'uploaded before mid 2025', '', '00:10:00', 'dynamic'),
    (9524, 'uploaded after 2026', '', '00:10:00', 'dynamic'),
    (9525, 'last played after seven days ago', '', '00:10:00', 'dynamic'),
    (9526, 'last played before 2020', '', '00:10:00', 'dynamic'),
    (9527, 'last played between 2024 and 2026', '', '00:10:00', 'dynamic'),
    (9528, 'uploaded between jan and feb 2026', '', '00:10:00', 'dynamic'),
    (9529, 'uploaded in the range 2026', '', '00:10:00', 'dynamic'),
    (9530, 'uploaded before today', '', '00:10:00', 'dynamic'),
    (9531, 'alpha and (beta or gamma)', '', '00:10:00', 'dynamic'),
    (9532, '(beta or gamma) and alpha', '', '00:10:00', 'dynamic'),
    (9533, 'ambient and techno', '', '00:10:00', 'dynamic'),
    (9534, 'ambient and calm', '', '00:10:00', 'dynamic'),
    (9535, 'everything, newest first', '', '00:10:00', 'dynamic'),
    (9536, 'everything, oldest first', '', '00:10:00', 'dynamic'),
    (9537, 'everything, most recent play', '', '00:10:00', 'dynamic'),
    (9538, 'everything, least recent play', '', '00:10:00', 'dynamic');

-- one helper insert per shape of criteria row, keeping the matrix readable
INSERT INTO cc_blockcriteria (block_id, criteria, modifier, value, extra, criteriagroup)
VALUES
    (9501, 'genre', 'contains', 'amb', NULL, 0),
    (9502, 'genre', 'does not contain', 'amb', NULL, 0),
    (9503, 'label', 'is not', 'Nada', NULL, 0),
    (9504, 'label', 'is not', 'Nada', NULL, 0),
    (9504, 'label', 'is not', 'Moot', NULL, 1),
    (9505, 'mood', 'is', 'Calm', NULL, 0),
    (9506, 'track_title', 'starts with', 'morn', NULL, 0),
    (9507, 'track_title', 'ends with', 'night', NULL, 0),
    (9508, 'description', 'contains', '100%', NULL, 0),
    (9509, 'bit_rate', 'is greater than', '200', NULL, 0),
    (9510, 'bit_rate', 'is', '320', NULL, 0),
    (9511, 'sample_rate', 'is in the range', '40', '50', 0),
    (9512, 'length', 'is', '00:04:02', NULL, 0),
    (9513, 'length', 'is less than', '00:10:00', NULL, 0),
    (9514, 'cuein', 'is', '00:00:01', NULL, 0),
    (9515, 'year', 'is in the range', '1990', '1999', 0),
    (9516, 'owner_id', 'contains', 'dj', NULL, 0),
    (9517, 'owner_id', 'is', 'announcer', NULL, 0),
    (9518, 'track_type_id', 'is', '9201', NULL, 0),
    (9519, 'track_type_id', 'is not', '9201', NULL, 0),
    (9520, 'filepath', 'starts with', '01-sax', NULL, 0),
    (9521, 'filepath', 'contains', 'saxophone', NULL, 0),
    (9522, 'filepath', 'starts with', 'jazz', NULL, 0),
    (9523, 'utime', 'before', '2025-06-01 00:00:00', NULL, 0),
    (9524, 'utime', 'after', '2026-01-01 00:00:00', NULL, 0),
    (9525, 'lptime', 'after', '7 days ago', NULL, 0),
    (9526, 'lptime', 'before', '2020-01-01 00:00:00', NULL, 0),
    (9527, 'lptime', 'between', '2024-01-01 00:00:00', '2026-01-01 00:00:00', 0),
    (9528, 'utime', 'between', '2026-01-01 00:00:00', '2026-02-01 00:00:00', 0),
    (9529, 'utime', 'is in the range', '2026-01-01 00:00:00', '2026-02-01 00:00:00', 0),
    (9530, 'utime', 'before', 'now{Y-m-d} 00:00:00', NULL, 0),
    (9531, 'description', 'contains', 'alpha', NULL, 0),
    (9531, 'description', 'contains', 'beta', NULL, 1),
    (9531, 'description', 'contains', 'gamma', NULL, 1),
    (9532, 'description', 'contains', 'beta', NULL, 1),
    (9532, 'description', 'contains', 'gamma', NULL, 1),
    (9532, 'description', 'contains', 'alpha', NULL, 0),
    (9533, 'genre', 'is', 'Ambient', NULL, 0),
    (9533, 'genre', 'is', 'Techno', NULL, 1),
    (9534, 'genre', 'is', 'Ambient', NULL, 0),
    (9534, 'mood', 'is', 'Calm', NULL, 0);

-- sort, limit and the two switches, stored for every block the way
-- storeCriteriaIntoDb() does it
INSERT INTO cc_blockcriteria (block_id, criteria, modifier, value, extra, criteriagroup)
SELECT id, 'limit', 'hours', '1', NULL, 0 FROM cc_block WHERE id BETWEEN 9500 AND 9599;

INSERT INTO cc_blockcriteria (block_id, criteria, modifier, value, extra, criteriagroup)
SELECT id, 'repeat_tracks', 'N/A', '0', NULL, 0 FROM cc_block WHERE id BETWEEN 9500 AND 9599;

INSERT INTO cc_blockcriteria (block_id, criteria, modifier, value, extra, criteriagroup)
SELECT id, 'overflow_tracks', 'N/A', '0', NULL, 0 FROM cc_block WHERE id BETWEEN 9500 AND 9599;

INSERT INTO cc_blockcriteria (block_id, criteria, modifier, value, extra, criteriagroup)
VALUES
    (9501, 'sort', 'N/A', 'newest', NULL, 0),
    (9502, 'sort', 'N/A', 'newest', NULL, 0),
    (9503, 'sort', 'N/A', 'newest', NULL, 0),
    (9504, 'sort', 'N/A', 'newest', NULL, 0),
    (9505, 'sort', 'N/A', 'newest', NULL, 0),
    (9506, 'sort', 'N/A', 'newest', NULL, 0),
    (9507, 'sort', 'N/A', 'newest', NULL, 0),
    (9508, 'sort', 'N/A', 'newest', NULL, 0),
    (9509, 'sort', 'N/A', 'newest', NULL, 0),
    (9510, 'sort', 'N/A', 'newest', NULL, 0),
    (9511, 'sort', 'N/A', 'newest', NULL, 0),
    (9512, 'sort', 'N/A', 'newest', NULL, 0),
    (9513, 'sort', 'N/A', 'newest', NULL, 0),
    (9514, 'sort', 'N/A', 'newest', NULL, 0),
    (9515, 'sort', 'N/A', 'newest', NULL, 0),
    (9516, 'sort', 'N/A', 'newest', NULL, 0),
    (9517, 'sort', 'N/A', 'newest', NULL, 0),
    (9518, 'sort', 'N/A', 'newest', NULL, 0),
    (9519, 'sort', 'N/A', 'newest', NULL, 0),
    (9520, 'sort', 'N/A', 'newest', NULL, 0),
    (9521, 'sort', 'N/A', 'newest', NULL, 0),
    (9522, 'sort', 'N/A', 'newest', NULL, 0),
    (9523, 'sort', 'N/A', 'newest', NULL, 0),
    (9524, 'sort', 'N/A', 'newest', NULL, 0),
    (9525, 'sort', 'N/A', 'newest', NULL, 0),
    (9526, 'sort', 'N/A', 'newest', NULL, 0),
    (9527, 'sort', 'N/A', 'newest', NULL, 0),
    (9528, 'sort', 'N/A', 'newest', NULL, 0),
    (9529, 'sort', 'N/A', 'newest', NULL, 0),
    (9530, 'sort', 'N/A', 'newest', NULL, 0),
    (9531, 'sort', 'N/A', 'newest', NULL, 0),
    (9532, 'sort', 'N/A', 'newest', NULL, 0),
    (9533, 'sort', 'N/A', 'newest', NULL, 0),
    (9534, 'sort', 'N/A', 'newest', NULL, 0),
    (9535, 'sort', 'N/A', 'newest', NULL, 0),
    (9536, 'sort', 'N/A', 'oldest', NULL, 0),
    (9537, 'sort', 'N/A', 'mostrecentplay', NULL, 0),
    (9538, 'sort', 'N/A', 'leastrecentplay', NULL, 0);

COMMIT;
