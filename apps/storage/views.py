from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.samples.serializers import SampleSerializer
from apps.samples.services import contents_of

from .models import StorageLocation
from .serializers import StorageLocationSerializer


class LocationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only: the storage hierarchy is a physical structure managed
    through admin/fixtures, not something the sample-management API
    mutates. Unpaginated because it's a hierarchy the frontend needs
    whole (a location picker/browser), not a high-volume table.
    """

    queryset = StorageLocation.objects.select_related("parent").all()
    serializer_class = StorageLocationSerializer
    pagination_class = None

    @action(detail=True, methods=["get"])
    def contents(self, request, pk=None):
        location = self.get_object()
        include_descendants = request.query_params.get("include_descendants", "true") != "false"
        samples = contents_of(location, include_descendants=include_descendants)
        return Response(SampleSerializer(samples, many=True).data)
