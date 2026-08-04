"""
Maps domain exceptions to HTTP status codes.

Service functions in apps/*/services.py raise plain domain exceptions
(apps/samples/errors.py, and eventually apps/inventory/errors.py) rather
than DRF's exceptions, so views can call them directly without an
exception mapping layer of their own. This handler is that mapping,
kept in one place so a new domain error type is a dict entry here, not a
try/except repeated in every view.
"""

from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.response import Response
from rest_framework import status

from apps.samples.errors import (
    IllegalTransition,
    InsufficientVolume,
    InvalidPosition,
    PermissionDenied as SamplePermissionDenied,
    PositionOccupied,
    ReasonRequired,
)

STATUS_BY_EXCEPTION = {
    IllegalTransition: status.HTTP_409_CONFLICT,
    PositionOccupied: status.HTTP_409_CONFLICT,
    InvalidPosition: status.HTTP_400_BAD_REQUEST,
    InsufficientVolume: status.HTTP_400_BAD_REQUEST,
    ReasonRequired: status.HTTP_400_BAD_REQUEST,
    SamplePermissionDenied: status.HTTP_403_FORBIDDEN,
}


def lims_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response

    for exc_type, http_status in STATUS_BY_EXCEPTION.items():
        if isinstance(exc, exc_type):
            return Response({"detail": str(exc)}, status=http_status)

    # contents_of() raises a plain ValueError for a location with no
    # computed path, rather than a domain exception of its own.
    if isinstance(exc, ValueError):
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return None
