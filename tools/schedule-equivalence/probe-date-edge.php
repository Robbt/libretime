<?php

/**
 * Probe 2 - the tricky PHP DateTime behaviours the builder relies on:
 * setTime()/setDate() offset re-resolution, gap/fold handling, DatePeriod
 * starting in a DST gap, and the monthly helpers.
 */
$legacyRoot = getenv('LEGACY_ROOT') ?: '/var/www/html';
require_once $legacyRoot . '/application/preload.php';
require_once $legacyRoot . '/application/check.php';
configureDatabase();

function dump(string $label, DateTime $dt): void
{
    printf(
        "%-46s %s %-7s off=%s utc=%s\n",
        $label,
        $dt->format('Y-m-d H:i:s'),
        $dt->getTimezone()->getName(),
        $dt->format('P'),
        (clone $dt)->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i:s')
    );
}

$berlin = new DateTimeZone('Europe/Berlin');
$ny = new DateTimeZone('America/New_York');

echo "=== a. setTime() re-resolves the offset?\n";
$d = new DateTime('2026-03-29 00:00:00', $berlin);      // pre-transition offset +01:00
dump('before setTime (00:00 Berlin)', $d);
$d2 = clone $d;
$d2->setTime(2, 30, 0);                                  // 02:30 does not exist that day
dump('setTime(2,30) gap', $d2);
$d3 = clone $d;
$d3->setTime(3, 30, 0);
dump('setTime(3,30)', $d3);

$e = new DateTime('2026-10-25 00:00:00', $berlin);      // fall-back day, offset +02:00 at midnight
dump('before setTime (fold day)', $e);
$e2 = clone $e;
$e2->setTime(2, 30, 0);                                  // ambiguous
dump('setTime(2,30) fold', $e2);

$f = new DateTime('2026-03-08 00:00:00', $ny);
$f2 = clone $f;
$f2->setTime(2, 30, 0);
dump('NY setTime(2,30) gap', $f2);

echo "\n=== b. setDate() day clamping (PHP rolls over, does not clamp)\n";
$g = new DateTime('2026-01-31 12:00:00', $berlin);
$g->setDate(2026, 2, 31);
dump('setDate(2026,2,31)', $g);

echo "\n=== c. DatePeriod starting inside a DST gap\n";
$gapStart = new DateTime('2026-03-29 02:30:00', $berlin);
dump('gap start', $gapStart);
$period = new DatePeriod($gapStart, new DateInterval('P7D'), new DateTime('2026-04-20 00:00:00', $berlin));
foreach ($period as $i => $dt) {
    dump("period[$i]", $dt);
}

echo "\n=== d. DatePeriod end comparison uses absolute time\n";
$p2 = new DatePeriod(
    new DateTime('2026-03-01 23:00:00', $berlin),
    new DateInterval('P7D'),
    new DateTime('2026-03-22 22:00:00', $berlin)   // 2026-03-22 22:00 Berlin == 21:00 UTC
);
foreach ($p2 as $i => $dt) {
    dump("endperiod[$i]", $dt);
}

echo "\n=== e. monthly-weekly with fifth-week skipping\n";
foreach (['America/New_York', 'Europe/Berlin'] as $zone) {
    $cur = new DateTime('2026-01-01 00:00:00', new DateTimeZone('UTC'));
    $out = [];
    for ($i = 0; $i < 5; ++$i) {
        $cur->add(new DateInterval('P1M'));
        $next = Application_Service_ShowService::getNextMonthlyWeeklyRepeatDate(
            $cur,
            $zone,
            '20:00:00',
            'fifth',
            'Thursday'
        );
        $out[] = $next->format('Y-m-d H:i:s') . '(' . $next->format('l') . ')=' . (clone $next)->setTimezone(new DateTimeZone('UTC'))->format('H:i');
        $cur = clone $next;
        $cur->setTimezone(new DateTimeZone('UTC'));
    }
    printf("%-18s fifth Thursday -> %s\n", $zone, implode(' | ', $out));
}

echo "\n=== f. monthly-monthly crossing a gap month, and fold/gap days\n";
$svc = new ReflectionClass('Application_Service_ShowService');
$mth = $svc->getMethod('getNextMonthlyMonthlyRepeatDate');
$mth->setAccessible(true);
$inst = $svc->newInstanceWithoutConstructor();
$s = new DateTime('2026-08-31 02:30:00', $berlin);
$out = [];
for ($i = 0; $i < 4; ++$i) {
    $s = $mth->invoke($inst, $s, 'Europe/Berlin', '02:30:00');
    $out[] = $s->format('Y-m-d H:i:s') . '/' . (clone $s)->setTimezone(new DateTimeZone('UTC'))->format('Y-m-d H:i:s');
}
echo 'berlin 31st 02:30 -> ' . implode(' | ', $out) . "\n";

echo "\n=== g. duration formats\n";
$cw = $svc->getMethod('createUTCStartEndDateTime');
$cw->setAccessible(true);
foreach (['01:00', '02:30:30', '24:00', '00:45'] as $dur) {
    [$s2, $e3] = $cw->invoke($inst, new DateTime('2026-06-01 21:00:00', $berlin), $dur);
    printf("%-10s -> %s .. %s\n", $dur, $s2->format('Y-m-d H:i:s'), $e3->format('Y-m-d H:i:s'));
}
