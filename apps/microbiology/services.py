"""
Microbiology service layer.

All state changes go through here rather than through views or the admin, so
that ID generation, validation and status transitions happen in one place and
inside one transaction (Global Standards §5).

Audit rows are deliberately *not* written here. Postgres triggers write one for
every change, reading the acting user from the database session (bound by
apps.audit.middleware). That catches writes the ORM never sees — data
migrations, bulk update(), a psql session — which are exactly the ones an
auditor cares about. See apps/audit/context.py.

What the application must still supply is the *why*. set_reason() is called
inside the transaction, before the write it annotates, so the reason lands on
the audit row the trigger produces. Only regulated deviations need one; routine
creates are fully described by the trigger's own before/after snapshot.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.audit.context import set_reason
from apps.core.identifiers import next_public_id

from .models import (
    CultureLot,
    CultureMovement,
    MediaLot,
    MediaMaster,
    MediaQualification,
    OrganismMaster,
)


class TransitionError(Exception):
    """Raised when an action is not allowed in the record's current status.

    Views translate this to HTTP 409 with the allowed actions (Global §9).
    """

    def __init__(self, message, current_status=None, allowed=None):
        super().__init__(message)
        self.current_status = current_status
        self.allowed = allowed or []


# ------------------------------------------------------------- media ------

@transaction.atomic
def create_media_lot(*, organization, media: MediaMaster, prepared_by, user, **fields) -> MediaLot:
    """Create a prepared media lot.

    Expiry defaults to the master's shelf life; an explicit expiry_date is only
    honoured when a reason is supplied (spec §5.4 — editable by Media
    Coordinator with reason).
    """
    if not media.is_active:
        raise ValidationError({"media": "This media is inactive and cannot be prepared."})

    prepared_at = fields.pop("prepared_at", None) or timezone.now()

    expiry_date = fields.pop("expiry_date", None)
    override_reason = fields.pop("expiry_override_reason", "") or ""
    default_expiry = timezone.localdate(prepared_at) + timedelta(
        days=media.default_shelf_life_days
    )
    if expiry_date and expiry_date != default_expiry and not override_reason:
        raise ValidationError(
            {
                "expiry_override_reason": (
                    "A reason is required to set an expiry date other than the "
                    f"default of {default_expiry.isoformat()}."
                )
            }
        )
    expiry_date = expiry_date or default_expiry

    lot = MediaLot(
        organization=organization,
        media=media,
        prepared_at=prepared_at,
        prepared_by=prepared_by,
        expiry_date=expiry_date,
        expiry_override_reason=override_reason,
        created_by=user,
        updated_by=user,
        **fields,
    )
    # A lot needing growth promotion is not usable until qualified.
    lot.status = (
        MediaLot.Status.PENDING_QUALIFICATION
        if media.requires_growth_promotion
        else MediaLot.Status.QUALIFIED
    )
    lot.public_id = next_public_id("media_lot", organization.id)
    lot.full_clean(exclude=["public_id"])
    if override_reason:
        # Expiry was set to something other than the master's shelf life, so the
        # audit row has to carry why. Must be set before the write.
        set_reason("expiry_override", override_reason)
    lot.save()
    return lot


@transaction.atomic
def record_media_qualification(
    *, media_lot: MediaLot, test_organism: OrganismMaster, result: str, user, **fields
) -> MediaQualification:
    """Record a growth-promotion result. A FAIL blocks the lot."""
    allowed = {MediaLot.Status.PENDING_QUALIFICATION, MediaLot.Status.QUALIFIED}
    if media_lot.status not in allowed:
        raise TransitionError(
            f"Media lot {media_lot.public_id} cannot be qualified in status "
            f"'{media_lot.status}'.",
            current_status=media_lot.status,
            allowed=sorted(allowed),
        )

    # A growth-promotion result changes whether the lot may be used at all, so
    # both the qualification row and the lot's status change are annotated.
    set_reason(
        fields.get("reason_code", "") or f"growth_promotion_{result}",
        fields.get("observations", ""),
    )

    qual = MediaQualification(
        organization=media_lot.organization,
        media_lot=media_lot,
        test_organism=test_organism,
        result=result,
        approved_by=user,
        approved_at=timezone.now(),
        created_by=user,
        updated_by=user,
        **fields,
    )
    qual.full_clean()
    qual.save()

    media_lot.status = (
        MediaLot.Status.QUALIFIED
        if result == MediaQualification.Result.PASS
        else MediaLot.Status.BLOCKED
    )
    media_lot.updated_by = user
    media_lot.save(update_fields=["status", "updated_by", "updated_at"])
    return qual


def usable_media_lots(organization, media=None):
    """Queryset of lots an analyst may select.

    Mirrors MediaLot.is_usable() in SQL so pickers and server-side validation
    agree. Keep the two in step.
    """
    qs = MediaLot.objects.filter(
        organization=organization,
        status=MediaLot.Status.QUALIFIED,
        expiry_date__gte=timezone.localdate(),
        qty_used__lt=F("qty_prepared"),
    )
    if media is not None:
        qs = qs.filter(media=media)
    return qs.select_related("media")


# ----------------------------------------------------------- cultures -----

@transaction.atomic
def create_culture_lot(*, organization, organism: OrganismMaster, user, **fields) -> CultureLot:
    if not organism.is_active:
        raise ValidationError({"organism": "This organism is inactive."})

    lot = CultureLot(
        organization=organization,
        organism=organism,
        created_by=user,
        updated_by=user,
        **fields,
    )
    lot.public_id = next_public_id("culture_lot", organization.id)
    lot.full_clean(exclude=["public_id"])
    lot.save()
    return lot


@transaction.atomic
def issue_culture(*, culture_lot: CultureLot, issued_to, user, test_reference="", remarks=""):
    """Issue a culture to an analyst. Blocked for unusable cultures (spec §5.5)."""
    if not culture_lot.is_usable():
        raise TransitionError(
            _why_culture_unusable(culture_lot),
            current_status=culture_lot.status,
            allowed=["dispose"],
        )

    movement = CultureMovement(
        organization=culture_lot.organization,
        culture_lot=culture_lot,
        action=CultureMovement.Action.ISSUE,
        issued_to=issued_to,
        test_reference=test_reference,
        passage_number_at_action=culture_lot.passage_number,
        remarks=remarks,
        created_by=user,
        updated_by=user,
    )
    movement.full_clean()
    movement.save()
    return movement


@transaction.atomic
def dispose_culture(*, culture_lot: CultureLot, reason_code: str, user, remarks=""):
    if not reason_code:
        raise ValidationError({"reason_code": "A reason code is required to dispose of a culture."})
    if culture_lot.status == CultureLot.Status.DISPOSED:
        raise TransitionError(
            f"Culture {culture_lot.public_id} is already disposed.",
            current_status=culture_lot.status,
            allowed=[],
        )

    # Disposal is irreversible and always needs a documented reason.
    set_reason(reason_code, remarks)

    movement = CultureMovement(
        organization=culture_lot.organization,
        culture_lot=culture_lot,
        action=CultureMovement.Action.DISPOSE,
        passage_number_at_action=culture_lot.passage_number,
        reason_code=reason_code,
        remarks=remarks,
        created_by=user,
        updated_by=user,
    )
    movement.full_clean()
    movement.save()

    culture_lot.status = CultureLot.Status.DISPOSED
    culture_lot.disposal_reason_code = reason_code
    culture_lot.updated_by = user
    culture_lot.save(
        update_fields=["status", "disposal_reason_code", "updated_by", "updated_at"]
    )
    return movement


def usable_culture_lots(organization, organism=None):
    qs = CultureLot.objects.filter(
        organization=organization,
        status=CultureLot.Status.ACTIVE,
        expiry_review_date__gte=timezone.localdate(),
        passage_number__lte=F("organism__max_passage_number"),
    )
    if organism is not None:
        qs = qs.filter(organism=organism)
    return qs.select_related("organism")


def _why_culture_unusable(lot: CultureLot) -> str:
    if lot.status != CultureLot.Status.ACTIVE:
        return f"Culture {lot.public_id} is {lot.get_status_display().lower()} and cannot be issued."
    if lot.is_expired:
        return f"Culture {lot.public_id} passed its expiry/review date on {lot.expiry_review_date}."
    if lot.passage_number > lot.organism.max_passage_number:
        return (
            f"Culture {lot.public_id} is at passage {lot.passage_number}, beyond the "
            f"permitted maximum of {lot.organism.max_passage_number}."
        )
    return f"Culture {lot.public_id} is not available for issue."

