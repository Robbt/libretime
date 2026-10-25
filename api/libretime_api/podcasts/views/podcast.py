from rest_framework import viewsets

from ...filters import QUERY_BACKENDS
from ...ownership import StampsOwner
from ..models import ImportedPodcast, Podcast, PodcastEpisode, StationPodcast
from ..serializers import (
    ImportedPodcastSerializer,
    PodcastEpisodeSerializer,
    PodcastSerializer,
    StationPodcastSerializer,
)


class PodcastViewSet(StampsOwner, viewsets.ModelViewSet):
    queryset = Podcast.objects.all()
    serializer_class = PodcastSerializer
    model_permission_name = "podcast"
    filter_backends = QUERY_BACKENDS
    filterset_fields = ("id", "owner", "itunes_explicit")
    search_fields = ("title", "description", "itunes_author")
    ordering_fields = ("id", "title", "url")
    ordering = ("title", "id")


class PodcastEpisodeViewSet(viewsets.ModelViewSet):
    owner_parent_field = "podcast"
    queryset = PodcastEpisode.objects.all()
    serializer_class = PodcastEpisodeSerializer
    model_permission_name = "podcastepisode"
    filter_backends = QUERY_BACKENDS
    filterset_fields = ("id", "podcast", "file")
    ordering_fields = ("id", "published_at", "created_at")
    # Newest episode first, which is where a podcast list always starts.
    ordering = ("-published_at", "id")


class StationPodcastViewSet(viewsets.ModelViewSet):
    queryset = StationPodcast.objects.all()
    serializer_class = StationPodcastSerializer
    model_permission_name = "station"


class ImportedPodcastViewSet(viewsets.ModelViewSet):
    queryset = ImportedPodcast.objects.all()
    serializer_class = ImportedPodcastSerializer
    model_permission_name = "importedpodcast"
