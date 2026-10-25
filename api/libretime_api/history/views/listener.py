from django_filters import rest_framework as filters
from rest_framework import viewsets

from ...filters import QUERY_BACKENDS
from ..models import ListenerCount, MountName, Timestamp
from ..serializers import (
    ListenerCountSerializer,
    MountNameSerializer,
    TimestampSerializer,
)


class MountNameViewSet(viewsets.ModelViewSet):
    queryset = MountName.objects.all()
    serializer_class = MountNameSerializer
    model_permission_name = "mountname"
    filter_backends = QUERY_BACKENDS
    ordering_fields = ("id", "mount_name")
    ordering = ("mount_name",)


class TimestampViewSet(viewsets.ModelViewSet):
    queryset = Timestamp.objects.all()
    serializer_class = TimestampSerializer
    model_permission_name = "timestamp"
    filter_backends = QUERY_BACKENDS
    ordering_fields = ("id", "timestamp")
    ordering = ("-timestamp__timestamp",)


class ListenerCountFilter(filters.FilterSet):
    """Listener counts for one mount, in one window."""

    timestamp_after = filters.IsoDateTimeFilter(
        field_name="timestamp__timestamp", lookup_expr="gte"
    )
    timestamp_before = filters.IsoDateTimeFilter(
        field_name="timestamp__timestamp", lookup_expr="lte"
    )

    class Meta:
        model = ListenerCount
        fields = ("id", "mount_name")


class ListenerCountViewSet(viewsets.ModelViewSet):
    queryset = ListenerCount.objects.all()
    serializer_class = ListenerCountSerializer
    model_permission_name = "listenercount"
    filter_backends = QUERY_BACKENDS
    filterset_class = ListenerCountFilter
    ordering_fields = ("id", "timestamp__timestamp", "listener_count", "mount_name")
    # Newest first: a listener graph is drawn from the recent end, and this table
    # only ever grows. The count points at a Timestamp row rather than carrying a
    # datetime, so the sort follows that.
    ordering = ("-timestamp__timestamp",)
