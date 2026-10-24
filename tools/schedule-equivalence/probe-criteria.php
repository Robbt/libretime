<?php

/**
 * Probe: dump the exact SQL + rows that Application_Service_ShowService::getShowDaysInRange()
 * produces, so the Python port can replicate the (subtle) Propel criteria grouping.
 */
$legacyRoot = getenv('LEGACY_ROOT') ?: '/var/www/html';
require_once $legacyRoot . '/application/preload.php';
require_once $legacyRoot . '/application/check.php';
configureDatabase();

$start = new DateTime('2026-01-01 00:00:00', new DateTimeZone('UTC'));
$end = new DateTime('2027-01-01 00:00:00', new DateTimeZone('UTC'));

$c = new Criteria();
$c->add(CcShowDaysPeer::FIRST_SHOW, $end->format(DEFAULT_TIMESTAMP_FORMAT), Criteria::LESS_THAN);
$c->addAnd(CcShowDaysPeer::LAST_SHOW, $start->format(DEFAULT_TIMESTAMP_FORMAT), Criteria::GREATER_THAN);
$c->addAnd(CcShowDaysPeer::REPEAT_TYPE, -1, Criteria::NOT_EQUAL);
$c->addOr(CcShowDaysPeer::LAST_SHOW, null, Criteria::ISNULL);

echo "--- Criteria::toString() ---\n{$c->toString()}\n";
echo "--- createSelectString() ---\n" . $c->createSelectString() . "\n";

$svc = new ReflectionClass('Application_Service_ShowService');
$m = $svc->getMethod('getShowDaysInRange');
$m->setAccessible(true);
$svcInstance = $svc->newInstanceWithoutConstructor();
$rows = $m->invoke($svcInstance, $start, $end);
echo '--- getShowDaysInRange rows: ' . count($rows) . "\n";
foreach ($rows as $r) {
    printf(
        "id=%d show=%d first=%s last=%s repeat=%d day=%s pop=%s tz=%s dur=%s rec=%d\n",
        $r->getDbId(),
        $r->getDbShowId(),
        $r->getDbFirstShow(),
        var_export($r->getDbLastShow(), true),
        $r->getDbRepeatType(),
        var_export($r->getDbDay(), true),
        var_export($r->getDbNextPopDate(), true),
        $r->getDbTimezone(),
        $r->getDbDuration(),
        $r->getDbRecord()
    );
}

echo "--- raw rows ---\n";
$raw = Application_Common_Database::prepareAndExecute(
    'SELECT id, show_id, first_show, last_show, repeat_type, day, next_pop_date, timezone, duration, record FROM cc_show_days ORDER BY id',
    [],
    'all'
);
foreach ($raw as $r) {
    echo json_encode($r) . "\n";
}
