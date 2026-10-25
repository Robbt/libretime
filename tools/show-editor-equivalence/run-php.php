<?php

/**
 * Run every fixture scenario through the LEGACY show editor and dump the result.
 *
 * Each scenario starts from the same two-row fixture state the legacy unit tests
 * use, runs its calls against Application_Service_ShowService, and prints the five
 * tables the legacy tests assert on. ab.sh runs the python port the same way and
 * compares.
 *
 * The fixture state includes shows_populated_until = 2044-02-07, which is what
 * makes this deterministic: instance creation stops at that date rather than at
 * "now", so both sides are asked to build the same 6 instances and a rerun next
 * year gives the same answer.
 */

$legacyRoot = getenv('LEGACY_ROOT') ?: '/var/www/html';
// Where the scenario payloads and the fixture scripts live. In the development
// container they are mounted at /tools and the legacy tests at /var/www/tests; on a
// plain host (tools/equivalence-run.sh) they are paths in this checkout. The defaults
// keep container mode working untouched.
$fixtures = getenv('AB_FIXTURES') ?: '/tools';
$testsRoot = getenv('LEGACY_TESTS_DIR') ?: '/var/www/tests';

require_once $legacyRoot . '/application/preload.php';
require_once $legacyRoot . '/application/check.php';
require_once $legacyRoot . '/application/configs/constants.php';
require_once $testsRoot . '/application/testdata/ShowServiceData.php';
configureDatabase();

$scenarios = json_decode(file_get_contents($fixtures . '/scenarios.json'), true);

/**
 * Put one item inside a show instance, the way the library's drag-and-drop does.
 *
 * The upstream test drives Application_Model_Scheduler::scheduleAfter() for this,
 * which for a linked show copies the content into every instance - a behaviour of
 * the scheduler, not of the show editor, and the scheduler port lives on another
 * branch. Both legs of this harness therefore seed with the same two inserts: the
 * scenario is about what an edit does to a queued item, and that needs an item, not
 * a scheduler. The rows carry the same values scheduleAfter leaves - a clip of the
 * file's length at the instance's start, marked as inside the show.
 */
function seedScheduledItem(PDO $pdo, int $instanceId): void
{
    $pdo->exec(
        "INSERT INTO cc_files (id, track_title, mime, length, cuein, cueout)
         VALUES (1, 'seeded item', 'audio/mpeg', '00:04:32', '00:00:00', '00:04:32')"
    );
    $pdo->exec("SELECT setval(pg_get_serial_sequence('cc_files', 'id'), 2, false)");

    // Every instance of the same show gets the item: that is what a linked show
    // means, and the dataset upstream asserts exactly this.
    $pdo->exec(
        "INSERT INTO cc_schedule (starts, ends, instance_id, file_id, clip_length, fade_in, fade_out,
                                  cue_in, cue_out, position, playout_status, broadcasted)
         SELECT i.starts,
                i.starts + INTERVAL '00:04:32',
                i.id,
                1,
                '00:04:32',
                '00:00:00.5',
                '00:00:00.5',
                '00:00:00',
                '00:04:32',
                0,
                1,
                0
         FROM cc_show_instances i
         WHERE i.show_id = (SELECT show_id FROM cc_show_instances WHERE id = {$instanceId})
           AND i.rebroadcast = 0
         ORDER BY i.id"
    );
}

/**
 * The identity Application_Service_ShowService asks for.
 *
 * addUpdateShow refuses anybody who is not admin or program manager, and reads it
 * through Application_Service_UserService, which reads Zend_Auth. TestHelper::
 * loginUser() does the same through the real auth adapter; the adapter needs a
 * known password in the database, and this fixture user has one, so the identity
 * is written the way the adapter would have written it.
 */
function loginAsAdmin(PDO $pdo): int
{
    $stmt = $pdo->prepare('SELECT id FROM cc_subjs WHERE login = :login');
    $stmt->execute([':login' => 'show-editor-fixture']);
    $row = $stmt->fetch();

    if (!$row) {
        $pdo->exec(
            "INSERT INTO cc_subjs (type, login, pass, first_name, last_name, email) "
            . "VALUES ('A', 'show-editor-fixture', '" . md5('admin') . "', 'Fixture', 'Admin', 'fixture@example.org')"
        );
        $stmt->execute([':login' => 'show-editor-fixture']);
        $row = $stmt->fetch();
    }

    $identity = (object) [
        'id' => (int) $row['id'],
        'login' => 'show-editor-fixture',
        'type' => 'A',
    ];

    if (!Zend_Session::isStarted()) {
        @Zend_Session::start();
    }

    $auth = Zend_Auth::getInstance();
    $auth->getStorage()->write($identity);

    return (int) $row['id'];
}

function resetFixtureState(PDO $pdo, string $toolsDir): void
{
    foreach (['clean.sql', 'seed.sql'] as $file) {
        $sql = file_get_contents("$toolsDir/$file");
        $pdo->exec($sql);
    }
}

/**
 * One line per row, as the database renders it.
 *
 * to_jsonb rather than a list of columns: booleans then come out true/false
 * instead of pdo_pgsql\'s "1" and "", intervals their own canonical text, and
 * nulls as null. The python side dumps with the same query for the same reason -
 * a comparison should not have to guess what a column meant.
 */
function dumpTable(PDO $pdo, string $label, string $projection, string $table): array
{
    $rows = $pdo->query("select to_jsonb(t)::text as j from ($projection) t")->fetchAll(PDO::FETCH_COLUMN);
    $lines = [];

    foreach ($rows as $json) {
        $lines[] = $label . '|' . $json;
    }

    return $lines;
}

$pdo = Propel::getConnection();
loginAsAdmin($pdo);

foreach ($scenarios as $scenario) {
    resetFixtureState($pdo, $fixtures);

    $payload = $scenario['payload'];
    $showId = null;

    foreach ($scenario['steps'] as $step) {
        $call = $step['call'];

        // An edit posts the same payload back with the fields the user changed,
        // which is what "set" says: mutate the running payload, then send it.
        foreach ($step['set'] ?? [] as $key => $value) {
            $payload[$key] = $value;
        }

        if ($call === 'addUpdateShow') {
            // A fresh service per call, as the legacy tests construct one per
            // action: it carries the repeat type and recorded flags parsed from
            // the payload, and the id once the row exists.
            if ($showId === null) {
                $service = new Application_Service_ShowService(null, $payload);
            } else {
                $service = new Application_Service_ShowService(null, $payload, true);
            }

            $showId = $service->addUpdateShow($payload);
        } elseif ($call === 'deleteShow') {
            $service->deleteShow(...$step['args']);
        } elseif ($call === 'editRepeatingShowInstance') {
            // The upstream test builds a second service from the instance payload
            // before calling, so this does the same rather than reusing $service.
            $editData = $step['data'];

            $service = new Application_Service_ShowService(null, $editData);
            $service->editRepeatingShowInstance($editData);
        } elseif ($call === 'delegateInstanceCreation') {
            $service->delegateInstanceCreation(
                null,
                new DateTime($step['end'], new DateTimeZone('UTC')),
                (bool) ($step['fill'] ?? false)
            );
        } elseif ($call === 'seedSchedule') {
            seedScheduledItem($pdo, (int) $step['instance']);
        }
    }

    // The same projections, in the same order, as ShowServiceDbTest.
    $lines = ["### {$scenario['name']}"];

    foreach ([
        ['cc_show', 'select * from cc_show order by id'],
        ['cc_show_days', 'select * from cc_show_days order by id'],
        [
            'cc_show_instances',
            'select id, starts, ends, show_id, record, rebroadcast, instance_id, modified_instance '
                . 'from cc_show_instances order by id',
        ],
        ['cc_show_rebroadcast', 'select * from cc_show_rebroadcast order by id'],
        ['cc_show_hosts', 'select * from cc_show_hosts order by id'],
        [
            'cc_schedule',
            'select id, starts, ends, file_id, clip_length, fade_in, fade_out, cue_in, '
                . 'cue_out, instance_id, playout_status from cc_schedule order by id',
        ],
    ] as [$table, $projection]) {
        $lines = array_merge($lines, dumpTable($pdo, $table, $projection, $table));
    }

    echo implode("\n", $lines), "\n";
}

// The fixture user is not part of any station.
$pdo->exec("DELETE FROM cc_subjs WHERE login = 'show-editor-fixture'");
$pdo->exec('DELETE FROM cc_show_hosts WHERE show_id IN (SELECT id FROM cc_show WHERE name = \'test show\')');
