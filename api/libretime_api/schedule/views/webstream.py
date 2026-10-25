from rest_framework import viewsets

from ...ownership import StampsOwner
from ..models import Webstream, WebstreamMetadata
from ..serializers import WebstreamMetadataSerializer, WebstreamSerializer


class WebstreamViewSet(StampsOwner, viewsets.ModelViewSet):
    queryset = Webstream.objects.all()
    serializer_class = WebstreamSerializer
    model_permission_name = "webstream"


class WebstreamMetadataViewSet(viewsets.ModelViewSet):
    owner_parent_field = "schedule"
    queryset = WebstreamMetadata.objects.all()
    serializer_class = WebstreamMetadataSerializer
    model_permission_name = "webstreametadata"
