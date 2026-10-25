"""
URL Configuration

For more information on this file, see
https://docs.djangoproject.com/en/3.2/topics/http/urls/
"""

from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from .core.router import urls as core_urls
from .history.router import urls as history_urls
from .podcasts.router import urls as podcasts_urls
from .schedule.router import urls as schedule_urls
from .storage.router import urls as storage_urls

api_urls = []
api_urls += core_urls
api_urls += history_urls
api_urls += podcasts_urls
api_urls += schedule_urls
api_urls += storage_urls


urlpatterns = [
    path("api/browser/", include("rest_framework.urls", namespace="rest_framework")),
    path("api/v2/", include(api_urls)),
    path(
        "api/v2/schema",
        SpectacularAPIView.as_view(),
        name="schema",
    ),
    path(
        "api/v2/schema/swagger-ui",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    # ReDoc for reading: one page per endpoint group, no collapsed panels to open when
    # someone wants to know what a filter parameter accepts. Swagger UI stays the place to
    # actually try a call, because it sends the browser's session cookie.
    path(
        "api/v2/schema/redoc",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
]
