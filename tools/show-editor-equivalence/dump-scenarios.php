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
 * Covered here: every path the legacy unit tests assert a dataset for - create
 * (each repeat kind, with and without rebroadcast, linked), delete, and edit
 * (change the end date, the repeat type, the repeat days, and split one instance
 * out of a repeating sequence).
 *
 * A step is an object: which service method to call, optionally what to change in
 * the payload first ("set"), and for the two calls that are not "save this
 * payload", what they were passed in the test. "seedSchedule" is not a legacy call
 * at all - see run-php.php for why the two legs do it in sql.
 */

$legacyRoot = getenv('LEGACY_ROOT') ?: '/var/www/html';
require_once $legacyRoot . '/application/configs/constants.php';
require_once '/var/www/tests/application/testdata/ShowServiceData.php';

header('Content-Type: application/json');

$scenarios = [
    [
        'name' => 'create_no_repeat',
        'base' => 'getNoRepeatNoRRData',
        'overrides' => [],
        'steps' => [['call' => 'addUpdateShow']],
        'spec' => 'test_createNoRepeatNoRRShow.yml',
    ],
    [
        'name' => 'create_no_repeat_recorded',
        'base' => 'getNoRepeatRRData',
        'overrides' => [],
        'steps' => [['call' => 'addUpdateShow']],
        'spec' => 'test_createNoRepeatRRShow.yml',
    ],
    [
        'name' => 'create_weekly',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => [],
        'steps' => [['call' => 'addUpdateShow']],
        'spec' => 'test_createWeeklyRepeatNoEndNoRRShow.yml',
    ],
    [
        'name' => 'create_biweekly',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_repeat_type' => '1'],
        'steps' => [['call' => 'addUpdateShow']],
        'spec' => 'test_createBiWeeklyRepeatNoEndNoRRShow.yml',
    ],
    [
        'name' => 'create_triweekly',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_repeat_type' => '4'],
        'steps' => [['call' => 'addUpdateShow']],
        'spec' => 'test_createTriWeeklyRepeatNoEndNoRRShow.yml',
    ],
    [
        'name' => 'create_quadweekly',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_repeat_type' => '5'],
        'steps' => [['call' => 'addUpdateShow']],
        'spec' => 'test_createQuadWeeklyRepeatNoEndNoRRShow.yml',
    ],
    [
        'name' => 'create_monthly_monthly',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        // repeat type 2 means "monthly", and add_show_monthly_repeat_type picks
        // monthly-on-a-date (2) over monthly-on-a-weekday (3).
        'overrides' => ['add_show_repeat_type' => '2'],
        'steps' => [['call' => 'addUpdateShow']],
        'spec' => 'test_createMonthlyMonthlyRepeatNoEndNoRRShow.yml',
    ],
    [
        'name' => 'create_monthly_weekly',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_repeat_type' => '3'],
        'steps' => [['call' => 'addUpdateShow']],
        'spec' => 'test_createMonthlyWeeklyRepeatNoEndNoRRShow.yml',
    ],
    [
        'name' => 'create_weekly_three_days',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_day_check' => [5, 1, 2]],
        'steps' => [['call' => 'addUpdateShow']],
        'spec' => null,
    ],
    [
        'name' => 'create_linked',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_linked' => 1],
        'steps' => [['call' => 'addUpdateShow']],
        'spec' => 'test_createLinkedShow.yml',
    ],
    [
        'name' => 'create_with_end',
        'base' => 'getWeeklyRepeatWithEndNoRRData',
        'overrides' => [],
        'steps' => [['call' => 'addUpdateShow']],
        'spec' => null,
    ],
    [
        'name' => 'delete_one_instance',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => [],
        'steps' => [['call' => 'addUpdateShow'], ['call' => 'deleteShow', 'args' => [3, true]]],
        'spec' => 'test_deleteShowInstance.yml',
    ],
    [
        'name' => 'delete_whole_show',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => [],
        'steps' => [['call' => 'addUpdateShow'], ['call' => 'deleteShow', 'args' => [1]]],
        'spec' => 'test_deleteRepeatingShow.yml',
    ],
    [
        'name' => 'delete_instances_and_following',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => ['add_show_day_check' => [5, 1, 2]],
        'steps' => [
            ['call' => 'addUpdateShow'],
            ['call' => 'deleteShow', 'args' => [1, true]],
            ['call' => 'deleteShow', 'args' => [6, true]],
            ['call' => 'deleteShow', 'args' => [8, true]],
            ['call' => 'deleteShow', 'args' => [4]],
        ],
        'spec' => 'test_deleteShowInstanceAndAllFollowing.yml',
    ],
    // -- editing an existing show ------------------------------------------------
    //
    // Each of these is a step sequence transcribed from ShowServiceDbTest: the
    // first addUpdateShow creates the show, the second posts the edited payload
    // back with add_show_id set, which is what the calendar does.
    [
        'name' => 'edit_change_no_end',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => [],
        'steps' => [
            ['call' => 'addUpdateShow'],
            [
                'call' => 'addUpdateShow',
                'set' => [
                    'add_show_end_date' => '2044-01-09',
                    'add_show_no_end' => 0,
                    'add_show_id' => 1,
                ],
            ],
        ],
        'spec' => 'test_editRepeatingShowChangeNoEndOption.yml',
    ],
    [
        'name' => 'edit_weekly_to_no_repeat',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => [],
        'steps' => [
            ['call' => 'addUpdateShow'],
            ['call' => 'addUpdateShow', 'set' => ['add_show_repeats' => 0, 'add_show_id' => 1]],
        ],
        'spec' => 'test_weeklyToNoRepeat.yml',
    ],
    [
        'name' => 'edit_weekly_to_biweekly',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => [],
        'steps' => [
            ['call' => 'addUpdateShow'],
            ['call' => 'addUpdateShow', 'set' => ['add_show_repeat_type' => 1, 'add_show_id' => 1]],
        ],
        'spec' => 'test_weeklyToBiWeekly.yml',
    ],
    [
        'name' => 'edit_repeating_instance',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => [],
        'steps' => [
            ['call' => 'addUpdateShow'],
            [
                'call' => 'editRepeatingShowInstance',
                'payload' => 'getEditRepeatInstanceData',
            ],
        ],
        'spec' => 'test_editRepeatingShowInstance.yml',
    ],
    [
        'name' => 'moves_forward_in_calendar',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        // 'add_show_repeat_type' => '1' - biweekly, the fixture the test uses for
        // this: two weeks of gap is what makes the extra populate pass visible.
        'overrides' => ['add_show_repeat_type' => '1'],
        'steps' => [
            ['call' => 'addUpdateShow'],
            // The calendar's "load more": same show, further end, instances filled.
            ['call' => 'delegateInstanceCreation', 'end' => '2044-03-12', 'fill' => true],
        ],
        'spec' => 'test_repeatShowCreationWhenUserMovesForwardInCalendar.yml',
    ],
    [
        'name' => 'edit_remove_first_repeat_day',
        'base' => 'getWeeklyRepeatNoEndNoRRData',
        'overrides' => [
            'add_show_start_date' => '2044-01-29',
            'add_show_day_check' => [5, 6],
            'add_show_linked' => 1,
        ],
        'steps' => [
            ['call' => 'addUpdateShow'],
            // Content queued inside an instance: the point of the scenario is what
            // an edit does to it.
            ['call' => 'seedSchedule', 'instance' => 1],
            ['call' => 'addUpdateShow', 'set' => ['add_show_day_check' => [6], 'add_show_id' => 1]],
        ],
        // Upstream has this dataset twice, byte for byte, under
        // test_changeRepeatDayUpdatesScheduleCorrectly.yml as well: two tests that
        // differ only in name. One scenario here covers both.
        'spec' => 'test_removeFirstRepeatShowDayUpdatesScheduleCorrectly.yml',
    ],
];

$out = [];

foreach ($scenarios as $scenario) {
    $payload = ShowServiceData::{$scenario['base']}();

    foreach ($scenario['overrides'] as $key => $value) {
        $payload[$key] = $value;
    }

    $steps = [];

    foreach ($scenario['steps'] as $step) {
        // A step that names a second fixture payload (the calendar's "edit this
        // instance" dialog posts its own shape) has it inlined, so the python leg
        // reads the same array rather than needing php's test data class.
        if (isset($step['payload'])) {
            $step['data'] = ShowServiceData::{$step['payload']}();
            unset($step['payload']);
        }

        $steps[] = $step;
    }

    $out[] = [
        'name' => $scenario['name'],
        'spec' => $scenario['spec'],
        'payload' => $payload,
        'steps' => $steps,
    ];
}

echo json_encode($out, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES), "\n";
