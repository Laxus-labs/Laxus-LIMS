"""
Sample accessioning, custody transfer, aliquoting, and disposal.

Every function here is a *domain operation*, not a CRUD wrapper. The
difference matters: "move this sample to box A1 position C7" is one atomic
business event with a custody consequence, and exposing it as
`PATCH /samples/{id} {location: ...}` would let a caller construct states
the lab's rules forbid. Views call these; they do not reimplement them.

Audit rows are emitted by database triggers rather than by this module (see
apps/audit/migrations/0002). What this module supplies is the *reason* —
the "why" that a trigger cannot infer — via the transaction-scoped context
in apps.audit.context.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.audit import context as audit_context
from apps.audit import signatures as audit_signatures
from apps.audit.models import ElectronicSignature
from apps.storage.models import LocationType, StorageLocation

from .errors import (
    IllegalTransition,
    InsufficientVolume,
    InvalidPosition,
    PermissionDenied,
    PositionOccupied,
    ReasonRequired,
    RejectionReasonRequired,
)
from .models import (
    ALLOWED_TRANSITIONS,
    AcceptanceDecision,
    RejectionReasonCode,
    Sample,
    SamplePlacement,
    SampleState,
)

CHECKLIST_FIELDS = (
    "container_integrity_ok",
    "seal_integrity_ok",
    "quantity_ok",
    "label_ok",
    "documentation_ok",
    "transport_conditions_ok",
)


@dataclass
class BatchResult:
    """Outcome of a batch scan — deliberately reports duplicates separately."""

    created: list[Sample]
    duplicates: list[Sample]

    @property
    def total_scanned(self) -> int:
        return len(self.created) + len(self.duplicates)


def _next_accession_number(prefix: str = "ACC") -> str:
    """
    Sequential, human-readable lab identifier.

    Derived from a Postgres sequence rather than `count() + 1`, which races
    under concurrent accessioning and would hand two samples the same
    number. The sequence is created on first use.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("CREATE SEQUENCE IF NOT EXISTS accession_number_seq")
        cursor.execute("SELECT nextval('accession_number_seq')")
        value = cursor.fetchone()[0]
    return f"{prefix}-{timezone.now():%Y}-{value:06d}"


@transaction.atomic
def accession_batch(
    *,
    scans: list[dict],
    user,
    reason_code: str = "BATCH_ACCESSION",
) -> BatchResult:
    """
    Ingest a batch of barcode scans idempotently.

    Handheld scanners double-fire, operators re-scan a rack they are unsure
    about, and flaky networks cause clients to resubmit. All three produce
    duplicate barcodes in one payload or across retries. Rather than
    erroring — which would make the operator re-do a 96-well rack because
    one tube was scanned twice — duplicates are recognised and reported
    back separately.

    Idempotency rests on the unique constraint on `barcode`, not on a
    pre-check. A `SELECT ... then INSERT` would still race two concurrent
    batches containing the same tube; letting the constraint arbitrate and
    catching the violation cannot.
    """
    created: list[Sample] = []
    duplicates: list[Sample] = []
    seen_in_payload: set[str] = set()

    with audit_context.reason(reason_code, f"Batch of {len(scans)} scans."):
        for scan in scans:
            barcode = str(scan["barcode"]).strip()
            if not barcode:
                continue

            # Collapse duplicates within a single payload before touching
            # the database at all.
            if barcode in seen_in_payload:
                existing = Sample.objects.filter(barcode=barcode).first()
                if existing:
                    duplicates.append(existing)
                continue
            seen_in_payload.add(barcode)

            try:
                # Savepoint so one duplicate does not abort the whole batch.
                with transaction.atomic():
                    sample = Sample.objects.create(
                        barcode=barcode,
                        sample_type=scan["sample_type"],
                        state=SampleState.RECEIVED,
                        customer=scan.get("customer"),
                        volume=Decimal(str(scan.get("volume", 0))),
                        volume_unit=scan.get("volume_unit", "uL"),
                        subject_ref=scan.get("subject_ref", ""),
                        collected_at=scan.get("collected_at"),
                        received_at=scan.get("received_at") or timezone.now(),
                        expires_at=scan.get("expires_at"),
                        metadata=scan.get("metadata", {}),
                        created_by=user,
                    )
            except IntegrityError:
                duplicates.append(Sample.objects.get(barcode=barcode))
                continue

            created.append(sample)

    return BatchResult(created=created, duplicates=duplicates)


@transaction.atomic
def accession(*, sample: Sample, user, reason_code: str = "ACCESSION") -> Sample:
    """Assign a lab identifier and advance out of RECEIVED."""
    sample = Sample.objects.select_for_update().get(pk=sample.pk)
    _assert_transition(sample, SampleState.ACCESSIONED)

    with audit_context.reason(reason_code):
        sample.accession_number = sample.accession_number or _next_accession_number()
        sample.accessioned_at = timezone.now()
        sample.state = SampleState.ACCESSIONED
        sample.save(update_fields=["accession_number", "accessioned_at", "state", "updated_at"])
    return sample


def _apply_checklist(sample: Sample, checklist: dict[str, bool | None]) -> list[str]:
    fields = []
    for name in CHECKLIST_FIELDS:
        if name in checklist:
            setattr(sample, name, checklist[name])
            fields.append(name)
    return fields


@transaction.atomic
def accept_sample(
    *,
    sample: Sample,
    user,
    with_deviation: bool = False,
    deviation_notes: str = "",
    checklist: dict[str, bool | None] | None = None,
    reason_code: str = "ACCEPTANCE",
    reason_text: str = "",
) -> Sample:
    """
    Pass the pre-testing acceptance review and advance to ACCESSIONED.

    Distinct from the plain `accession()` above: this is the gated path the
    spec calls for — a checklist, a decision (accepted / accepted with
    deviation), and a lab identifier assignment, all as one event. Deviation
    notes are required when accepting with a deviation; an accepted sample
    with an unexplained deviation is worse than a rejected one, because it
    looks clean.
    """
    sample = Sample.objects.select_for_update().get(pk=sample.pk)
    _assert_transition(sample, SampleState.ACCESSIONED)

    if with_deviation and not deviation_notes.strip():
        raise ReasonRequired("Accepting with a deviation requires deviation notes.")

    with audit_context.reason(reason_code, reason_text):
        fields = _apply_checklist(sample, checklist or {})
        sample.accession_number = sample.accession_number or _next_accession_number()
        sample.accessioned_at = timezone.now()
        sample.state = SampleState.ACCESSIONED
        sample.acceptance_decision = (
            AcceptanceDecision.ACCEPTED_WITH_DEVIATION
            if with_deviation
            else AcceptanceDecision.ACCEPTED
        )
        sample.deviation_notes = deviation_notes if with_deviation else ""
        sample.save(
            update_fields=fields
            + [
                "accession_number",
                "accessioned_at",
                "state",
                "acceptance_decision",
                "deviation_notes",
                "updated_at",
            ]
        )
    return sample


@transaction.atomic
def put_on_hold(
    *,
    sample: Sample,
    user,
    reason_code: str,
    reason_text: str = "",
    checklist: dict[str, bool | None] | None = None,
) -> Sample:
    """Park a sample whose acceptance review raised a question that needs
    chasing down, without forcing an accept/reject decision yet."""
    if not reason_code:
        raise ReasonRequired("Placing a sample on hold requires a reason code.")

    sample = Sample.objects.select_for_update().get(pk=sample.pk)
    _assert_transition(sample, SampleState.ON_HOLD)

    with audit_context.reason(reason_code, reason_text):
        fields = _apply_checklist(sample, checklist or {})
        sample.state = SampleState.ON_HOLD
        sample.acceptance_decision = AcceptanceDecision.ON_HOLD
        sample.save(update_fields=fields + ["state", "acceptance_decision", "updated_at"])
    return sample


@transaction.atomic
def reject_sample(
    *,
    sample: Sample,
    user,
    rejection_reason_code: str,
    password: str = "",
    reason_text: str = "",
    checklist: dict[str, bool | None] | None = None,
) -> Sample:
    """
    Reject a sample before testing begins.

    Three invariants are enforced twice, same pattern as everywhere else in
    this module: a readable error here, and a CHECK constraint on `sample`
    that holds regardless of which code path performed the write —
    `sample_rejected_requires_reason` and
    `sample_rejected_requires_qa_approval`.

    - A rejection reason code is mandatory, and codes flagged
      `requires_details` (e.g. "Other") also require reason_text.
    - Only a QA-capable role may reject. There is no separate propose/
      approve step; the actor calling this *is* the QA approval, recorded
      as `qa_approved_by` — the same shape as `can_dispose_samples` gating
      `transition()` to DISPOSED.
    - The rejection must be signed (apps.audit.signatures.sign), which
      re-authenticates the caller with `password` — being logged in is not
      the same as affirming, right now, "I am rejecting this sample".
    """
    if not getattr(user, "can_approve_qa", False):
        raise PermissionDenied("This role may not reject samples; QA approval is required.")

    try:
        reason = RejectionReasonCode.objects.get(code=rejection_reason_code, is_active=True)
    except RejectionReasonCode.DoesNotExist as exc:
        raise RejectionReasonRequired(
            f"'{rejection_reason_code}' is not a known, active rejection reason code."
        ) from exc
    if reason.requires_details and not reason_text.strip():
        raise RejectionReasonRequired(f"Reason code '{reason.code}' requires reason_text.")

    sample = Sample.objects.select_for_update().get(pk=sample.pk)
    _assert_transition(sample, SampleState.REJECTED)

    audit_signatures.sign(
        signer=user,
        password=password,
        meaning=ElectronicSignature.Meaning.REJECTED,
        table_name="sample",
        record_pk=sample.pk,
        record_state={
            "barcode": sample.barcode,
            "rejection_reason": reason.code,
            "reason_text": reason_text,
        },
    )

    with audit_context.reason(f"REJECT:{reason.code}", reason_text):
        fields = _apply_checklist(sample, checklist or {})
        sample.state = SampleState.REJECTED
        sample.acceptance_decision = AcceptanceDecision.REJECTED
        sample.rejection_reason = reason
        sample.rejection_reason_other = reason_text if reason.requires_details else ""
        sample.qa_approved_by = user
        sample.qa_approved_at = timezone.now()
        sample.save(
            update_fields=fields
            + [
                "state",
                "acceptance_decision",
                "rejection_reason",
                "rejection_reason_other",
                "qa_approved_by",
                "qa_approved_at",
                "updated_at",
            ]
        )
        SamplePlacement.objects.filter(sample=sample, removed_at__isnull=True).update(
            removed_at=timezone.now(), removed_by=user, reason_code=f"REJECT:{reason.code}"
        )
    return sample


@transaction.atomic
def mark_client_notified(*, sample: Sample, user) -> Sample:
    """Record that the client has been told about a rejected sample.
    Deliberately just a timestamp — actually sending the notification
    (email/SMS) is an integration this module does not own."""
    sample = Sample.objects.select_for_update().get(pk=sample.pk)
    sample.client_notified_at = timezone.now()
    sample.save(update_fields=["client_notified_at", "updated_at"])
    return sample


@transaction.atomic
def transfer(
    *,
    sample: Sample,
    location: StorageLocation,
    user,
    row_label: str = "",
    column_number: int | None = None,
    reason_code: str = "TRANSFER",
    reason_text: str = "",
) -> SamplePlacement:
    """
    Move a sample to a location, closing its previous placement.

    Closing the old row and opening the new one happen in one transaction,
    so the sample is never in two places and never in none.
    """
    if location.location_type == LocationType.BOX:
        if column_number is None or not row_label:
            raise InvalidPosition(
                f"{location} is a box and requires a row and column."
            )
        if not location.valid_position(row_label, column_number):
            raise InvalidPosition(
                f"{row_label}{column_number} is outside {location}'s "
                f"{location.grid_rows}x{location.grid_cols} grid."
            )
    elif column_number is not None:
        raise InvalidPosition(f"{location} has no grid, so it takes no coordinate.")

    # Re-read under lock rather than trusting the caller's instance. A
    # Sample handed in by a view may have been disposed by another request
    # since it was loaded, and an in-memory `state` would happily let us
    # move a sample that no longer physically exists.
    sample = Sample.objects.select_for_update().get(pk=sample.pk)

    if sample.state in {SampleState.DISPOSED, SampleState.EXHAUSTED}:
        raise IllegalTransition(sample.pk, sample.state, "moved")

    with audit_context.reason(reason_code, reason_text):
        SamplePlacement.objects.filter(sample=sample, removed_at__isnull=True).update(
            removed_at=timezone.now(), removed_by=user, reason_code=reason_code
        )
        try:
            with transaction.atomic():
                placement = SamplePlacement.objects.create(
                    sample=sample,
                    location=location,
                    row_label=row_label.upper(),
                    column_number=column_number,
                    placed_by=user,
                    reason_code=reason_code,
                )
        except IntegrityError as exc:
            # The partial unique index on (location, row, col) is what makes
            # two technicians scanning the same well concurrently safe. One
            # of them lands here.
            raise PositionOccupied(
                f"Position {row_label}{column_number} in {location} is already occupied."
            ) from exc

    return placement


@transaction.atomic
def aliquot(
    *,
    parent: Sample,
    count: int,
    volume_each: Decimal,
    user,
    barcodes: list[str] | None = None,
    reason_code: str = "ALIQUOT",
) -> list[Sample]:
    """
    Derive `count` child samples from `parent`, drawing volume from it.

    The parent is locked for the duration: two benches aliquoting the same
    tube concurrently would otherwise both read the same starting volume
    and between them take more than exists. Same class of bug as
    overselling stock, same fix.

    Children inherit subject and type — an aliquot of a patient's serum is
    still that patient's serum, and losing that link would break the chain
    of custody the whole system exists to maintain.
    """
    if count < 1:
        raise ValueError("Aliquot count must be at least 1.")
    volume_each = Decimal(str(volume_each))
    if volume_each <= 0:
        raise ValueError("Aliquot volume must be positive.")

    parent = Sample.objects.select_for_update().get(pk=parent.pk)

    if parent.state not in {
        SampleState.ACCESSIONED,
        SampleState.IN_STORAGE,
        SampleState.IN_TESTING,
        SampleState.ALIQUOTED,
    }:
        raise IllegalTransition(parent.pk, parent.state, SampleState.ALIQUOTED)

    total = volume_each * count
    if total > parent.volume:
        raise InsufficientVolume(parent.pk, total, parent.volume)

    if barcodes is not None and len(barcodes) != count:
        raise ValueError(f"Expected {count} barcodes, got {len(barcodes)}.")

    children: list[Sample] = []
    with audit_context.reason(reason_code, f"{count} aliquots of {volume_each} each."):
        root = parent.root or parent
        for i in range(count):
            barcode = (
                barcodes[i]
                if barcodes
                else f"{parent.barcode}-A{parent.children.count() + i + 1:02d}"
            )
            children.append(
                Sample.objects.create(
                    barcode=barcode,
                    sample_type=parent.sample_type,
                    state=SampleState.ACCESSIONED,
                    parent=parent,
                    root=root,
                    generation=parent.generation + 1,
                    volume=volume_each,
                    volume_unit=parent.volume_unit,
                    subject_ref=parent.subject_ref,
                    collected_at=parent.collected_at,
                    received_at=parent.received_at,
                    accessioned_at=timezone.now(),
                    expires_at=parent.expires_at,
                    metadata={"derived_from": str(parent.pk)},
                    created_by=user,
                )
            )

        Sample.objects.filter(pk=parent.pk).update(volume=F("volume") - total)
        parent.refresh_from_db(fields=["volume"])

        # A parent whose volume reached zero is exhausted, not merely
        # aliquoted — the distinction drives whether it can be tested again.
        new_state = (
            SampleState.EXHAUSTED if parent.volume <= 0 else SampleState.ALIQUOTED
        )
        if parent.can_transition_to(new_state):
            Sample.objects.filter(pk=parent.pk).update(state=new_state)

    return children


@transaction.atomic
def transition(
    *,
    sample: Sample,
    new_state: str,
    user,
    reason_code: str = "",
    reason_text: str = "",
    password: str = "",
) -> Sample:
    """Move a sample through its lifecycle, with the reason recorded.

    `password` is only required (and only used) when moving to DISPOSED —
    disposal is signed (ElectronicSignature.Meaning.DISPOSED), the same
    re-authentication requirement as apps.samples.services.reject_sample.
    """
    sample = Sample.objects.select_for_update().get(pk=sample.pk)
    _assert_transition(sample, new_state)

    # Terminal and irreversible actions must be explained. This is the
    # difference between an audit trail that answers "why" and one that
    # only answers "what".
    if new_state in {SampleState.DISPOSED, SampleState.REJECTED} and not reason_code:
        raise ReasonRequired(f"Moving a sample to '{new_state}' requires a reason code.")

    if new_state == SampleState.DISPOSED:
        if not getattr(user, "can_dispose_samples", False):
            raise PermissionDenied("This role may not dispose of samples.")
        audit_signatures.sign(
            signer=user,
            password=password,
            meaning=ElectronicSignature.Meaning.DISPOSED,
            table_name="sample",
            record_pk=sample.pk,
            record_state={"barcode": sample.barcode, "reason_code": reason_code, "reason_text": reason_text},
        )

    with audit_context.reason(reason_code, reason_text):
        sample.state = new_state
        fields = ["state", "updated_at"]
        if new_state == SampleState.DISPOSED:
            sample.disposed_at = timezone.now()
            fields.append("disposed_at")
            SamplePlacement.objects.filter(sample=sample, removed_at__isnull=True).update(
                removed_at=timezone.now(), removed_by=user, reason_code=reason_code
            )
        sample.save(update_fields=fields)

    return sample


def lineage(sample: Sample):
    """Every sample sharing this one's root, ancestors and descendants alike."""
    root_id = sample.root_id or sample.pk
    return Sample.objects.filter(Q(pk=root_id) | Q(root_id=root_id)).order_by(
        "generation", "created_at"
    )


def contents_of(location: StorageLocation, *, include_descendants: bool = True):
    """
    Samples currently held in a location.

    With `include_descendants`, this is a single indexed ltree query rather
    than a recursive walk — the reason the hierarchy uses a materialised
    path at all. "What is in Freezer 3?" spans shelves, racks and boxes and
    is the most common question the storage UI asks.
    """
    placements = SamplePlacement.objects.filter(removed_at__isnull=True)
    if include_descendants:
        if not location.path:
            # An empty ltree is an ancestor of everything, so proceeding
            # would return the entire sample table as "contents of this
            # freezer". Refuse loudly instead of answering wrongly.
            raise ValueError(
                f"Location {location.pk} has no materialised path; "
                "refusing a subtree query that would match every sample."
            )
        placements = placements.filter(location__path__descendant_of=location.path)
    else:
        placements = placements.filter(location=location)
    return Sample.objects.filter(pk__in=placements.values("sample_id"))


def expiring_within(days: int):
    """Samples whose stability window closes soon — a daily operational report."""
    horizon = timezone.now() + dt.timedelta(days=days)
    return Sample.objects.filter(
        expires_at__isnull=False,
        expires_at__lte=horizon,
        state__in=[
            SampleState.ACCESSIONED,
            SampleState.IN_STORAGE,
            SampleState.IN_TESTING,
            SampleState.ALIQUOTED,
        ],
    ).order_by("expires_at")


def _assert_transition(sample: Sample, new_state: str) -> None:
    if new_state not in ALLOWED_TRANSITIONS.get(sample.state, set()):
        raise IllegalTransition(sample.pk, sample.state, new_state)


# --- turnaround time / SLA / retention --------------------------------------
# These read the clock rather than a stored column — see the field
# docstrings on Sample for why expected-completion and TAT status are
# computed here, not written anywhere.

def sla_status(sample: Sample) -> dict:
    """Where a sample stands against its turnaround commitment, computed
    from its non-cancelled test orders' due dates and, failing that, the
    committed_report_date agreed with the customer at accessioning."""
    from apps.testing.models import TestOrderStatus

    now = timezone.now()
    orders = sample.test_orders.exclude(status=TestOrderStatus.CANCELLED)
    open_due_dates = [
        o.due_at
        for o in orders
        if o.due_at and o.status != TestOrderStatus.COMPLETE
    ]
    expected_completion_date = max(open_due_dates) if open_due_dates else sample.committed_report_date

    if sample.actual_completion_date:
        tat_status = "complete"
    elif expected_completion_date is None:
        # Nothing to judge against: no open order carries a due date, and
        # no committed_report_date was agreed with the customer.
        tat_status = "no_orders"
    elif expected_completion_date < now:
        tat_status = "breached"
    elif expected_completion_date - now < dt.timedelta(hours=24):
        tat_status = "due_soon"
    else:
        tat_status = "on_time"

    return {
        "expected_completion_date": expected_completion_date,
        "actual_completion_date": sample.actual_completion_date,
        "committed_report_date": sample.committed_report_date,
        "tat_status": tat_status,
    }


def overdue_samples():
    """Samples with either an overdue open test order or a missed
    committed report date — the "Overdue Sample Dashboard" the spec asks
    for. Building blocks for reminders/escalation, not the notifications
    themselves: this codebase has no scheduler (see mark_client_notified's
    docstring for the same boundary on the sample-rejection side)."""
    from apps.testing.models import TestOrder, TestOrderStatus

    now = timezone.now()
    overdue_order_sample_ids = TestOrder.objects.filter(
        status__in=[TestOrderStatus.REQUESTED, TestOrderStatus.SCHEDULED, TestOrderStatus.IN_PROGRESS],
        due_at__lt=now,
    ).values_list("sample_id", flat=True)
    committed_overdue_ids = Sample.objects.filter(
        committed_report_date__lt=now, actual_completion_date__isnull=True
    ).values_list("id", flat=True)
    ids = set(overdue_order_sample_ids) | set(committed_overdue_ids)
    return Sample.objects.filter(pk__in=ids).exclude(
        state__in=[SampleState.DISPOSED, SampleState.REJECTED]
    )


def samples_due_for_disposal():
    """Samples whose retention period has lapsed. Reporting only — actual
    disposal still requires a human to call transition(..., DISPOSED),
    signed and reasoned, never automatically."""
    return Sample.objects.filter(retention_until__lte=timezone.now()).exclude(
        state=SampleState.DISPOSED
    )
