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

from apps.storage.models import StorageLocation

from .models import Sample, SamplePlacement, SampleState, SampleType, VolumeUnit


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
    current_placement = serializers.SerializerMethodField()

    class Meta:
        model = Sample
        fields = [
            "id",
            "barcode",
            "accession_number",
            "sample_type",
            "state",
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
