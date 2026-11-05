"""Admin for microbiology master data.

Deliberately restrictive: no delete permission anywhere (Global Standards §4 —
no hard deletes), and derived/system fields are read-only so they cannot be
edited by hand.
"""
from django.contrib import admin

from .models import (
    CultureLot,
    CultureMovement,
    MediaLot,
    MediaMaster,
    MediaQualification,
    Organization,
    OrganismMaster,
)


class NoDeleteAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Organization)
class OrganizationAdmin(NoDeleteAdmin):
    list_display = ["name", "is_active", "created_at"]


@admin.register(MediaMaster)
class MediaMasterAdmin(NoDeleteAdmin):
    list_display = [
        "media_code", "media_name", "media_type",
        "default_shelf_life_days", "requires_growth_promotion", "is_active",
    ]
    list_filter = ["media_type", "requires_growth_promotion", "is_active"]
    search_fields = ["media_code", "media_name", "manufacturer"]


@admin.register(MediaLot)
class MediaLotAdmin(NoDeleteAdmin):
    list_display = [
        "public_id", "media", "status", "prepared_at",
        "expiry_date", "qty_remaining_display", "usable",
    ]
    list_filter = ["status", "media"]
    search_fields = ["public_id", "dehydrated_lot_ref"]
    readonly_fields = ["public_id", "created_at", "updated_at"]
    date_hierarchy = "prepared_at"

    @admin.display(description="Remaining")
    def qty_remaining_display(self, obj):
        return f"{obj.qty_remaining} {obj.unit}"

    @admin.display(boolean=True, description="Usable")
    def usable(self, obj):
        return obj.is_usable()


@admin.register(MediaQualification)
class MediaQualificationAdmin(NoDeleteAdmin):
    list_display = ["media_lot", "test_organism", "result", "reason_code", "approved_by", "approved_at"]
    list_filter = ["result"]
    readonly_fields = ["created_at", "updated_at"]

    def has_change_permission(self, request, obj=None):
        # Append-only: a re-test is a new record, never an edit of the old one.
        return obj is None


@admin.register(OrganismMaster)
class OrganismMasterAdmin(NoDeleteAdmin):
    list_display = [
        "organism_code", "organism_name", "organism_type",
        "reference_collection", "max_passage_number", "is_active",
    ]
    list_filter = ["organism_type", "is_active"]
    search_fields = ["organism_code", "organism_name", "strain_number"]


@admin.register(CultureLot)
class CultureLotAdmin(NoDeleteAdmin):
    list_display = [
        "public_id", "organism", "status", "passage_number",
        "expiry_review_date", "usable",
    ]
    list_filter = ["status", "organism"]
    search_fields = ["public_id", "source"]
    readonly_fields = ["public_id", "created_at", "updated_at"]

    @admin.display(boolean=True, description="Usable")
    def usable(self, obj):
        return obj.is_usable()


@admin.register(CultureMovement)
class CultureMovementAdmin(NoDeleteAdmin):
    list_display = ["culture_lot", "action", "issued_to", "performed_at", "reason_code"]
    list_filter = ["action"]
    readonly_fields = ["created_at", "updated_at"]

    def has_change_permission(self, request, obj=None):
        return obj is None  # append-only log
