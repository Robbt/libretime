<?php

/**
 * Ask the LEGACY application who may change or delete each fixture object.
 *
 * ab.sh seeds the fixture station, runs this, runs the api equivalent, and
 * compares the two answers. Every rule below is the composition of predicates the
 * application itself evaluates, and each composition cites the call site it comes
 * from - the predicates come from php, and the composition is what a reviewer has
 * to check against that file.
 */

$legacyRoot = getenv('LEGACY_ROOT') ?: '/var/www/html';
require_once $legacyRoot . '/application/preload.php';
require_once $legacyRoot . '/application/check.php';
configureDatabase();

// UTYPE_* live in their own config file, loaded by the bootstrap in a web
// request. Nothing here recomputes a role from a letter without them.
if (!defined('UTYPE_SUPERADMIN')) {
    require_once $legacyRoot . '/application/configs/constants.php';
}

/**
 * The manager set used by library, playlist, block and show code.
 *
 * Application_Model_User::isUserType is the real predicate, including the fact
 * that a superadmin counts as an admin everywhere except where the list below
 * forgets it.
 */
function isManager(Application_Model_User $user): bool
{
    return $user->isUserType([UTYPE_SUPERADMIN, UTYPE_ADMIN, UTYPE_PROGRAM_MANAGER]);
}

/** Application_Model_Webstream::deleteStreams omits UTYPE_SUPERADMIN. */
function isManagerWithoutSuperadmin(Application_Model_User $user): bool
{
    return $user->isUserType([UTYPE_ADMIN, UTYPE_PROGRAM_MANAGER]);
}

/**
 * Whether a user may touch an object of one kind.
 *
 * @return array{bool, string} the verdict and the name of the rule that decided
 */
function decide(int $userId, string $kind, int $id, string $action): array
{
    $user = new Application_Model_User($userId);

    // Show structure - creating, moving, cancelling a show or its instances,
    // and saying who hosts it. ScheduleController.php:219 resizeShow, :698
    // cancelShow, :110 and :142 for the editable flag; hosts are absent there,
    // even from their own show.
    if (in_array($kind, ['show', 'showdays', 'showrebroadcast', 'showhost', 'showinstance'], true)) {
        return [isManager($user), 'manager'];
    }

    // Content placed inside an instance. Scheduler.php:146 refuses with
    // "You are not allowed to schedule show %s" unless canSchedule passes, and
    // Application_Model_User::canSchedule is manager or a row in cc_show_hosts.
    if ($kind === 'schedule') {
        $row = CcScheduleQuery::create()->findPK($id);
        $instance = CcShowInstancesQuery::create()->findPK($row->getDbInstanceId());

        return [$user->canSchedule($instance->getDbShowId()), 'can-schedule'];
    }

    // Files. StoredFile.php:399 refuses a delete by a non manager who does not
    // own the file, and LibraryController.php:352 applies the same rule to the
    // metadata form.
    if ($kind === 'file') {
        $file = Application_Model_StoredFile::RecallById($id);

        return [
            isManager($user) || $file->getFileOwnerId() == $userId,
            'owner-or-manager',
        ];
    }

    // Playlists and their contents: Playlist.php:986 with
    // playlistsNotOwnedByUser, which is "the creator of each selected id is me".
    if ($kind === 'playlist') {
        $row = CcPlaylistQuery::create()->findPK($id);

        return [
            isManager($user) || $row->getDbCreatorId() == $userId,
            'owner-or-manager',
        ];
    }

    if ($kind === 'playlistcontent') {
        $row = CcPlaylistcontentsQuery::create()->findPK($id);
        $playlist = CcPlaylistQuery::create()->findPK($row->getDbPlaylistId());

        return [
            isManager($user) || $playlist->getDbCreatorId() == $userId,
            'owner-via-parent',
        ];
    }

    // Smart blocks, their contents and their criteria: Block.php:1019 with
    // blocksNotOwnedByUser.
    if ($kind === 'smartblock') {
        $row = CcBlockQuery::create()->findPK($id);

        return [
            isManager($user) || $row->getDbCreatorId() == $userId,
            'owner-or-manager',
        ];
    }

    if ($kind === 'smartblockcontent') {
        $row = CcBlockcontentsQuery::create()->findPK($id);
        $block = CcBlockQuery::create()->findPK($row->getDbBlockId());

        return [
            isManager($user) || $block->getDbCreatorId() == $userId,
            'owner-via-parent',
        ];
    }

    if ($kind === 'smartblockcriteria') {
        $row = CcBlockcriteriaQuery::create()->findPK($id);
        $block = CcBlockQuery::create()->findPK($row->getDbBlockId());

        return [
            isManager($user) || $block->getDbCreatorId() == $userId,
            'owner-via-parent',
        ];
    }

    // Webstreams: Webstream.php:97, whose manager list leaves out the
    // superadmin. Reproduced faithfully; compare.py expects the api to differ.
    if ($kind === 'webstream') {
        $row = CcWebstreamQuery::create()->findPK($id);

        return [
            isManagerWithoutSuperadmin($user) || $row->getDbCreatorId() == $userId,
            'owner-or-manager-without-superadmin',
        ];
    }

    // Podcasts: the rest module keeps them private to their owner unless the
    // user is a manager.
    if ($kind === 'podcast') {
        $row = PodcastQuery::create()->findPK($id);

        return [
            isManager($user) || $row->getDbOwner() == $userId,
            'owner-or-manager',
        ];
    }

    if ($kind === 'podcastepisode') {
        $row = PodcastEpisodesQuery::create()->findPK($id);
        $podcast = PodcastQuery::create()->findPK($row->getDbPodcastId());

        return [
            isManager($user) || $podcast->getDbOwner() == $userId,
            'owner-via-parent',
        ];
    }

    fwrite(STDERR, "unknown kind {$kind}\n");
    exit(1);
}

$cases = file(__DIR__ . '/cases.txt', FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
$printed = 0;

foreach ($cases as $case) {
    if (str_starts_with(trim($case), '#')) {
        continue;
    }

    [$userId, $object, $action] = explode(' ', $case);
    [$kind, $id] = explode(':', $object);

    [$allowed, $rule] = decide((int) $userId, $kind, (int) $id, $action);

    printf("%s %s %s %s|%-4s|%s\n", $userId, $kind, $id, $action, $allowed ? 'allow' : 'deny', $rule);
    ++$printed;
}

fprintf(STDERR, "%d cases\n", $printed);
