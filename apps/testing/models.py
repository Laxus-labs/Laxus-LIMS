"""
Test definitions, specifications, orders, and results with a review and
approval workflow.

The workflow encodes segregation of duties: an analyst enters a result, a
different person reviews it, and a QA approver releases it. The system
refuses to let one person do two of those on the same record. That rule is
enforced in the service layer (services.py) where it can produce a clear
error, and mirrored in a DB constraint so it cannot be bypassed.

Specifications are versioned rather than edited in place. Editing a spec
would silently change whether historical results were in or out of
specification, which is the sort of quiet retroactive rewrite that makes a
lab's data untrustworthy.
"""

from django.conf import settings
from django.db import models


class TestDefinition(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=200)
    method_reference = models.CharField(
        max_length=200, blank=True, help_text="SOP number or pharmacopoeial method."
    )
    unit = models.CharField(max_length=32, blank=True)

    # Quantitative tests produce a number checked against limits;
    # qualitative ones produce a value from a fixed set (e.g. Pass/Fail).
    is_quantitative = models.BooleanField(default=True)
    allowed_values = models.JSONField(
        default=list, blank=True, help_text="For qualitative tests, the permitted results."
    )

    # Reagents this test consumes, used to reserve stock when an order is
    # placed. Quantity is per single test run.
    consumes = models.ManyToManyField(
        "inventory.InventoryItem",
        through="TestConsumable",
        related_name="consumed_by_tests",
        blank=True,
    )

    turnaround_hours = models.PositiveIntegerField(default=24)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "test_definition"
        indexes = [models.Index(fields=["is_active", "code"])]

    def __str__(self) -> str:
        return f"{self.name} [{self.code}]"


class TestConsumable(models.Model):
    """How much of which reagent one run of a test consumes."""

    test_definition = models.ForeignKey(TestDefinition, on_delete=models.CASCADE)
    item = models.ForeignKey("inventory.InventoryItem", on_delete=models.PROTECT)
    quantity_per_run = models.DecimalField(max_digits=14, decimal_places=4)

    class Meta:
        db_table = "test_consumable"
        constraints = [
            models.UniqueConstraint(
                fields=["test_definition", "item"], name="uniq_consumable_per_test"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_per_run__gt=0),
                name="consumable_quantity_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.test_definition_id} uses {self.quantity_per_run} of {self.item_id}"


class Specification(models.Model):
    """
    Acceptance limits for a test, optionally scoped to a sample type.
    Superseded rather than edited — see module docstring.
    """

    test_definition = models.ForeignKey(
        TestDefinition, on_delete=models.PROTECT, related_name="specifications"
    )
    sample_type = models.CharField(
        max_length=20, blank=True, help_text="Blank means it applies to all sample types."
    )
    version = models.PositiveIntegerField(default=1)

    lower_limit = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    upper_limit = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    expected_values = models.JSONField(default=list, blank=True)

    effective_from = models.DateTimeField()
    superseded_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        db_table = "specification"
        constraints = [
            models.UniqueConstraint(
                fields=["test_definition", "sample_type", "version"],
                name="uniq_spec_version",
            ),
            # Only one spec may be in force for a test/sample-type pair.
            models.UniqueConstraint(
                fields=["test_definition", "sample_type"],
                condition=models.Q(superseded_at__isnull=True),
                name="uniq_active_spec",
            ),
            models.CheckConstraint(
                condition=models.Q(lower_limit__isnull=True)
                | models.Q(upper_limit__isnull=True)
                | models.Q(upper_limit__gte=models.F("lower_limit")),
                name="spec_limits_ordered",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.test_definition_id} spec v{self.version}"

    def evaluate(self, value) -> bool:
        """True when `value` is within specification."""
        if value is None:
            return False
        if self.lower_limit is not None and value < self.lower_limit:
            return False
        if self.upper_limit is not None and value > self.upper_limit:
            return False
        return True


class TestOrderStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    SCHEDULED = "scheduled", "Scheduled"
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETE = "complete", "Complete"
    CANCELLED = "cancelled", "Cancelled"


class TestOrder(models.Model):
    """A request to run a specific test against a specific sample."""

    sample = models.ForeignKey(
        "samples.Sample", on_delete=models.PROTECT, related_name="test_orders"
    )
    test_definition = models.ForeignKey(TestDefinition, on_delete=models.PROTECT)
    status = models.CharField(
        max_length=20, choices=TestOrderStatus.choices, default=TestOrderStatus.REQUESTED
    )
    priority = models.PositiveSmallIntegerField(
        default=5, help_text="1 = STAT, 5 = routine. Lower runs first."
    )

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_orders"
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "test_order"
        indexes = [
            models.Index(fields=["status", "priority", "due_at"]),
            models.Index(fields=["sample"]),
        ]

    def __str__(self) -> str:
        return f"order#{self.id} {self.test_definition_id} on {self.sample_id}"


class ResultStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted for review"
    REVIEWED = "reviewed", "Reviewed"
    APPROVED = "approved", "Approved / released"
    REJECTED = "rejected", "Rejected"


class TestResult(models.Model):
    """
    A result and its journey through review. Out-of-specification is stored
    as a computed flag at entry time against the spec then in force, so a
    later spec change cannot retroactively alter the judgement recorded.
    """

    order = models.OneToOneField(TestOrder, on_delete=models.PROTECT, related_name="result")
    specification = models.ForeignKey(
        Specification,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        help_text="The spec version in force when this result was entered.",
    )

    numeric_value = models.DecimalField(
        max_digits=14, decimal_places=4, null=True, blank=True
    )
    text_value = models.CharField(max_length=200, blank=True)
    is_out_of_spec = models.BooleanField(default=False)

    status = models.CharField(
        max_length=20, choices=ResultStatus.choices, default=ResultStatus.DRAFT
    )

    # Lots actually used, so a recalled lot can be traced to every result
    # it touched. This is the query that matters during an incident.
    lots_used = models.ManyToManyField("inventory.ItemLot", blank=True, related_name="results")

    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="entered_results"
    )
    entered_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reviewed_results",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approved_results",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    instrument_ref = models.CharField(max_length=100, blank=True)
    comments = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "test_result"
        constraints = [
            # Segregation of duties, enforced by the database. The service
            # layer checks this too, for a readable error message — but this
            # is the version that cannot be forgotten.
            models.CheckConstraint(
                condition=models.Q(reviewed_by__isnull=True)
                | ~models.Q(reviewed_by=models.F("entered_by")),
                name="reviewer_differs_from_author",
            ),
            models.CheckConstraint(
                condition=models.Q(approved_by__isnull=True)
                | ~models.Q(approved_by=models.F("entered_by")),
                name="approver_differs_from_author",
            ),
            # A result must carry a value of the right shape for its test.
            models.CheckConstraint(
                condition=models.Q(numeric_value__isnull=False) | ~models.Q(text_value=""),
                name="result_has_a_value",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "-entered_at"]),
            models.Index(fields=["is_out_of_spec", "status"]),
        ]

    def __str__(self) -> str:
        return f"result for order#{self.order_id} ({self.status})"
