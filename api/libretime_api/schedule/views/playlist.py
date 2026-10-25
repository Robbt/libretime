from rest_framework import viewsets

from ...ownership import StampsOwner
from ..models import Playlist, PlaylistContent
from ..serializers import PlaylistContentSerializer, PlaylistSerializer


class PlaylistViewSet(StampsOwner, viewsets.ModelViewSet):
    queryset = Playlist.objects.all()
    serializer_class = PlaylistSerializer
    model_permission_name = "playlist"


class PlaylistContentViewSet(viewsets.ModelViewSet):
    # A row of a playlist belongs to whoever owns the playlist.
    owner_parent_field = "playlist"
    queryset = PlaylistContent.objects.all()
    serializer_class = PlaylistContentSerializer
    model_permission_name = "playlistcontent"
