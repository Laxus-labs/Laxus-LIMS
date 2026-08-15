"""
Read serializers for `Sample`/`SamplePlacement`, plus plain (non-model)
input serializers for each domain operation in apps/samples/services.py.

The input serializers exist to validate a request body before it reaches a
service function — they never call `.save()`. Mutation always happens
through the named service function a view action calls, never through a
serializer writing directly to the model (see the module docstring in
services.py for why: a generic writable serializer would let a caller
construct states the lifecycle rules forbid).
"""

from decimal import Decimal

from rest_framework import serializers

from apps.customers.models import Customer
from apps.storage.models import StorageLocation

from .models import RejectionReasonCode, Sample, SamplePlacement, SampleState, SampleType, VolumeUnit


class CustomerMiniSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    code = serializers.CharField()


class RejectionReasonCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RejectionReasonCode
        fields = ["code", "label", "requires_details"]


class LocationMiniSerializer(serializers.Serializer):
    """Just enough to place a sample on a map without a second round trip."""

    id = serializers.IntegerField()
    code = serializers.CharField()
    name = serializers.CharField()
    location_type = serializers.CharField()


class SamplePlacementSerializer(serializers.ModelSerializer):
    location = LocationMiniSerializer(read_only=True)
    placed_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    removed_by = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = SamplePlacement
        fields = [
            "id",
            "sample",
            "location",
            "row_label",
            "column_number",
            "placed_at",
            "placed_by",
            "removed_at",
            "removed_by",
            "reason_code",
        ]


class SampleSerializer(serializers.ModelSerializer):
    created_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    qa_approved_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    customer = CustomerMiniSerializer(read_only=True)
    rejection_reason = RejectionReasonCodeSerializer(read_only=True)
    current_placement = serializers.SerializerMethodField()

    class Meta:
        model = Sample
        fields = [
            "id",
            "barcode",
            "accession_number",
            "sample_type",
            "state",
            "customer",
            "parent",
            "root",
            "generation",
            "volume",
            "volume_unit",
            "subject_ref",
            "collected_at",
            "received_at",
            "accessioned_at",
            "disposed_at",
            "expires_at",
            "acceptance_decision",
            "deviation_notes",
            "container_integrity_ok",
            "seal_integrity_ok",
            "quantity_ok",
            "label_ok",
            "documentation_ok",
            "transport_conditions_ok",
            "rejection_reason",
            "rejection_reason_other",
            "qa_approved_by",
            "qa_approved_at",
            "client_notified_at",
            "metadata",
            "created_by",
            "created_at",
            "updated_at",
            "current_placement",
        ]

    def get_current_placement(self, sample: Sample):
        placement = (
            sample.placements.filter(removed_at__isnull=True)
            .select_related("location")
            .first()
        )
        if placement is None:
            return None
        return SamplePlacementSerializer(placement).data


class ScanInputSerializer(serializers.Serializer):
    """One barcode scan within a batch accession payload."""

    barcode = serializers.CharField(max_length=64)
    sample_type = serializers.ChoiceField(choices=SampleType.choices)
    customer_id = serializers.PrimaryKeyRelatedField(
        source="customer", queryset=Customer.objects.all(), required=False, allow_null=True, default=None
    )
    volume = serializers.DecimalField(
        max_digits=12, decimal_places=4, required=False, default=0
    )
    volume_unit = serializers.ChoiceField(
        choices=VolumeUnit.choices, required=False, default=VolumeUnit.UL
    )
    subject_ref = serializers.CharField(
        max_length=64, required=False, allow_blank=True, default=""
    )
    collected_at = serializers.DateTimeField(required=False, allow_null=True)
    received_at = serializers.DateTimeField(required=False, allow_null=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    metadata = serializers.JSONField(required=False, default=dict)


class AccessionBatchInputSerializer(serializers.Serializer):
    scans = ScanInputSerializer(many=True, allow_empty=False)
    reason_code = serializers.CharField(
        max_length=64, required=False, default="BATCH_ACCESSION"
    )


class AcceptanceChecklistInputSerializer(serializers.Serializer):
    """Every field optional — a caller may record only what they actually
    checked, and re-checking is fine (accept/hold/reject all accept this)."""

    container_integrity_ok = serializers.BooleanField(required=False, allow_null=True, default=None)
    seal_integrity_ok = serializers.BooleanField(required=False, allow_null=True, default=None)
    quantity_ok = serializers.BooleanField(required=False, allow_null=True, default=None)
    label_ok = serializers.BooleanField(required=False, allow_null=True, default=None)
    documentation_ok = serializers.BooleanField(required=False, allow_null=True, default=None)
    transport_conditions_ok = serializers.BooleanField(required=False, allow_null=True, default=None)

    def to_internal_value(self, data):
        validated = super().to_internal_value(data)
        # Only pass along keys the caller actually sent, so an omitted
        # field doesn't overwrite a previously-recorded check with None.
        return {key: validated[key] for key in data if key in validated}


class AcceptInputSerializer(serializers.Serializer):
    with_deviation = serializers.BooleanField(required=False, default=False)
    deviation_notes = serializers.CharField(required=False, allow_blank=True, default="")
    checklist = AcceptanceChecklistInputSerializer(required=False, default=dict)
    reason_code = serializers.CharField(max_length=64, required=False, default="ACCEPTANCE")
    reason_text = serializers.CharField(required=False, allow_blank=True, default="")


class HoldInputSerializer(serializers.Serializer):
    reason_code = serializers.CharField(max_length=64)
    reason_text = serializers.CharField(required=False, allow_blank=True, default="")
    checklist = AcceptanceChecklistInputSerializer(required=False, default=dict)


class RejectInputSerializer(serializers.Serializer):
    rejection_reason_code = serializers.CharField(max_length=16)
    reason_text = serializers.CharField(required=False, allow_blank=True, default="")
    checklist = AcceptanceChecklistInputSerializer(required=False, default=dict)


class TransferInputSerializer(serializers.Serializer):
    location_id = serializers.PrimaryKeyRelatedField(
        source="location", queryset=StorageLocation.objects.all()
    )
    row_label = serializers.CharField(max_length=2, required=False, allow_blank=True, default="")
    column_number = serializers.IntegerField(required=False, allow_null=True, default=None)
    reason_code = serializers.CharField(max_length=64, required=False, default="TRANSFER")
    reason_text = serializers.CharField(required=False, allow_blank=True, default="")


class AliquotInputSerializer(serializers.Serializer):
    count = serializers.IntegerField(min_value=1)
    volume_each = serializers.DecimalField(
        max_digits=12, decimal_places=4, min_value=Decimal("0")
    )
    barcodes = serializers.ListField(
        child=serializers.CharField(max_length=64), required=False, allow_null=True, default=None
    )
    reason_code = serializers.CharField(max_length=64, required=False, default="ALIQUOT")


class TransitionInputSerializer(serializers.Serializer):
    new_state = serializers.ChoiceField(choices=SampleState.choices)
    reason_code = serializers.CharField(max_length=64, required=False, allow_blank=True, default="")
    reason_text = serializers.CharField(required=False, allow_blank=True, default="")
