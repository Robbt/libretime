import logging
import os
from os import remove

from django.conf import settings
from django.http import HttpResponse
from django.utils.encoding import filepath_to_uri
from django_filters import rest_framework as filters
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException

from ...filters import QUERY_BACKENDS
from ...ownership import StampsOwner
from ...schedule.models import Schedule
from ..models import File
from ..serializers import FileSerializer

logger = logging.getLogger(__name__)


class FileInUse(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The file is currently used"
    default_code = "file_in_use"


class FileFilter(filters.FilterSet):
    """
    What the library screen can be asked for.

    The exact filters are the columns the legacy library has a control for; the ranges
    are the questions that were only ever asked in SQL - files between two durations,
    added since a date, not played in a month. Lengths are Postgres intervals, so they
    are compared as the api renders them: "00:04:32".
    """

    length_min = filters.CharFilter(field_name="length", lookup_expr="gte")
    length_max = filters.CharFilter(field_name="length", lookup_expr="lte")
    bpm_min = filters.NumberFilter(field_name="bpm", lookup_expr="gte")
    bpm_max = filters.NumberFilter(field_name="bpm", lookup_expr="lte")
    created_after = filters.IsoDateTimeFilter(
        field_name="created_at", lookup_expr="gte"
    )
    created_before = filters.IsoDateTimeFilter(
        field_name="created_at", lookup_expr="lte"
    )
    updated_after = filters.IsoDateTimeFilter(
        field_name="updated_at", lookup_expr="gte"
    )
    updated_before = filters.IsoDateTimeFilter(
        field_name="updated_at", lookup_expr="lte"
    )
    last_played_after = filters.IsoDateTimeFilter(
        field_name="last_played_at", lookup_expr="gte"
    )
    last_played_before = filters.IsoDateTimeFilter(
        field_name="last_played_at", lookup_expr="lte"
    )

    class Meta:
        model = File
        fields = (
            "id",
            "library",
            "owner",
            "import_status",
            "mime",
            "md5",
            "genre",
            "artist_name",
            "album_title",
            "composer",
            "track_title",
            "hidden",
            "scheduled",
            "part_of_list",
            "exists",
        )


class FileViewSet(StampsOwner, viewsets.ModelViewSet):
    queryset = File.objects.all()
    serializer_class = FileSerializer
    model_permission_name = "file"
    filter_backends = QUERY_BACKENDS
    filterset_class = FileFilter
    search_fields = (
        "track_title",
        "artist_name",
        "album_title",
        "composer",
        "isrc",
        "md5",
    )
    ordering_fields = (
        "id",
        "track_title",
        "artist_name",
        "album_title",
        "length",
        "bpm",
        "size",
        "created_at",
        "updated_at",
        "last_played_at",
    )
    # Newest first, which is what a library list has always been asked for. Nothing
    # in the running system reads this list - playout fetches files by id - so a
    # stable order is a gain here rather than a change of contract.
    ordering = ("-created_at", "id")

    # pylint: disable=invalid-name,unused-argument
    @action(detail=True, methods=["GET"])
    def download(self, request, pk=None):
        instance: File = self.get_object()

        response = HttpResponse()
        # HTTP headers must be USASCII encoded, or Nginx might not find the file and
        # will return a 404.
        redirect_uri = filepath_to_uri(os.path.join("/api/_media", instance.filepath))
        response["X-Accel-Redirect"] = redirect_uri
        return response

    def perform_destroy(self, instance: File):
        if Schedule.is_file_scheduled_in_the_future(file_id=instance.id):
            raise FileInUse("file is scheduled in the future")

        try:
            if instance.filepath is None:
                logger.warning("file does not have a filepath: %d", instance.id)
                return

            path = os.path.join(settings.CONFIG.storage.path, instance.filepath)

            if not os.path.isfile(path):
                logger.warning("file does not exist in storage: %d", instance.id)
                return

            remove(path)
        except OSError as exception:
            raise APIException("could not delete file from storage") from exception
