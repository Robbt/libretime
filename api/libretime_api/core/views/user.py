from django.contrib.auth import get_user_model
from rest_framework import viewsets

from ...filters import QUERY_BACKENDS
from ...permissions import IsAdminOrOwnUser
from ..serializers import UserSerializer


class UserViewSet(viewsets.ModelViewSet):
    queryset = get_user_model().objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAdminOrOwnUser]
    model_permission_name = "user"
    filter_backends = QUERY_BACKENDS
    filterset_fields = ("id", "role")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering_fields = ("id", "username", "email", "last_login")
    ordering = ("username",)
