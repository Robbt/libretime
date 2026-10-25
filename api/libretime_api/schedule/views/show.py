from rest_framework import status, viewsets
from rest_framework.response import Response
from rest_framework.serializers import ValidationError

from ..models import Show, ShowDays, ShowHost, ShowInstance, ShowRebroadcast
from ..serializers import (
    LEGACY_DERIVED_FIELD_NAMES,
    LEGACY_FIELD_NAMES,
    ShowCreateSerializer,
    ShowDaysSerializer,
    ShowHostSerializer,
    ShowInstanceSerializer,
    ShowRebroadcastSerializer,
    ShowSerializer,
)
from ..services.show import ShowForm, ShowFormError, save_show


class ShowViewSet(viewsets.ModelViewSet):
    """
    A show, and the calendar it makes.

    Reading and editing the row is the ordinary behaviour of a viewset. Creating
    one is not: a show is only on the air once it has repeat days and instances,
    so ``POST`` goes through the same service the show dialog drives, writes the
    show, its days, its rebroadcasts, its hosts and its instances in one
    transaction, and tells the playout service when the transaction commits.

    The request body is documented in :class:`ShowCreateSerializer`. The field
    names the dialog posts are accepted too, and mean the same thing.
    """

    queryset = Show.objects.all()
    serializer_class = ShowSerializer
    model_permission_name = "show"

    def get_serializer_class(self):
        if self.action == "create":
            return ShowCreateSerializer

        return super().get_serializer_class()

    def create(self, request, *args, **kwargs):
        serializer = ShowCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            show, _ = save_show(
                ShowForm.from_legacy_payload(serializer.as_legacy_payload())
            )
        except ShowFormError as error:
            raise ValidationError(_form_error(str(error))) from error

        show.refresh_from_db()

        return Response(
            {
                **ShowSerializer(instance=show).data,
                "week_days": list(
                    ShowDays.objects.filter(show=show).values_list(
                        "week_day", flat=True
                    )
                ),
                "instances": ShowInstanceSerializer(
                    ShowInstance.objects.filter(show=show).order_by("starts_at"),
                    many=True,
                ).data,
            },
            status=status.HTTP_201_CREATED,
        )


def _form_error(message):
    """
    A refusal from the service, in the vocabulary the client used.

    The service names the field it choked on by the name the dialog posts. A client
    that wrote ``timezone`` should be told about ``timezone``; a client that wrote
    the dialog's own name gets it back unchanged.
    """
    field, _, detail = message.partition(": ")
    names = {legacy: name for name, legacy in LEGACY_FIELD_NAMES.items()}
    names.update(LEGACY_DERIVED_FIELD_NAMES)

    return {names.get(field, field): [detail or message]}


class ShowDaysViewSet(viewsets.ModelViewSet):
    queryset = ShowDays.objects.all()
    serializer_class = ShowDaysSerializer
    model_permission_name = "showdays"


class ShowHostViewSet(viewsets.ModelViewSet):
    queryset = ShowHost.objects.all()
    serializer_class = ShowHostSerializer
    model_permission_name = "showhost"


class ShowInstanceViewSet(viewsets.ModelViewSet):
    queryset = ShowInstance.objects.all()
    serializer_class = ShowInstanceSerializer
    model_permission_name = "showinstance"


class ShowRebroadcastViewSet(viewsets.ModelViewSet):
    queryset = ShowRebroadcast.objects.all()
    model_permission_name = "showrebroadcast"
    serializer_class = ShowRebroadcastSerializer
