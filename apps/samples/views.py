from rest_framework import filters, mixins, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

from . import services
from .models import Sample
from .serializers import (
    AccessionBatchInputSerializer,
    AliquotInputSerializer,
    SamplePlacementSerializer,
    SampleSerializer,
    TransferInputSerializer,
    TransitionInputSerializer,
)


class SampleCursorPagination(CursorPagination):
    ordering = "-created_at"
    page_size = 50


class SampleViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Read (list/retrieve) plus one action per domain operation in
    apps.samples.services. Deliberately no create/update/destroy —
    mutation goes through the named operations below, not generic CRUD.
    """

    queryset = Sample.objects.select_related("created_by").all()
    serializer_class = SampleSerializer
    pagination_class = SampleCursorPagination
    filter_backends = [filters.SearchFilter]
    search_fields = ["barcode", "accession_number", "subject_ref"]

    def get_queryset(self):
        qs = super().get_queryset()
        state = self.request.query_params.get("state")
        if state:
            qs = qs.filter(state=state)
        sample_type = self.request.query_params.get("sample_type")
        if sample_type:
            qs = qs.filter(sample_type=sample_type)
        return qs

    @action(detail=False, methods=["post"], url_path="accession-batch")
    def accession_batch(self, request):
        serializer = AccessionBatchInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = services.accession_batch(
            scans=data["scans"], user=request.user, reason_code=data["reason_code"]
        )
        return Response(
            {
                "created": SampleSerializer(result.created, many=True).data,
                "duplicates": SampleSerializer(result.duplicates, many=True).data,
                "total_scanned": result.total_scanned,
            },
            status=201,
        )

    @action(detail=True, methods=["post"])
    def accession(self, request, pk=None):
        sample = services.accession(sample=self.get_object(), user=request.user)
        return Response(SampleSerializer(sample).data)

    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        serializer = TransferInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        placement = services.transfer(
            sample=self.get_object(),
            location=data["location"],
            user=request.user,
            row_label=data["row_label"],
            column_number=data["column_number"],
            reason_code=data["reason_code"],
            reason_text=data["reason_text"],
        )
        return Response(SamplePlacementSerializer(placement).data)

    @action(detail=True, methods=["post"])
    def aliquot(self, request, pk=None):
        serializer = AliquotInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        children = services.aliquot(
            parent=self.get_object(),
            count=data["count"],
            volume_each=data["volume_each"],
            user=request.user,
            barcodes=data["barcodes"],
            reason_code=data["reason_code"],
        )
        return Response(SampleSerializer(children, many=True).data, status=201)

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        serializer = TransitionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        sample = services.transition(
            sample=self.get_object(),
            new_state=data["new_state"],
            user=request.user,
            reason_code=data["reason_code"],
            reason_text=data["reason_text"],
        )
        return Response(SampleSerializer(sample).data)

    @action(detail=True, methods=["get"])
    def lineage(self, request, pk=None):
        qs = services.lineage(self.get_object())
        return Response(SampleSerializer(qs, many=True).data)

    @action(detail=True, methods=["get"])
    def placements(self, request, pk=None):
        qs = (
            self.get_object()
            .placements.select_related("location", "placed_by", "removed_by")
            .order_by("-placed_at")
        )
        return Response(SamplePlacementSerializer(qs, many=True).data)

    @action(detail=False, methods=["get"])
    def expiring(self, request):
        """
        Unpaginated: the cursor paginator would reorder this by its
        default `-created_at`, discarding the soonest-first ordering that
        is the entire point of this report. A daily operational list is
        small enough to return whole.
        """
        days = int(request.query_params.get("days", 30))
        qs = services.expiring_within(days)
        return Response(SampleSerializer(qs, many=True).data)
