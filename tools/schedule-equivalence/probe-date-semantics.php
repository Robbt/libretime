<?php

/**
 * Probe PHP's date semantics that the schedule builder depends on, so the
 * Python port can replicate them exactly:
 *
 *  1. DatePeriod iteration + end comparison across a DST transition (weekly).
 *  2. DateTime::add(P7D) wall-clock vs absolute semantics.
 *  3. Nonexistent / ambiguous local times (DST gap and fold).
 *  4. getMonthlyWeeklyRepeatInterval() + getNextMonthlyWeeklyRepeatDate().
 *  5. getNextMonthlyMonthlyRepeatDate() for day-of-month 29..31.
 */
$legacyRoot = getenv('LEGACY_ROOT') ?: '/var/www/html';
require_once $legacyRoot . '/application/preload.php';
require_once $legacyRoot . '/application/check.php';
configureDatabase();

function show(string $label, DateTime $dt): void
{
    printf("%-38s %s (%s) -> UTC %s\n", $label, $dt->format('Y-m-d H:i:s'), $dt->getTimezone()->getName(), (clone $dt)->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i:s'));
}

echo "=== 1+2. weekly DatePeriod across Europe/Berlin DST (2026-03-29)\n";
$tz = new DateTimeZone('Europe/Berlin');
$start = new DateTime('2026-03-21 21:00:00', $tz);
$end = new DateTime('2026-04-19 00:00:00', new DateTimeZone('UTC'));
$period = new DatePeriod($start, new DateInterval('P7D'), $end);
foreach ($period as $i => $d) {
    show("period[$i]", $d);
}
$m = new DateTime('2026-03-28 21:00:00', $tz);
$m->add(new DateInterval('P7D'));
show('add P7D from 03-28 21:00', $m);

echo "\n=== 3. DST gap (02:30 does not exist) and fold (02:30 twice)\n";
show('gap 2026-03-29 02:30', new DateTime('2026-03-29 02:30:00', $tz));
show('fold 2026-10-25 02:30', new DateTime('2026-10-25 02:30:00', $tz));
show('ny gap 2026-03-08 02:30', new DateTime('2026-03-08 02:30:00', new DateTimeZone('America/New_York')));

echo "\n=== 4. monthly weekly (Nth weekday) rules\n";
$svc = new ReflectionClass('Application_Service_ShowService');
$im = $svc->getMethod('getMonthlyWeeklyRepeatInterval');
$im->setAccessible(true);
foreach (['UTC', 'America/New_York', 'Europe/Berlin', 'Asia/Tokyo'] as $zone) {
    $zoneObj = new DateTimeZone($zone);
    foreach (['2026-01-05', '2026-01-29', '2026-02-01'] as $first) {
        $dt = new DateTime($first, $zoneObj);
        [$weekNum, $dow] = Application_Service_ShowService::getMonthlyWeeklyRepeatInterval($dt);
        printf("%-18s first=%s -> %s %s\n", $zone, $first, $weekNum, $dow);
    }
    // next occurrences starting from a UTC year-month object, as the builder does
    $cur = new DateTime('2026-01-01 00:00:00', new DateTimeZone('UTC'));
    $out = [];
    for ($i = 0; $i < 4; ++$i) {
        $cur->add(new DateInterval('P1M'));
        $next = Application_Service_ShowService::getNextMonthlyWeeklyRepeatDate(
            $cur,
            $zone,
            '20:00:00',
            'first',
            'Monday'
        );
        $out[] = $next->format('Y-m-d H:i:s') . '(' . $next->format('l') . ')';
        $cur = clone $next;
        $cur->setTimezone(new DateTimeZone('UTC'));
    }
    printf("%-18s first Monday 20:00 -> %s\n", $zone, implode(' | ', $out));
}

echo "\n=== 5. monthly monthly (day of month) rules\n";
$mth = $svc->getMethod('getNextMonthlyMonthlyRepeatDate');
$mth->setAccessible(true);
foreach (['UTC', 'America/New_York'] as $zone) {
    $s = new DateTime('2026-01-31 17:00:00', new DateTimeZone($zone));
    $out = [];
    for ($i = 0; $i < 5; ++$i) {
        $s = $mth->invoke($svc->newInstanceWithoutConstructor(), $s, $zone, '17:00:00');
        $out[] = $s->format('Y-m-d H:i:s') . '(' . $s->format('l') . ')';
    }
    printf("%-18s 31st -> %s\n", $zone, implode(' | ', $out));
}

echo "\n=== 6. rebroadcast offset arithmetic (day_offset + start_time)\n";
$cw = $svc->getMethod('createUTCStartEndDateTime');
$cw->setAccessible(true);
$showStart = new DateTime('2026-05-04 21:00:00', new DateTimeZone('America/Chicago'));
foreach ([['days' => 1, 'hours' => '10', 'mins' => '00'], ['days' => 0, 'hours' => '23', 'mins' => '30']] as $off) {
    [$s, $e] = $cw->invoke($svc->newInstanceWithoutConstructor(), $showStart, '02:00', $off);
    printf("offset d=%s %s:%s -> %s .. %s\n", $off['days'], $off['hours'], $off['mins'], $s->format('Y-m-d H:i:s'), $e->format('Y-m-d H:i:s'));
}
