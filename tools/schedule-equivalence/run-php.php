<?php

/**
 * Run the LEGACY show instance builder against the current fixture state.
 *
 * Called by ab.sh; the rows it writes are dumped by dump.sh and compared
 * against the ones the Python builder writes in the same fixture state.
 */
$legacyRoot = getenv('LEGACY_ROOT') ?: '/var/www/html';
require_once $legacyRoot . '/application/preload.php';
require_once $legacyRoot . '/application/check.php';
configureDatabase();

$until = new DateTime(getenv('AB_UNTIL') ?: '2026-12-31 00:00:00', new DateTimeZone('UTC'));

$pdo = Propel::getConnection();
$before = (int) $pdo->query('select count(*) from cc_show_instances')->fetchColumn();

Application_Model_Show::createAndFillShowInstancesPastPopulatedUntilDate($until);

$after = (int) $pdo->query('select count(*) from cc_show_instances')->fetchColumn();
$pref = $pdo->query("select valstr from cc_pref where keystr = 'shows_populated_until'")->fetchColumn();

printf(
    "php: instances %d -> %d, shows_populated_until = %s\n",
    $before,
    $after,
    $pref
);

/*
 * The legacy entry point swallows exceptions and only logs a warning, and a
 * rolled back transaction leaves the preference untouched, so a run that did
 * nothing can look like a successful one. Fail here instead.
 */
if ($pref !== $until->format('Y-m-d H:i:s')) {
    fwrite(STDERR, "php: populated-until preference was not updated, the run did not complete\n");

    exit(1);
}
