"""
Sample lifecycle, lineage, and physical placement.

Three ideas carry this module:

1. *State is a machine, not a string.* `ALLOWED_TRANSITIONS` is the single
   definition of what may follow what, and it is enforced twice: in the
   service layer for a friendly error, and by a database trigger so the
   invariant survives a shell session, a data migration, or a future
   developer who writes `sample.state = "disposed"; sample.save()`.

2. *Lineage is a tree, and aliquots are samples.* An aliquot is not a
   different kind of object; it is a child sample whose volume came out of
   its parent. Modelling it as a self-referencing tree means chain of
   custody for a derivative automatically includes its ancestors.

3. *Placement is history, not a column.* Where a sample "is" is the open
   row in `SamplePlacement`. Storing a location FK on the sample would
   lose the custody trail, which is the part a regulator actually asks for.
"""

import uuid

from django.conf import settings
from django.db import models


class SampleState(models.TextChoices):
    RECEIVED = "received", "Received"
    ON_HOLD = "on_hold", "On hold"
    ACCESSIONED = "accessioned", "Accessioned"
    IN_STORAGE = "in_storage", "In storage"
    IN_TESTING = "in_testing", "In testing"
    ALIQUOTED = "aliquoted", "Aliquoted"
    EXHAUSTED = "exhausted", "Exhausted"
    DISPOSED = "disposed", "Disposed"
    REJECTED = "rejected", "Rejected"


# The lifecycle, in one place. Terminal states map to an empty set.
#
# Note that ALIQUOTED is not terminal: taking an aliquot from a sample does
# not consume it, so the parent returns to storage or testing while its
# children live their own lives. EXHAUSTED means volume actually hit zero.
#
# ON_HOLD sits between RECEIVED and ACCESSIONED for the acceptance-review
# gate (see services.accept_sample/put_on_hold/reject_sample): a sample
# whose checklist raised a question is parked here rather than forced into
# either a pass or a fail while it is being chased down.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    SampleState.RECEIVED: {
        SampleState.ACCESSIONED,
        SampleState.ON_HOLD,
        SampleState.REJECTED,
    },
    SampleState.ON_HOLD: {SampleState.ACCESSIONED, SampleState.REJECTED},
    SampleState.ACCESSIONED: {
        SampleState.IN_STORAGE,
        SampleState.IN_TESTING,
        SampleState.REJECTED,
    },
    SampleState.IN_STORAGE: {
        SampleState.IN_TESTING,
        SampleState.ALIQUOTED,
        SampleState.EXHAUSTED,
        SampleState.DISPOSED,
    },
    SampleState.IN_TESTING: {
        SampleState.IN_STORAGE,
        SampleState.ALIQUOTED,
        SampleState.EXHAUSTED,
        SampleState.DISPOSED,
    },
    SampleState.ALIQUOTED: {
        SampleState.IN_STORAGE,
        SampleState.IN_TESTING,
        SampleState.EXHAUSTED,
        SampleState.DISPOSED,
    },
    SampleState.EXHAUSTED: {SampleState.DISPOSED},
    SampleState.REJECTED: {SampleState.DISPOSED},
    SampleState.DISPOSED: set(),
}

TERMINAL_STATES = {SampleState.DISPOSED}


class SampleType(models.TextChoices):
    BLOOD = "blood", "Whole blood"
    SERUM = "serum", "Serum"
    PLASMA = "plasma", "Plasma"
    TISSUE = "tissue", "Tissue"
    SWAB = "swab", "Swab"
    URINE = "urine", "Urine"
    DNA = "dna", "Extracted DNA"
    RNA = "rna", "Extracted RNA"
    OTHER = "other", "Other"


class VolumeUnit(models.TextChoices):
    UL = "uL", "microlitre"
    ML = "mL", "millilitre"
    MG = "mg", "milligram"
    G = "g", "gram"
    COUNT = "count", "count"


class AcceptanceDecision(models.TextChoices):
    """
    The outcome of the pre-testing acceptance review — a narrower question
    than `state`. A sample can be ACCESSIONED (state) while its acceptance
    decision recorded ACCEPTED_WITH_DEVIATION, which is information a
    reviewer needs and a bare state string would lose.
    """

    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    ACCEPTED_WITH_DEVIATION = "accepted_with_deviation", "Accepted with deviation"
    ON_HOLD = "on_hold", "On hold"
    REJECTED = "rejected", "Rejected"


class RejectionReasonCode(models.Model):
    """
    The lab's fixed list of rejection reasons (R001-R012, plus a catch-all
    "Other"). A lookup table rather than a `TextChoices` because it is
    referenced by FK from `Sample.rejection_reason` — that FK, not a free
    -text field, is what lets `sample_rejected_requires_reason` below be a
    real database constraint instead of a service-layer-only check.
    """

    code = models.CharField(max_length=16, primary_key=True)
    label = models.CharField(max_length=200)
    # "Other" (and any future catch-all) forces reason_text/
    # rejection_reason_other to be filled in — enforced in
    # services.reject_sample, since a cross-row rule like this needs a
    # trigger to enforce in the database and isn't worth one here.
    requires_details = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "rejection_reason_code"
        ordering = ["sort_order", "code"]

    def __str__(self) -> str:
        return f"{self.code} — {self.label}"


class Sample(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Scanned off the tube. Unique because a duplicate scan must be
    # recognisable as the same physical object — this is what makes batch
    # accessioning idempotent (see services.accession_batch).
    barcode = models.CharField(max_length=64, unique=True)
    # Human-facing lab identifier, assigned when the sample is accessioned.
    accession_number = models.CharField(max_length=32, null=True, blank=True, unique=True)

    sample_type = models.CharField(max_length=20, choices=SampleType.choices)
    state = models.CharField(
        max_length=20, choices=SampleState.choices, default=SampleState.RECEIVED
    )

    # The client this sample was submitted on behalf of. Nullable because
    # historical samples predate this field and internal/QC samples may
    # have no external customer at all; PROTECT because a customer with
    # samples on file must be deactivated, never deleted out from under them.
    customer = models.ForeignKey(
        "customers.Customer",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="samples",
    )

    # --- lineage ---------------------------------------------------------
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    # Denormalised root of the lineage tree. Lets "show the whole family of
    # this sample" be one indexed query instead of a recursive walk; it is
    # safe to denormalise because a sample's ancestry never changes once set.
    root = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="descendants"
    )
    generation = models.PositiveSmallIntegerField(
        default=0, help_text="0 = primary sample, 1 = aliquot, 2 = aliquot of an aliquot."
    )

    # --- quantity --------------------------------------------------------
    volume = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    volume_unit = models.CharField(
        max_length=10, choices=VolumeUnit.choices, default=VolumeUnit.UL
    )

    # --- subject / PII ---------------------------------------------------
    # Deliberately a pseudonymous reference, not a name or identifier.
    # Under India's DPDP Act the identifiable data is personal data with
    # its own retention and erasure duties; keeping it out of the sample
    # table means erasing a subject does not mean deleting lab records
    # that must be retained. Resolution lives in a separate, encrypted,
    # access-controlled store — see the README's data-protection section.
    subject_ref = models.CharField(
        max_length=64, blank=True, help_text="Pseudonymous subject key. Never raw PII."
    )

    collected_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    accessioned_at = models.DateTimeField(null=True, blank=True)
    disposed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Stability expiry, after which results are invalid."
    )

    # --- turnaround time / retention ---------------------------------------
    # What was promised to the customer at accessioning. "Expected
    # completion" and "TAT status" are deliberately NOT stored columns —
    # they are a function of the clock (services.sla_status computes them
    # on read) and a stored value would silently go stale the moment time
    # passed without a write, the same class of bug the ltree ADR warns
    # about for derived data. committed_report_date is the one date a human
    # actually decided, so it is the one that is a real column.
    committed_report_date = models.DateTimeField(null=True, blank=True)
    actual_completion_date = models.DateTimeField(
        null=True, blank=True, help_text="Set when this sample's COA is released."
    )
    delay_reason = models.TextField(blank=True)

    # How long the physical sample/record must be kept after completion,
    # per the lab's retention policy for this sample type. Null means no
    # policy has been applied yet; disposal still requires a human to act
    # (see services.transition) — this only drives the "due for disposal"
    # report, never an automatic disposal.
    retention_until = models.DateTimeField(null=True, blank=True)

    # --- acceptance review -------------------------------------------------
    # The pre-testing gate: a checklist plus a decision, recorded once (or
    # again, if put ON_HOLD and revisited) before a sample may be
    # accessioned. See services.accept_sample / put_on_hold / reject_sample.
    acceptance_decision = models.CharField(
        max_length=30, choices=AcceptanceDecision.choices, default=AcceptanceDecision.PENDING
    )
    deviation_notes = models.TextField(blank=True)

    container_integrity_ok = models.BooleanField(null=True, blank=True)
    seal_integrity_ok = models.BooleanField(null=True, blank=True)
    quantity_ok = models.BooleanField(null=True, blank=True)
    label_ok = models.BooleanField(null=True, blank=True)
    documentation_ok = models.BooleanField(null=True, blank=True)
    transport_conditions_ok = models.BooleanField(null=True, blank=True)

    rejection_reason = models.ForeignKey(
        RejectionReasonCode, null=True, blank=True, on_delete=models.PROTECT, related_name="samples"
    )
    rejection_reason_other = models.TextField(blank=True)

    # Rejection requires sign-off from a QA-capable role (see
    # accounts.User.can_approve_qa) — recorded here, not just in the audit
    # trail, so "was this QA-approved" is a column read rather than a query
    # over audit_event. Enforced by sample_rejected_requires_qa_approval.
    qa_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="qa_approved_samples",
    )
    qa_approved_at = models.DateTimeField(null=True, blank=True)
    client_notified_at = models.DateTimeField(null=True, blank=True)

    # Assay-specific fields vary per lab and per study; a JSONB column keeps
    # that flexibility without a migration per customer. Anything queried
    # often enough to need an index gets promoted to a real column.
    metadata = models.JSONField(default=dict, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_samples"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sample"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(volume__gte=0), name="sample_volume_non_negative"
            ),
            models.CheckConstraint(
                condition=~models.Q(parent=models.F("id")), name="sample_not_own_parent"
            ),
            # A derived sample must declare where it came from, and a
            # primary sample must not claim a parent.
            models.CheckConstraint(
                condition=(
                    models.Q(generation=0, parent__isnull=True)
                    | models.Q(generation__gt=0, parent__isnull=False)
                ),
                name="sample_generation_matches_parentage",
            ),
            # "Mandatory rejection reason codes" and "QA approval required
            # for rejection" from the spec, held as real invariants rather
            # than trusted to services.reject_sample alone (see ADR #1) —
            # a rejected sample with no reason or no QA sign-off cannot
            # exist, full stop, regardless of which code path wrote it.
            models.CheckConstraint(
                condition=~models.Q(state=SampleState.REJECTED)
                | models.Q(rejection_reason__isnull=False),
                name="sample_rejected_requires_reason",
            ),
            models.CheckConstraint(
                condition=~models.Q(state=SampleState.REJECTED)
                | models.Q(qa_approved_by__isnull=False),
                name="sample_rejected_requires_qa_approval",
            ),
        ]
        indexes = [
            models.Index(fields=["state"]),
            models.Index(fields=["sample_type", "state"]),
            models.Index(fields=["root"]),
            models.Index(fields=["subject_ref"]),
            models.Index(fields=["-created_at"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return self.accession_number or self.barcode

    def can_transition_to(self, new_state: str) -> bool:
        return new_state in ALLOWED_TRANSITIONS.get(self.state, set())


class SamplePlacement(models.Model):
    """
    One row per stay in a location. The open row (removed_at IS NULL) is
    where the sample is now; closed rows are its custody history.

    Occupancy is enforced by a partial unique index rather than application
    logic, so two technicians scanning the same well concurrently cannot
    both succeed — one gets a constraint violation, which the service layer
    turns into a clear conflict error.
    """

    id = models.BigAutoField(primary_key=True)
    sample = models.ForeignKey(Sample, on_delete=models.PROTECT, related_name="placements")
    location = models.ForeignKey(
        "storage.StorageLocation", on_delete=models.PROTECT, related_name="placements"
    )

    # Grid coordinate within a box. Null when stored somewhere without a
    # grid (e.g. loose on a shelf during processing).
    row_label = models.CharField(max_length=2, blank=True)
    column_number = models.PositiveSmallIntegerField(null=True, blank=True)

    placed_at = models.DateTimeField(auto_now_add=True)
    placed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="placements_made"
    )
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="placements_ended",
    )
    reason_code = models.CharField(max_length=64, blank=True)

    class Meta:
        db_table = "sample_placement"
        constraints = [
            # A sample is in exactly one place at a time.
            models.UniqueConstraint(
                fields=["sample"],
                condition=models.Q(removed_at__isnull=True),
                name="uniq_open_placement_per_sample",
            ),
            # A grid position holds at most one sample at a time.
            models.UniqueConstraint(
                fields=["location", "row_label", "column_number"],
                condition=models.Q(removed_at__isnull=True, column_number__isnull=False),
                name="uniq_occupied_position",
            ),
            models.CheckConstraint(
                condition=models.Q(removed_at__isnull=True)
                | models.Q(removed_at__gte=models.F("placed_at")),
                name="placement_removed_after_placed",
            ),
        ]
        indexes = [
            models.Index(fields=["location", "removed_at"]),
            models.Index(fields=["sample", "-placed_at"]),
        ]

    def __str__(self) -> str:
        pos = f" {self.row_label}{self.column_number}" if self.column_number else ""
        return f"{self.sample_id} @ {self.location_id}{pos}"
