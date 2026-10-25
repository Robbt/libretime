from rest_framework import viewsets

from ...filters import QUERY_BACKENDS
from ..models import LiveLog
from ..serializers import LiveLogSerializer


class LiveLogViewSet(viewsets.ModelViewSet):
    queryset = LiveLog.objects.all()
    serializer_class = LiveLogSerializer
    model_permission_name = "livelog"
    filter_backends = QUERY_BACKENDS
    filterset_fields = ("id", "state")
    ordering_fields = ("id", "start_time", "end_time")
    ordering = ("-start_time",)
