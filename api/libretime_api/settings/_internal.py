from os import environ, getenv

from .. import PACKAGE, VERSION

API_VERSION = "2.0.0"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = getenv("LIBRETIME_DEBUG", "false").lower() == "true"

# Application definition

INSTALLED_APPS = [
    "libretime_api.legacy",
    "libretime_api.core",
    "libretime_api.history",
    "libretime_api.storage",
    "libretime_api.podcasts",
    "libretime_api.schedule",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_celery_results",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "libretime_api.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "libretime_api.wsgi.application"

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/3.2/howto/static-files/

STATIC_URL = "/api/v2/static/"

# Default primary key field type
# https://docs.djangoproject.com/en/3.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Password validation
# https://docs.djangoproject.com/en/3.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Logging
# https://docs.djangoproject.com/en/3.2/topics/logging/#configuring-logging


def setup_logger(log_filepath: str | None):
    logging_handlers = {
        "console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    }

    if log_filepath is not None:
        logging_handlers["file"] = {
            "level": "DEBUG",
            "class": "logging.FileHandler",
            "filename": log_filepath,
            "formatter": "verbose",
        }

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "simple": {
                "format": "{levelname} {message}",
                "style": "{",
            },
            "verbose": {
                "format": "{asctime} {module} {levelname} {message}",
                "style": "{",
            },
        },
        "handlers": logging_handlers,
        "loggers": {
            "django": {
                "handlers": logging_handlers.keys(),
                "level": "INFO",
                "propagate": True,
            },
            "libretime_api": {
                "handlers": logging_handlers.keys(),
                "level": "INFO",
                "propagate": True,
            },
        },
    }


# Rest Framework
# https://www.django-rest-framework.org/api-guide/settings/

renderer_classes = ["rest_framework.renderers.JSONRenderer"]
if DEBUG:
    renderer_classes += ["rest_framework.renderers.BrowsableAPIRenderer"]

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": renderer_classes,
    "DEFAULT_AUTHENTICATION_CLASSES": (
        # The session cookie of the legacy service first: a browser that logged
        # in to the station is authenticated here as the same user. Anything that
        # changes something must also carry that session's csrf token. The class
        # returns None for everything it does not recognise, so the two below
        # keep working exactly as before.
        "libretime_api.core.auth.PhpSessionAuthentication",
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": [
        "libretime_api.permissions.IsSystemTokenOrUser",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "URL_FIELD_NAME": "item_url",
}

# Auth
# https://docs.djangoproject.com/en/3.2/topics/auth/customizing/#substituting-a-custom-user-model

AUTH_USER_MODEL = "core.User"

# Spectacular
# https://drf-spectacular.readthedocs.io/en/latest/settings.html

SPECTACULAR_ENUM_NAME_OVERRIDES = {
    "FileImportStatusEnum": "libretime_api.storage.models.File.ImportStatus",
    "PlaylistContentKindEnum": "libretime_api.schedule.models.PlaylistContent.Kind",
    "SmartBlockKindEnum": "libretime_api.schedule.models.SmartBlock.Kind",
}

# The two documentation pages are static javascript, loaded from a CDN. drf-spectacular's
# defaults point at `swagger-ui-dist@latest` and `redoc@latest`, which means a station's API
# page can change appearance or break with no commit on our side and no release — the same
# class of problem as an unpinned formatter, one layer over. Pinned like everything else
# here; tools/api-docs/index.html repeats the swagger-ui version and a test keeps them
# together.
SWAGGER_UI_VERSION = "5.34.0"
REDOC_VERSION = "3.0.1"

SPECTACULAR_SETTINGS = {
    "TITLE": "LibreTime API",
    "DESCRIPTION": (
        "Radio Broadcast & Automation Platform\n\n"
        "### Authentication\n\n"
        "Three credentials are accepted, and the generated documentation lists them on "
        "every operation:\n\n"
        "- **Session cookie** (`PHPSESSID`) — a browser that signed in to the station's "
        "interface is the same user here, with the same roles and the same ownership of "
        "its shows and files. Cookie requests must also carry the csrf token, like the "
        "interface does: `GET /api/v2/session/csrf-token` returns the one for the current "
        "session, and it goes in the `X-CSRFToken` header (or a `csrf_token` form field). "
        "The legacy application validates the same value from the same session, so one "
        "sign-in works across both.\n"
        "- **Station api key** — `Authorization: Api-Key <general.api_key>`, the "
        "credential the other LibreTime services use. Not subject to the csrf "
        "requirement, since it is not a cookie and cannot be sent by a browser as a "
        "side effect of someone visiting a page.\n"
        "- **HTTP basic** — a station user's own username and password.\n\n"
        "Permissions are per object as well as per role: a host may change the shows they "
        "host and the files they uploaded, and being allowed to create something does not "
        "imply being allowed to change someone else's.\n"
    ),
    "VERSION": API_VERSION,
    "ENUM_NAME_OVERRIDES": SPECTACULAR_ENUM_NAME_OVERRIDES,
    # The default enum hook first, then the parts of LibreTime's authentication the
    # generator cannot see (see libretime_api/openapi.py for why they are not visible).
    "POSTPROCESSING_HOOKS": [
        "drf_spectacular.hooks.postprocess_schema_enums",
        "libretime_api.openapi.libretime_authentication_extensions",
    ],
    # Same assets, pinned versions, still from jsdelivr. Serving them offline would mean
    # vendoring ~1.5 MB of javascript, which also trips the check-added-large-files hook's
    # 500 KB default; an air-gapped station gets a page that explains nothing instead. If
    # that ever matters, the fix is a vendored copy plus an exclusion, not a move back to
    # @latest.
    "SWAGGER_UI_DIST": f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}",
    "REDOC_DIST": f"https://cdn.jsdelivr.net/npm/redoc@{REDOC_VERSION}",
    # Pulled from the same CDN by default; not worth a failed request per page load.
    "SWAGGER_UI_FAVICON_HREF": "",
    # The document is public on purpose, and not because permissions are lax: it is
    # generated from serializers, viewsets and filter classes, so it contains no station
    # data — no users, no shows, no library — and LibreTime's api is the same open-source
    # surface anyone can read in the repository. What a request is *allowed to do* is still
    # decided per request by IsSystemTokenOrUser and the ownership rules beside it. Naming
    # the default here turns an inherited behaviour into a decision with a reason.
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
}

# Sentry
# https://docs.sentry.io/platforms/python/guides/django/
if "SENTRY_DSN" in environ:
    # pylint: disable=import-outside-toplevel
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        traces_sample_rate=1.0,
        release=f"{PACKAGE}@{VERSION}",
        integrations=[
            DjangoIntegration(),
        ],
    )

# Celery
# https://docs.celeryq.dev/en/stable/userguide/configuration.html#configuration

# CELERY_BROKER_URL
CELERY_WORKER_CONCURRENCY = 2
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_EVENT_QUEUE_EXPIRES = 900
CELERY_RESULT_BACKEND = "django-db"
CELERY_RESULT_PERSISTENT = True
CELERY_RESULT_EXPIRES = 24 * 60 * 60  # Clean task result from backend after 24 hours
CELERY_RESULT_EXTENDED = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 1800
