from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..auth import csrf_token
from ..models import LoginAttempt, Session, UserToken
from ..serializers import LoginAttemptSerializer, UserTokenSerializer


class UserTokenViewSet(viewsets.ModelViewSet):
    queryset = UserToken.objects.all()
    serializer_class = UserTokenSerializer
    model_permission_name = "usertoken"


class LoginAttemptViewSet(viewsets.ModelViewSet):
    queryset = LoginAttempt.objects.all()
    serializer_class = LoginAttemptSerializer
    model_permission_name = "loginattempt"


class CsrfTokenView(APIView):
    """
    The token the caller's own session expects back on a request that writes.

    A page replacing a legacy screen needs this to change anything: the cookie
    proves which user it is, and this proves the page asked for it. It is handed
    out over an ordinary authenticated read, which is safe because the same-origin
    policy is what stops any other page reading the answer - and it is only ever
    the caller's own token, never somebody else's.

    Callers authenticated some other way get nothing. An API key does not need a
    CSRF token, and basic authentication has no session to read one from, so
    there is no case where returning a token here would be correct.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not isinstance(request.auth, Session):
            raise PermissionDenied("Only a legacy session has a CSRF token.")

        token = csrf_token(request.auth.data)

        if token is None:
            raise PermissionDenied("This session carries no CSRF token.")

        return Response({"csrf_token": token})
