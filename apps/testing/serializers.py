"""
Read serializers for the testing models, plus plain input serializers for
each operation in services.py — same convention as apps/samples/serializers.py:
input serializers validate, they never `.save()`; mutation always goes
through the named service function.
"""

from rest_framework import serializers

from apps.accounts.models import User
from apps.samples.models import Sample

from .models import (
    CertificateOfAnalysis,
    ResultStatus,
    Specification,
    TestDefinition,
    TestOrder,
    TestOrderStatus,
    TestResult,
)


class TestDefinitionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestDefinition
        fields = [
            "id",
            "code",
            "name",
            "method_reference",
            "unit",
            "is_quantitative",
            "allowed_values",
            "turnaround_hours",
            "is_active",
        ]


class TestDefinitionMiniSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    unit = serializers.CharField()


class SpecificationSerializer(serializers.ModelSerializer):
    test_definition = TestDefinitionMiniSerializer(read_only=True)
    created_by = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = Specification
        fields = [
            "id",
            "test_definition",
            "sample_type",
            "version",
            "lower_limit",
            "upper_limit",
            "expected_values",
            "effective_from",
            "superseded_at",
            "created_by",
        ]


class SampleMiniSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    barcode = serializers.CharField()
    accession_number = serializers.CharField(allow_null=True)
    sample_type = serializers.CharField()


class TestOrderSerializer(serializers.ModelSerializer):
    sample = SampleMiniSerializer(read_only=True)
    test_definition = TestDefinitionMiniSerializer(read_only=True)
    assigned_to = serializers.SlugRelatedField(slug_field="username", read_only=True)
    requested_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    has_result = serializers.SerializerMethodField()

    class Meta:
        model = TestOrder
        fields = [
            "id",
            "sample",
            "test_definition",
            "status",
            "priority",
            "assigned_to",
            "requested_by",
            "requested_at",
            "due_at",
            "completed_at",
            "cancellation_reason",
            "has_result",
        ]

    def get_has_result(self, order: TestOrder) -> bool:
        return hasattr(order, "result")


class TestResultSerializer(serializers.ModelSerializer):
    order = TestOrderSerializer(read_only=True)
    entered_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    reviewed_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    approved_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    oos_closed_by = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = TestResult
        fields = [
            "id",
            "order",
            "specification",
            "numeric_value",
            "text_value",
            "is_out_of_spec",
            "status",
            "entered_by",
            "entered_at",
            "reviewed_by",
            "reviewed_at",
            "approved_by",
            "approved_at",
            "instrument_ref",
            "comments",
            "oos_closed_by",
            "oos_closed_at",
            "oos_resolution",
            "updated_at",
        ]


class CertificateOfAnalysisSerializer(serializers.ModelSerializer):
    sample = SampleMiniSerializer(read_only=True)
    generated_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    released_by = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = CertificateOfAnalysis
        fields = [
            "id",
            "coa_number",
            "sample",
            "status",
            "results_snapshot",
            "generated_by",
            "generated_at",
            "released_by",
            "released_at",
            "superseded_at",
            "superseded_by",
        ]


# --- input serializers -------------------------------------------------------

class PlaceOrderInputSerializer(serializers.Serializer):
    sample_id = serializers.PrimaryKeyRelatedField(source="sample", queryset=Sample.objects.all())
    test_definition_id = serializers.PrimaryKeyRelatedField(
        source="test_definition", queryset=TestDefinition.objects.filter(is_active=True)
    )
    priority = serializers.IntegerField(required=False, default=5, min_value=1, max_value=5)
    due_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    assigned_to_id = serializers.PrimaryKeyRelatedField(
        source="assigned_to", queryset=User.objects.all(), required=False, allow_null=True, default=None
    )
    reason_code = serializers.CharField(max_length=64, required=False, default="TEST_ORDER_PLACED")


class ReassignOrderInputSerializer(serializers.Serializer):
    assigned_to_id = serializers.PrimaryKeyRelatedField(source="assigned_to", queryset=User.objects.all())


class CancelOrderInputSerializer(serializers.Serializer):
    reason_text = serializers.CharField()


class EnterResultInputSerializer(serializers.Serializer):
    numeric_value = serializers.DecimalField(
        max_digits=14, decimal_places=4, required=False, allow_null=True, default=None
    )
    text_value = serializers.CharField(required=False, allow_blank=True, default="")
    instrument_ref = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    comments = serializers.CharField(required=False, allow_blank=True, default="")
    reason_code = serializers.CharField(max_length=64, required=False, default="RESULT_ENTRY")

    def validate(self, attrs):
        if attrs["numeric_value"] is None and not attrs["text_value"]:
            raise serializers.ValidationError("Provide numeric_value or text_value.")
        return attrs


class SignedActionInputSerializer(serializers.Serializer):
    """Shared shape for the review/approve/close-oos/release actions: a
    password (re-authentication) plus free-text context."""

    password = serializers.CharField(write_only=True)
    reason_text = serializers.CharField(required=False, allow_blank=True, default="")


class CloseOosInputSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)
    resolution_text = serializers.CharField()


class RejectResultInputSerializer(serializers.Serializer):
    reason_text = serializers.CharField()
