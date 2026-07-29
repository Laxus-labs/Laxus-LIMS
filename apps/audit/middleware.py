import uuid

from django.utils.deprecation import MiddlewareMixin

from . import context


class AuditContextMiddleware(MiddlewareMixin):
    """
    Binds the authenticated user and a request id to the database session so
    audit triggers can record who acted.

    Ordering matters: this must sit after AuthenticationMiddleware, or
    `request.user` is not resolved yet and every audit row is written with a
    null actor.

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
        if user is not None and user.is_authenticated:
            context.set_actor(user, request_id)
        else:
            context.set_actor(None, request_id)

    def process_response(self, request, response):
        request_id = getattr(request, "request_id", None)
        if request_id:
            response["X-Request-ID"] = request_id
        return response
