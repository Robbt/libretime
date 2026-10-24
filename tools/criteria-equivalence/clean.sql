-- Remove everything the criteria harness wrote.
DELETE FROM cc_blockcriteria WHERE block_id BETWEEN 9500 AND 9599;
DELETE FROM cc_blockcontents WHERE block_id BETWEEN 9500 AND 9599;
DELETE FROM cc_block WHERE id BETWEEN 9500 AND 9599;
DELETE FROM cc_files WHERE id BETWEEN 9000 AND 9199;
DELETE FROM cc_track_types WHERE id BETWEEN 9200 AND 9299;
DELETE FROM cc_subjs WHERE id BETWEEN 9100 AND 9199;
