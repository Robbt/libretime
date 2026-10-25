"""
Who a created row belongs to.

The legacy application stamps the creator on everything a user makes: a playlist
gets ``creator_id`` from the session user when it is created, a block likewise, an
uploaded file gets ``owner_id`` - and that stamp is what later lets the same user
edit the thing. Without it the ownership rules in
:mod:`libretime_api.permissions` are correct and useless at once: a row nobody owns
is a manager's to change, so a host who creates a playlist through the api cannot
then add a song to it.
"""

from __future__ import annotations

from typing import Any

# Both classes below are mixins: self.request and super().perform_create belong to
# the viewset they are composed into, which pylint cannot see from here.
# pylint: disable=no-member


class StampsOwner:
    """
    Set the owner of a created row to the user who created it.

    Applied to the viewsets whose model has an owner, and deliberately quiet in
    two cases:

    - the caller named an owner - a manager moving a playlist to somebody else,
      or an import assigning ownership explicitly. The submitted value wins,
      because it is the more specific instruction.
    - nobody is authenticated. A service writing with the api key is not a user,
      and inventing one would attribute a machine's work to a person. The column
      stays empty, which is what the analyzer's own imports leave it.
    """

    owner_field = "owner"

    def perform_create(self, serializer):
        user = getattr(self.request, "user", None)
        named_owner = serializer.validated_data.get(self.owner_field)

        if named_owner is not None or user is None or not user.is_authenticated:
            super().perform_create(serializer)

            return

        serializer.save(**{self.owner_field: user})


def owner_of_parent(view, data: Any):
    """
    The object a new row would hang off, or ``None``.

    A create has no object to check yet, which is where a child row is awkward: a
    playlist's contents belong to whoever owns the playlist, so the question "may
    this user add a song here?" is answered by the playlist, not by the new row.
    Viewsets that hold children say which field points at the parent
    (``owner_parent_field``), and this resolves it.
    """
    field_name = getattr(view, "owner_parent_field", None)

    if field_name is None or data is None:
        return None

    parent_id = data.get(field_name) if hasattr(data, "get") else None

    if parent_id is None:
        return None

    try:
        field = view.queryset.model._meta.get_field(field_name)
        parent_model = field.related_model
    except Exception:  # noqa: BLE001
        # Not a relation this code can follow; the caller falls back to the role's
        # own permission, which is the stricter answer rather than the looser one.
        return None

    try:
        return parent_model.objects.filter(pk=parent_id).first()
    except (ValueError, TypeError):
        return None
