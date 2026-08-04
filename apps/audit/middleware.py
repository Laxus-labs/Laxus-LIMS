import uuid

from django.utils.deprecation import MiddlewareMixin
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenError

from . import context


class AuditContextMiddleware(MiddlewareMixin):
    """
    Binds the authenticated user and a request id to the database session so
    audit triggers can record who acted.

    Ordering matters: this must sit after AuthenticationMiddleware, or
    `request.user` is not resolved yet and every audit row is written with a
    null actor. That alone is not enough for the API, though: DRF's
    JWTAuthentication only resolves `request.user` inside APIView.dispatch(),
    which runs after every middleware's process_request. Without resolving
    the JWT here too, every API-originated audit row would silently get a
    null actor. Resolving it in the middleware (rather than in a shared DRF
    base view) means the guarantee holds for any endpoint automatically,
    matching the "audit trail covers every writer" principle elsewhere in
    this project.

    A request id is generated when the caller does not supply one, so every
    row written while serving a single API call shares a correlation key.
    That turns "what happened during that failed batch import?" into one
    indexed query.
    """

    HEADER = "HTTP_X_REQUEST_ID"

    def process_request(self, request):
        request_id = request.META.get(self.HEADER) or uuid.uuid4().hex
        request.request_id = request_id

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            user = self._resolve_jwt_user(request)

        if user is not None and user.is_authenticated:
            context.set_actor(user, request_id)
        else:
            context.set_actor(None, request_id)

    def _resolve_jwt_user(self, request):
        try:
            result = JWTAuthentication().authenticate(request)
        except (AuthenticationFailed, TokenError):
            return None
        return result[0] if result else None

    def process_response(self, request, response):
        request_id = getattr(request, "request_id", None)
        if request_id:
            response["X-Request-ID"] = request_id
        return response
