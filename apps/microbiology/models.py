"""
Microbiology master data: media and cultures.

Module 2 (Microbiology & Environment), build-order step 1 — the master data
that has no dependency on the Laboratory pipeline.

Follows Global Standards v1.0:
  - every record carries organization_id (multi-tenant isolation)
  - no hard deletes: records are retired/blocked via status
  - public_id is system-generated, never editable
  - corrections/decisions capture a reason; audit is written by the service layer
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Organization(models.Model):
    """Tenant. If the Laboratory module already defines this, delete this class
    and point the FKs below at that one instead — see NOTES.md."""
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class OrgScopedModel(models.Model):
    """Common columns per Global Standards §3."""
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="%(class)s_created", null=True, blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="%(class)s_updated", null=True, blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ---------------------------------------------------------------- media ----

class MediaMaster(OrgScopedModel):
    """Catalogue of media types the lab uses. Reference data, not a usable lot."""

    class MediaType(models.TextChoices):
        AGAR = "agar", "Agar"
        BROTH = "broth", "Broth"
        SELECTIVE = "selective", "Selective"
        ENRICHMENT = "enrichment", "Enrichment"
        OTHER = "other", "Other"

    media_code = models.CharField(max_length=30)
    media_name = models.CharField(max_length=255)
    media_type = models.CharField(max_length=20, choices=MediaType.choices)
    manufacturer = models.CharField(max_length=255, blank=True)
    storage_condition = models.CharField(max_length=255, blank=True)
    # Shelf life applied to new preparations, in days.
    default_shelf_life_days = models.PositiveIntegerField(default=30)
    # Whether a growth-promotion test is required before lots may be used.
    requires_growth_promotion = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "media_code"], name="uniq_media_code_per_org"
            )
        ]
        ordering = ["media_code"]

    def __str__(self):
        return f"{self.media_code} — {self.media_name}"


class MediaLot(OrgScopedModel):
    """A prepared batch of media. This is what an analyst actually selects.

    Status drives usability; see `is_usable`. Expiry is derived at preparation
    time from the master's shelf life, and may only be changed with a reason.
    """

    class Status(models.TextChoices):
        IN_PREPARATION = "in_preparation", "In preparation"
        PENDING_QUALIFICATION = "pending_qualification", "Pending qualification"
        QUALIFIED = "qualified", "Qualified"
        BLOCKED = "blocked", "Blocked"
        EXPIRED = "expired", "Expired"
        CONSUMED = "consumed", "Consumed"

    # Unique per tenant, not globally: sequences restart at 1 for each
    # organization so a lab sees contiguous numbering and learns nothing about
    # another tenant's volume.
    public_id = models.CharField(max_length=20, editable=False)
    media = models.ForeignKey(MediaMaster, on_delete=models.PROTECT, related_name="lots")

    # Source material
    dehydrated_lot_ref = models.CharField(
        max_length=100, blank=True,
        help_text="Supplier lot of dehydrated media / ingredients.",
    )

    prepared_at = models.DateTimeField(default=timezone.now)
    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="media_prepared"
    )
    sterilization_ref = models.CharField(
        max_length=100, blank=True, help_text="Autoclave / cycle reference."
    )
    final_ph = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    storage_condition = models.CharField(max_length=255, blank=True)
    expiry_date = models.DateField()
    expiry_override_reason = models.TextField(blank=True)

    qty_prepared = models.DecimalField(max_digits=12, decimal_places=3)
    qty_used = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    unit = models.CharField(max_length=20, default="mL")

    status = models.CharField(
        max_length=25, choices=Status.choices, default=Status.IN_PREPARATION
    )

    class Meta:
        ordering = ["-prepared_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "public_id"], name="uniq_media_lot_public_id_per_org"
            )
        ]
        indexes = [models.Index(fields=["organization", "status", "expiry_date"])]

    def __str__(self):
        return self.public_id

    # -- derived -----------------------------------------------------------
    @property
    def qty_remaining(self):
        return self.qty_prepared - self.qty_used

    @property
    def is_expired(self):
        return self.expiry_date < timezone.localdate()

    def is_usable(self):
        """Single source of truth for 'may an analyst select this lot?'.

        Spec §5.5: expired or growth-promotion-failed media is excluded from
        selection lists AND rejected server-side.
        """
        if self.status != self.Status.QUALIFIED:
            return False
        if self.is_expired:
            return False
        if self.qty_remaining <= 0:
            return False
        return True

    def clean(self):
        if self.expiry_date and self.prepared_at:
            if self.expiry_date <= timezone.localdate(self.prepared_at):
                raise ValidationError(
                    {"expiry_date": "Expiry date must be after the preparation date."}
                )
        if self.qty_prepared is not None and self.qty_prepared <= 0:
            raise ValidationError({"qty_prepared": "Quantity prepared must be greater than zero."})
        if self.qty_used is not None and self.qty_prepared is not None:
            if self.qty_used > self.qty_prepared:
                raise ValidationError(
                    {"qty_used": "Quantity used cannot exceed quantity prepared."}
                )


class MediaQualification(OrgScopedModel):
    """Growth promotion / qualification result for a media lot.

    A FAIL blocks the lot permanently (spec §5.5). Append-only: a re-test
    creates a new row rather than editing the previous one.
    """

    class Result(models.TextChoices):
        PASS = "pass", "Pass"
        FAIL = "fail", "Fail"

    media_lot = models.ForeignKey(
        MediaLot, on_delete=models.PROTECT, related_name="qualifications"
    )
    test_organism = models.ForeignKey(
        "microbiology.OrganismMaster", on_delete=models.PROTECT, related_name="+"
    )
    inoculum_reference = models.CharField(max_length=255, blank=True)
    acceptance_criteria = models.TextField(
        blank=True, help_text="Copied from the approved method at test time."
    )
    result = models.CharField(max_length=10, choices=Result.choices)
    observations = models.TextField(blank=True)
    reason_code = models.CharField(
        max_length=50, blank=True,
        help_text="Required on FAIL: gp_test_failed, sterility_check_failed, "
                  "ph_out_of_range, contamination_observed, other.",
    )

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="media_qual_reviewed", null=True, blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="media_qual_approved", null=True, blank=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        if self.result == self.Result.FAIL and not self.reason_code:
            raise ValidationError(
                {"reason_code": "A reason code is required when the result is Fail."}
            )

    def __str__(self):
        return f"{self.media_lot_id} — {self.result}"


# -------------------------------------------------------------- culture ----

class OrganismMaster(OrgScopedModel):
    """Reference organism catalogue (ATCC and equivalents)."""

    class OrganismType(models.TextChoices):
        BACTERIA = "bacteria", "Bacteria"
        YEAST = "yeast", "Yeast"
        MOULD = "mould", "Mould"
        OTHER = "other", "Other"

    organism_code = models.CharField(max_length=30)
    organism_name = models.CharField(max_length=255)
    organism_type = models.CharField(max_length=20, choices=OrganismType.choices)
    strain_number = models.CharField(max_length=100, blank=True)
    reference_collection = models.CharField(
        max_length=100, blank=True, help_text="e.g. ATCC, NCTC, MTCC."
    )
    # Passage beyond this is not permitted for routine use.
    max_passage_number = models.PositiveIntegerField(
        default=5, help_text="Routine use is blocked beyond this passage."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "organism_code"], name="uniq_organism_code_per_org"
            )
        ]
        ordering = ["organism_code"]

    def __str__(self):
        return f"{self.organism_code} — {self.organism_name}"


class CultureLot(OrgScopedModel):
    """A physical culture held by the lab, derived from an organism master."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        QUARANTINED = "quarantined", "Quarantined"
        EXPIRED = "expired", "Expired"
        DISPOSED = "disposed", "Disposed"

    public_id = models.CharField(max_length=20, editable=False)  # unique per tenant
    organism = models.ForeignKey(
        OrganismMaster, on_delete=models.PROTECT, related_name="culture_lots"
    )
    source = models.CharField(max_length=255, blank=True)
    received_date = models.DateField()
    passage_number = models.PositiveIntegerField(default=0)
    storage_location = models.CharField(max_length=255, blank=True)
    storage_condition = models.CharField(max_length=255, blank=True)
    expiry_review_date = models.DateField()
    qualification_reference = models.CharField(
        max_length=255, blank=True, help_text="Authentication / qualification evidence."
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    disposal_reason_code = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["-received_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "public_id"], name="uniq_culture_lot_public_id_per_org"
            )
        ]
        indexes = [models.Index(fields=["organization", "status", "expiry_review_date"])]

    def __str__(self):
        return self.public_id

    @property
    def is_expired(self):
        return self.expiry_review_date < timezone.localdate()

    def is_usable(self):
        """Spec §5.5: only authorized, unexpired cultures within passage limit."""
        if self.status != self.Status.ACTIVE:
            return False
        if self.is_expired:
            return False
        if self.passage_number > self.organism.max_passage_number:
            return False
        return True

    def clean(self):
        if self.received_date and self.received_date > timezone.localdate():
            raise ValidationError({"received_date": "Receipt date cannot be in the future."})
        if self.expiry_review_date and self.received_date:
            if self.expiry_review_date <= self.received_date:
                raise ValidationError(
                    {"expiry_review_date": "Expiry/review date must be after the receipt date."}
                )


class CultureMovement(OrgScopedModel):
    """Append-only issue / return / disposal log for cultures."""

    class Action(models.TextChoices):
        ISSUE = "issue", "Issue"
        RETURN = "return", "Return"
        DISPOSE = "dispose", "Dispose"

    culture_lot = models.ForeignKey(
        CultureLot, on_delete=models.PROTECT, related_name="movements"
    )
    action = models.CharField(max_length=10, choices=Action.choices)
    issued_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="cultures_received", null=True, blank=True,
    )
    # Linked to a micro test once Laboratory test instances exist (see NOTES.md).
    test_reference = models.CharField(max_length=50, blank=True)
    passage_number_at_action = models.PositiveIntegerField(null=True, blank=True)
    performed_at = models.DateTimeField(default=timezone.now)
    reason_code = models.CharField(max_length=50, blank=True)
    remarks = models.TextField(blank=True)

    class Meta:
        ordering = ["-performed_at"]

    def clean(self):
        if self.action == self.Action.DISPOSE and not self.reason_code:
            raise ValidationError(
                {"reason_code": "A reason code is required when disposing of a culture."}
            )

    def __str__(self):
        return f"{self.culture_lot_id} — {self.action}"
