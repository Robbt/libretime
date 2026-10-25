<?php

/**
 * Emit the fixture scenarios both sides of the harness will run.
 *
 * The payloads come out of php's own test data class - ShowServiceData - so the
 * python side is compared against exactly what the legacy unit tests feed the
 * service, not against a transcription of it. The overrides and the call
 * sequence are transcribed from tests/application/services/database/
 * ShowServiceDbTest.php, one entry per test, and are the part a reviewer should
 * check against that file.
 *
 * Run inside the legacy container:
 *   docker exec -i <legacy> php /tools/dump-scenarios.php > scenarios.json
 *
 * Covered here: the create paths (every repeat kind, with and without
 * rebroadcast, linked) and the delete paths. The edit paths - editing a
 * repeating instance, changing repeat day or type, which also move content in
 * cc_schedule - need the update path of the port and come with it.
 */

header('Content-Type: application/json');

$scenarios = [
    [
        'name' => 'create_no_repeat',
        'base' => 'getNoRepeatNoRRData',
        'overrides' => [],
        'steps' => [['addUpdateShow']],
        'spec' => 'test_createNoRepeatNoRRShow.yml',
    ],
    [
        'name' => 'create_no_repeat_recorded',
        'base' => 'getNoRepeatRRData',
        'overrides' => [],
        'steps' => [['addUpdateShow']],
        'spec' => 'test_createNoRepeatRRShow.yml',
    ],
    [
        'name' => 'create_weekly',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => [],
        'steps' => [['addUpdateShow']],
        'spec' => 'test_createWeeklyRepeatNoEndNoRRShow.yml',
    ],
    [
        'name' => 'create_biweekly',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_repeat_type' => '1'],
        'steps' => [['addUpdateShow']],
        'spec' => 'test_createBiWeeklyRepeatNoEndNoRRShow.yml',
    ],
    [
        'name' => 'create_triweekly',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_repeat_type' => '4'],
        'steps' => [['addUpdateShow']],
        'spec' => 'test_createTriWeeklyRepeatNoEndNoRRShow.yml',
    ],
    [
        'name' => 'create_quadweekly',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_repeat_type' => '5'],
        'steps' => [['addUpdateShow']],
        'spec' => 'test_createQuadWeeklyRepeatNoEndNoRRShow.yml',
    ],
    [
        'name' => 'create_monthly_monthly',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        // repeat type 2 means "monthly", and add_show_monthly_repeat_type picks
        // monthly-on-a-date (2) over monthly-on-a-weekday (3).
        'overrides' => ['add_show_repeat_type' => '2'],
        'steps' => [['addUpdateShow']],
        'spec' => 'test_createMonthlyMonthlyRepeatNoEndNoRRShow.yml',
    ],
    [
        'name' => 'create_monthly_weekly',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_repeat_type' => '3'],
        'steps' => [['addUpdateShow']],
        'spec' => 'test_createMonthlyWeeklyRepeatNoEndNoRRShow.yml',
    ],
    [
        'name' => 'create_weekly_three_days',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_day_check' => [5, 1, 2]],
        'steps' => [['addUpdateShow']],
        'spec' => null,
    ],
    [
        'name' => 'create_linked',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_linked' => 1],
        'steps' => [['addUpdateShow']],
        'spec' => 'test_createLinkedShow.yml',
    ],
    [
        'name' => 'create_with_end',
        'base' => 'getWeeklyRepeatWithEndNoRRData',
        'overrides' => [],
        'steps' => [['addUpdateShow']],
        'spec' => null,
    ],
    [
        'name' => 'delete_one_instance',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => [],
        'steps' => [['addUpdateShow'], ['deleteShow', 3, true]],
        'spec' => 'test_deleteShowInstance.yml',
    ],
    [
        'name' => 'delete_whole_show',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => [],
        'steps' => [['addUpdateShow'], ['deleteShow', 1]],
        'spec' => 'test_deleteRepeatingShow.yml',
    ],
    [
        'name' => 'delete_instances_and_following',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_day_check' => [5, 1, 2]],
        'steps' => [
            ['addUpdateShow'],
            ['deleteShow', 1, true],
            ['deleteShow', 6, true],
            ['deleteShow', 8, true],
            ['deleteShow', 4],
        ],
        'spec' => 'test_deleteShowInstanceAndAllFollowing.yml',
    ],
];

$out = [];

foreach ($scenarios as $scenario) {
    $payload = ShowServiceData::{$scenario['base']}();

    foreach ($scenario['overrides'] as $key => $value) {
        $payload[$key] = $value;
    }

    $out[] = [
        'name' => $scenario['name'],
        'spec' => $scenario['spec'],
        'payload' => $payload,
        'steps' => $scenario['steps'],
    ];
}

echo json_encode($out, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES), "\n";
