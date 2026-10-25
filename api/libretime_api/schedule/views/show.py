from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.serializers import ValidationError

from ..models import Show, ShowDays, ShowHost, ShowInstance, ShowRebroadcast
from ..serializers import (
    LEGACY_DERIVED_FIELD_NAMES,
    LEGACY_FIELD_NAMES,
    ExtendShowSerializer,
    ShowCreateSerializer,
    ShowDaysSerializer,
    ShowEditSerializer,
    ShowHostSerializer,
    ShowInstanceEditSerializer,
    ShowInstanceSerializer,
    ShowRebroadcastSerializer,
    ShowSerializer,
)
from ..services.show import (
    ShowForm,
    ShowFormError,
    ShowInstanceInThePast,
    ShowInstanceNotFound,
    delete_show_instance,
    edit_repeating_instance,
    extend_show_instances,
    save_show,
)


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

    #: Whether the payload describes a new show or the one in the url.
    def get_serializer_class(self):
        if self.action == "create":
            return ShowCreateSerializer

        if self.action in {"update", "partial_update"}:
            return ShowEditSerializer

        if self.action == "extend":
            return ExtendShowSerializer

        return super().get_serializer_class()

    def update(self, request, *args, **kwargs):
        """
        Re-describe a show, and let the calendar work out what that changes.

        The same service the create path uses, with the show's id from the url. The
        nights already on the calendar are adopted rather than replaced, the ones the
        new rules no longer produce are removed, and anything queued inside a
        survivor moves with it.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            show, _ = save_show(
                ShowForm.from_legacy_payload(serializer.as_legacy_payload()),
                is_update=True,
            )
        except ShowFormError as error:
            raise ValidationError(_form_error(str(error))) from error

        show.refresh_from_db()

        return Response(ShowSerializer(instance=show).data)

    @action(detail=True, methods=["post"])
    def extend(self, request, pk=None):
        """
        Build a show's nights further into the calendar, on request.

        The calendar's "load more". It does not move the station's populated date -
        looking at March and having the calendar is not the same as telling the
        populator the calendar reaches March, which would make it stop there.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        created = extend_show_instances(
            int(pk), serializer.validated_data["until"], fill_instances=True
        )

        return Response(
            {
                "show": int(pk),
                "instances_created": len(created.get(int(pk), [])),
            }
        )

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
    """
    A single night of a show.

    Two of the ordinary methods mean something particular here. ``DELETE`` removes
    that night, or it and every night after it, depending on ``?scope=``: the
    calendar's two menu entries are not the same request, and a client that wants the
    show off the air from next week onwards should not have to count. ``PATCH``
    takes one night out of the show's repeating pattern and gives it its own time -
    the show goes on airing, this cell stops being part of it.
    """

    queryset = ShowInstance.objects.all()
    serializer_class = ShowInstanceSerializer
    model_permission_name = "showinstance"

    def destroy(self, request, *args, **kwargs):
        scope = request.query_params.get("scope", "single")

        if scope not in ("single", "following"):
            raise ValidationError(
                {"scope": "Either 'single' - this night only - or 'following'."}
            )

        try:
            show_id = delete_show_instance(
                int(kwargs["pk"]), single_instance=scope == "single"
            )
        except ShowInstanceNotFound as error:
            raise NotFound(str(error)) from error
        except ShowInstanceInThePast as error:
            raise ValidationError(
                {"id": "That night has already aired, and its history is real."}
            ) from error

        return Response({"show": show_id, "scope": scope}, status=status.HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = ShowInstanceEditSerializer(
            data=request.data, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)

        edited = edit_repeating_instance(
            serializer.as_legacy_payload(instance.show, instance)
        )

        return Response(ShowInstanceSerializer(instance=edited).data)

    update = partial_update


class ShowRebroadcastViewSet(viewsets.ModelViewSet):
    queryset = ShowRebroadcast.objects.all()
    model_permission_name = "showrebroadcast"
    serializer_class = ShowRebroadcastSerializer
