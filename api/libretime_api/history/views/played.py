from django_filters import rest_framework as filters
from rest_framework import viewsets

from ...filters import QUERY_BACKENDS
from ..models import (
    PlayoutHistory,
    PlayoutHistoryMetadata,
    PlayoutHistoryTemplate,
    PlayoutHistoryTemplateField,
)
from ..serializers import (
    PlayoutHistoryMetadataSerializer,
    PlayoutHistorySerializer,
    PlayoutHistoryTemplateFieldSerializer,
    PlayoutHistoryTemplateSerializer,
)


class PlayoutHistoryFilter(filters.FilterSet):
    """What aired, in a window, in a night, or of one file."""

    starts_after = filters.IsoDateTimeFilter(field_name="starts", lookup_expr="gte")
    starts_before = filters.IsoDateTimeFilter(field_name="starts", lookup_expr="lte")

    class Meta:
        model = PlayoutHistory
        fields = ("id", "file", "instance")


class PlayoutHistoryViewSet(viewsets.ModelViewSet):
    queryset = PlayoutHistory.objects.all()
    serializer_class = PlayoutHistorySerializer
    model_permission_name = "playouthistory"
    filter_backends = QUERY_BACKENDS
    filterset_class = PlayoutHistoryFilter
    ordering_fields = ("id", "starts", "ends")
    ordering = ("-starts", "id")


class PlayoutHistoryMetadataViewSet(viewsets.ModelViewSet):
    queryset = PlayoutHistoryMetadata.objects.all()
    serializer_class = PlayoutHistoryMetadataSerializer
    model_permission_name = "playouthistorymetadata"
    filter_backends = QUERY_BACKENDS
    filterset_fields = ("id", "history", "key", "value")
    ordering_fields = ("id", "key", "value")


class PlayoutHistoryTemplateViewSet(viewsets.ModelViewSet):
    queryset = PlayoutHistoryTemplate.objects.all()
    serializer_class = PlayoutHistoryTemplateSerializer
    model_permission_name = "playouthistorytemplate"


class PlayoutHistoryTemplateFieldViewSet(viewsets.ModelViewSet):
    queryset = PlayoutHistoryTemplateField.objects.all()
    serializer_class = PlayoutHistoryTemplateFieldSerializer
    model_permission_name = "playouthistorytemplatefield"
