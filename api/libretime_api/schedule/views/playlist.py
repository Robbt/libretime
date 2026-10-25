from rest_framework import viewsets

from ...filters import QUERY_BACKENDS
from ...ownership import StampsOwner
from ..models import Playlist, PlaylistContent
from ..serializers import PlaylistContentSerializer, PlaylistSerializer


class PlaylistViewSet(StampsOwner, viewsets.ModelViewSet):
    queryset = Playlist.objects.all()
    serializer_class = PlaylistSerializer
    model_permission_name = "playlist"
    filter_backends = QUERY_BACKENDS
    filterset_fields = ("id", "owner")
    search_fields = ("name", "description")
    ordering_fields = ("id", "name", "length", "created_at", "updated_at")
    ordering = ("name", "id")


class PlaylistContentViewSet(viewsets.ModelViewSet):
    # A row of a playlist belongs to whoever owns the playlist.
    owner_parent_field = "playlist"
    queryset = PlaylistContent.objects.all()
    serializer_class = PlaylistContentSerializer
    model_permission_name = "playlistcontent"
    filter_backends = QUERY_BACKENDS
    filterset_fields = ("id", "playlist", "block", "file", "stream", "kind")
    ordering_fields = ("id", "position", "offset", "length")
    # No default order, even though position is the order a playlist is played in:
    # those values are written by the legacy editor as well as here, and deciding
    # what "in order" means is one writer's job. Ask for ordering=position.
