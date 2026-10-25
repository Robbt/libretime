from rest_framework import viewsets

from ...filters import QUERY_BACKENDS
from ...ownership import StampsOwner
from ..models import SmartBlock, SmartBlockContent, SmartBlockCriteria
from ..serializers import (
    SmartBlockContentSerializer,
    SmartBlockCriteriaSerializer,
    SmartBlockSerializer,
)


class SmartBlockViewSet(StampsOwner, viewsets.ModelViewSet):
    queryset = SmartBlock.objects.all()
    serializer_class = SmartBlockSerializer
    model_permission_name = "smartblock"
    filter_backends = QUERY_BACKENDS
    filterset_fields = ("id", "owner", "kind")
    search_fields = ("name", "description")
    ordering_fields = ("id", "name", "length", "created_at", "kind")
    ordering = ("name", "id")


class SmartBlockContentViewSet(viewsets.ModelViewSet):
    owner_parent_field = "block"
    queryset = SmartBlockContent.objects.all()
    serializer_class = SmartBlockContentSerializer
    model_permission_name = "smartblockcontent"
    filter_backends = QUERY_BACKENDS
    filterset_fields = ("id", "block", "file")
    ordering_fields = ("id", "position", "offset", "length")
    # No default order: position is the block's own data, written by the legacy
    # editor too. Ask for ordering=position.


class SmartBlockCriteriaViewSet(viewsets.ModelViewSet):
    owner_parent_field = "block"
    queryset = SmartBlockCriteria.objects.all()
    serializer_class = SmartBlockCriteriaSerializer
    model_permission_name = "smartblockcriteria"
