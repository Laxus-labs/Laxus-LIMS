from django.http import HttpResponse
from django.template.loader import render_to_string
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.samples.models import Sample

from . import services
from .models import CertificateOfAnalysis, Specification, TestDefinition, TestOrder, TestResult
from .serializers import (
    CancelOrderInputSerializer,
    CertificateOfAnalysisSerializer,
    CloseOosInputSerializer,
    EnterResultInputSerializer,
    PlaceOrderInputSerializer,
    ReassignOrderInputSerializer,
    RejectResultInputSerializer,
    SignedActionInputSerializer,
    SpecificationSerializer,
    TestDefinitionSerializer,
    TestOrderSerializer,
    TestResultSerializer,
)


class TestDefinitionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read-only, like storage locations — the test catalogue is
    admin/fixture-managed, not something an analyst edits from the API."""

    queryset = TestDefinition.objects.filter(is_active=True).order_by("code")
    serializer_class = TestDefinitionSerializer
    pagination_class = None


class SpecificationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = Specification.objects.select_related("test_definition", "created_by").filter(
        superseded_at__isnull=True
    )
    serializer_class = SpecificationSerializer
    pagination_class = None

    def get_queryset(self):
        qs = super().get_queryset()
        test_definition = self.request.query_params.get("test_definition")
        if test_definition:
            qs = qs.filter(test_definition_id=test_definition)
        return qs


class TestOrderViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet
):
    queryset = TestOrder.objects.select_related(
        "sample", "test_definition", "assigned_to", "requested_by"
    ).all()
    serializer_class = TestOrderSerializer
    pagination_class = None

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        sample = self.request.query_params.get("sample")
        if sample:
            qs = qs.filter(sample_id=sample)
        assigned_to = self.request.query_params.get("assigned_to")
        if assigned_to:
            qs = qs.filter(assigned_to_id=assigned_to)
        return qs.order_by("priority", "due_at")

    def create(self, request, *args, **kwargs):
        serializer = PlaceOrderInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = services.place_order(user=request.user, **serializer.validated_data)
        return Response(TestOrderSerializer(order).data, status=201)

    @action(detail=True, methods=["post"])
    def reassign(self, request, pk=None):
        serializer = ReassignOrderInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = services.reassign_order(
            order=self.get_object(), assigned_to=serializer.validated_data["assigned_to"], user=request.user
        )
        return Response(TestOrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        order = services.start_order(order=self.get_object(), user=request.user)
        return Response(TestOrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        serializer = CancelOrderInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = services.cancel_order(
            order=self.get_object(), user=request.user, reason_text=serializer.validated_data["reason_text"]
        )
        return Response(TestOrderSerializer(order).data)

    @action(detail=True, methods=["post"], url_path="enter-result")
    def enter_result(self, request, pk=None):
        serializer = EnterResultInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = services.enter_result(
            order=self.get_object(),
            user=request.user,
            numeric_value=data["numeric_value"],
            text_value=data["text_value"],
            instrument_ref=data["instrument_ref"],
            comments=data["comments"],
            reason_code=data["reason_code"],
        )
        return Response(TestResultSerializer(result).data, status=201)

    @action(detail=False, methods=["get"])
    def overdue(self, request):
        return Response(TestOrderSerializer(services.overdue_orders(), many=True).data)

    @action(detail=False, methods=["get"], url_path="due-soon")
    def due_soon(self, request):
        hours = int(request.query_params.get("hours", 24))
        return Response(TestOrderSerializer(services.due_soon_orders(hours=hours), many=True).data)

    @action(detail=False, methods=["get"], url_path="tat-summary")
    def tat_summary(self, request):
        return Response(services.tat_summary())


class TestResultViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = TestResult.objects.select_related(
        "order", "order__sample", "order__test_definition", "entered_by", "reviewed_by", "approved_by"
    ).all()
    serializer_class = TestResultSerializer
    pagination_class = None

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        out_of_spec = self.request.query_params.get("is_out_of_spec")
        if out_of_spec is not None:
            qs = qs.filter(is_out_of_spec=out_of_spec.lower() == "true")
        return qs.order_by("-entered_at")

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        result = services.submit_result(result=self.get_object(), user=request.user)
        return Response(TestResultSerializer(result).data)

    @action(detail=True, methods=["post"])
    def review(self, request, pk=None):
        serializer = SignedActionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = services.review_result(
            result=self.get_object(), user=request.user, password=data["password"], reason_text=data["reason_text"]
        )
        return Response(TestResultSerializer(result).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        serializer = SignedActionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = services.approve_result(
            result=self.get_object(), user=request.user, password=data["password"], reason_text=data["reason_text"]
        )
        return Response(TestResultSerializer(result).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        serializer = RejectResultInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.reject_result(
            result=self.get_object(), user=request.user, reason_text=serializer.validated_data["reason_text"]
        )
        return Response(TestResultSerializer(result).data)

    @action(detail=True, methods=["post"], url_path="close-oos")
    def close_oos(self, request, pk=None):
        serializer = CloseOosInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = services.close_oos(
            result=self.get_object(),
            user=request.user,
            password=data["password"],
            resolution_text=data["resolution_text"],
        )
        return Response(TestResultSerializer(result).data)


class CertificateViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = CertificateOfAnalysis.objects.select_related("sample", "generated_by", "released_by").all()
    serializer_class = CertificateOfAnalysisSerializer
    pagination_class = None

    def get_queryset(self):
        qs = super().get_queryset()
        sample = self.request.query_params.get("sample")
        if sample:
            qs = qs.filter(sample_id=sample)
        return qs.order_by("-generated_at")

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        serializer = SignedActionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        coa = services.release_coa(
            coa=self.get_object(), user=request.user, password=data["password"], reason_text=data["reason_text"]
        )
        return Response(CertificateOfAnalysisSerializer(coa).data)

    @action(detail=True, methods=["get"])
    def html(self, request, pk=None):
        """
        A printable rendering of the certificate. There is no PDF library
        in this codebase's dependencies (see docs/architecture-decisions.md
        #9 on deliberately minimal dependencies) — this returns a clean,
        print-styled HTML document instead, which the browser's own
        "Print to PDF" turns into a PDF without the lab needing to run a
        rendering service. The frontend opens this in a new tab.
        """
        coa = self.get_object()
        html = render_to_string("testing/coa.html", {"coa": coa})
        return HttpResponse(html, content_type="text/html")
