<?php

/**
 * Schedule the fixture playlists into the fixture instances with the LEGACY
 * builder, exactly as the autoplaylist poll does: add the playlist at the end
 * of the instance, which updates the instance statistics afterwards.
 *
 * ab.sh dumps the resulting cc_schedule rows and compares them with the ones
 * the Python port writes.
 */
$legacyRoot = getenv('LEGACY_ROOT') ?: '/var/www/html';
require_once $legacyRoot . '/application/preload.php';
require_once $legacyRoot . '/application/check.php';
configureDatabase();

$cases = [
    // [instance id, playlist id]
    [8001, 8101],
    [8002, 8101],
    [8003, 8102],
    [8004, 8103],
    [8005, 8104],
];

foreach ($cases as [$instanceId, $playlistId]) {
    try {
        $instance = new Application_Model_ShowInstance($instanceId);
        $instance->addPlaylistToShow($playlistId, false);
        printf("%d %d|ok\n", $instanceId, $playlistId);
    } catch (Throwable $e) {
        printf("%d %d|ERROR %s\n", $instanceId, $playlistId, $e->getMessage());
    }
}
