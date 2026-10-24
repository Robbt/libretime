<?php

/**
 * Run the LEGACY smart block criteria compiler over the fixture blocks.
 *
 * One line per block: "<block id>|<file ids in the order the criteria return
 * them>". Called by ab.sh; the Python side prints the same lines and the two
 * outputs are compared.
 */
$legacyRoot = getenv('LEGACY_ROOT') ?: '/var/www/html';
require_once $legacyRoot . '/application/preload.php';
require_once $legacyRoot . '/application/check.php';
configureDatabase();

$pdo = Propel::getConnection();
$blocks = $pdo
    ->query('select id from cc_block where id between 9500 and 9599 order by id')
    ->fetchAll(PDO::FETCH_COLUMN);

if (count($blocks) === 0) {
    fwrite(STDERR, "php: no fixture blocks, run seed.sql first\n");

    exit(1);
}

foreach ($blocks as $blockId) {
    $model = new Application_Model_Block($blockId);

    try {
        $result = $model->getListofFilesMeetCriteria();
    } catch (Throwable $e) {
        printf("%d|ERROR %s\n", $blockId, $e->getMessage());

        continue;
    }

    /*
     * The legacy method swallows query exceptions and returns nothing at all,
     * which would otherwise look like a block that matches no files.
     */
    if (!is_array($result) || !isset($result['files'])) {
        printf("%d|BROKEN\n", $blockId);

        continue;
    }

    $files = [];
    foreach ($result['files'] as $file) {
        $files[] = $file->getDbId();
    }

    printf("%d|%s\n", $blockId, implode(',', $files));
}
