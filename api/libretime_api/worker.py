import os
from datetime import timedelta

from celery import Celery, signals
from celery.schedules import schedule
from celery.utils.log import get_task_logger
from django.conf import settings
from libretime_api_client.v1 import ApiClient as LegacyClient

from . import PACKAGE, VERSION

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "libretime_api.settings.prod")
os.environ.setdefault("LIBRETIME_CONFIG_FILEPATH", "/etc/libretime/config.yml")

app = Celery("worker")

app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks(
    [
        "libretime_api.core.tasks",
        "libretime_api.podcasts.tasks",
        "libretime_api.schedule.tasks",
    ]
)

app.conf.beat_schedule = {
    "core-clean-expired-sessions": {
        "task": "libretime_api.core.tasks.clean_expired_sessions",
        "schedule": schedule(run_every=timedelta(hours=6)),
    },
    "podcasts-clean-failed-imports": {
        "task": "libretime_api.podcasts.tasks.clean_failed_imports",
        "schedule": schedule(run_every=timedelta(hours=1)),
    },
    "schedule-clean-overbooked-schedule": {
        "task": "libretime_api.schedule.tasks.clean_overbooked_schedule",
        "schedule": schedule(run_every=timedelta(hours=6)),
    },
    "legacy-trigger-task-manager": {
        "task": "libretime_api.worker.legacy_trigger_task_manager",
        "schedule": schedule(run_every=timedelta(minutes=5)),
    },
}

logger = get_task_logger(__name__)


def populate_shows_scheduled() -> bool:
    """
    Whether this worker owns the future show calendar.

    Exactly one service generates the instances of repeating shows, see
    general.show_instance_populator: the legacy service tops the horizon up
    unless the API is told to do it, and takes it back when the horizon starts
    lapsing - which is how a worker that is not running shows up.
    """
    return settings.CONFIG.general.show_instance_populator == "api"


# Exactly one service keeps the future show calendar populated. The legacy
# service does it unless the configuration hands the job to this worker, and it
# takes the job back when this worker stops extending the horizon.
if populate_shows_scheduled():
    app.conf.beat_schedule["schedule-populate-shows"] = {
        "task": "libretime_api.schedule.tasks.populate_shows",
        "schedule": schedule(run_every=timedelta(minutes=30)),
    }


def build_autoplaylists_scheduled() -> bool:
    """
    Whether this worker fills the autoplaylists of upcoming shows.

    The legacy task manager keeps a poll either way; with api it slows to an
    hourly safety net, see general.auto_playlist_builder.
    """
    return settings.CONFIG.general.auto_playlist_builder == "api"


if build_autoplaylists_scheduled():
    app.conf.beat_schedule["schedule-build-autoplaylists"] = {
        "task": "libretime_api.schedule.tasks.build_upcoming_autoplaylists",
        "schedule": schedule(run_every=timedelta(minutes=1)),
    }


@signals.worker_init.connect
def init_sentry(**_kwargs):
    if "SENTRY_DSN" in os.environ:
        logger.info("installing sentry")
        # pylint: disable=import-outside-toplevel
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration

        sentry_sdk.init(
            traces_sample_rate=1.0,
            release=f"{PACKAGE}@{VERSION}",
            integrations=[
                CeleryIntegration(),
            ],
        )


@app.task(ignore_result=True)
def legacy_trigger_task_manager():
    """
    Trigger the legacy task manager to perform background tasks.
    """
    legacy_client = LegacyClient(
        base_url=settings.CONFIG.general.public_url,
        api_key=settings.CONFIG.general.api_key,
    )
    legacy_client.trigger_task_manager()
