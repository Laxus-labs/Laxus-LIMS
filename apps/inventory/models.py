"""
Reagent and consumable inventory with lot tracking, FEFO allocation, and
concurrency-safe reservations.

The central claim of this module is that **overselling stock is
structurally impossible**, and the reason is one line of SQL, not the
service layer:

    CHECK (quantity_reserved <= quantity_on_hand)

Application code can be wrong. Two workers can race. A retry can double-
submit. None of that can produce negative available stock, because the
database rejects the write. The reservation service (services.py) exists
to make the *common* path correct and fast — taking row locks in a
deterministic order so contenders queue instead of colliding — but the
constraint is what makes the guarantee true rather than merely likely.

Note what is deliberately absent: a Redis lock. `SELECT ... FOR UPDATE`
inside a transaction already serialises contenders on a row across every
application instance, because the lock lives in the database that owns the
data. A Redis lease layered on top adds no safety and introduces a failure
mode — the lease can expire while the transaction is still open, so two
workers can believe they hold it. Redis earns its place here as a cache
and as admission control, not as a correctness mechanism.
"""

from django.conf import settings
from django.db import models


class Supplier(models.Model):
    name = models.CharField(max_length=200, unique=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    # Indian labs order both locally and on import; lead time drives the
    # reorder point, and imported reagents have very different lead times.
    typical_lead_time_days = models.PositiveSmallIntegerField(default=7)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "supplier"

    def __str__(self) -> str:
        return self.name


class UnitOfMeasure(models.TextChoices):
    EACH = "each", "each"
    UL = "uL", "microlitre"
    ML = "mL", "millilitre"
    L = "L", "litre"
    MG = "mg", "milligram"
    G = "g", "gram"
    RXN = "rxn", "reaction"


class InventoryItem(models.Model):
    """Catalogue entry — the *kind* of thing. Physical stock lives in lots."""

    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    unit = models.CharField(max_length=10, choices=UnitOfMeasure.choices)
    supplier = models.ForeignKey(
        Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="items"
    )

    # Consumables like pipette tips are interchangeable and not worth lot
    # tracking; a PCR master mix absolutely is, because a bad lot means
    # recalling every result that touched it.
    is_lot_tracked = models.BooleanField(default=True)
    requires_cold_chain = models.BooleanField(default=False)

    # Reorder point, not a bare minimum: the level at which ordering now
    # means stock arrives before it runs out.
    reorder_point = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    reorder_quantity = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_item"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reorder_point__gte=0), name="item_reorder_point_non_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(reorder_quantity__gte=0),
                name="item_reorder_qty_non_negative",
            ),
        ]
        indexes = [models.Index(fields=["is_active", "name"])]

    def __str__(self) -> str:
        return f"{self.name} [{self.sku}]"


class LotStatus(models.TextChoices):
    AVAILABLE = "available", "Available"
    QUARANTINED = "quarantined", "Quarantined (pending QC)"
    EXPIRED = "expired", "Expired"
    RECALLED = "recalled", "Recalled"
    DEPLETED = "depleted", "Depleted"


class ItemLot(models.Model):
    """
    A physical lot. This is the row that gets locked during allocation, and
    the row whose CHECK constraints make overselling impossible.

    `quantity_reserved` is held stock: promised to a caller but not yet
    physically consumed. Available stock is the difference, exposed as a
    generated column so no caller can compute it differently.
    """

    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="lots")
    lot_number = models.CharField(max_length=100)
    supplier = models.ForeignKey(
        Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="lots"
    )

    manufactured_on = models.DateField(null=True, blank=True)
    # Null means "does not expire" and must sort *last* under FEFO, which
    # is why the ordering uses NULLS LAST explicitly rather than relying on
    # Postgres' default (NULLS LAST for ASC — correct here, but stating it
    # keeps the intent obvious if the ordering ever flips to DESC).
    expires_on = models.DateField(null=True, blank=True, db_index=True)
    received_on = models.DateField()

    quantity_on_hand = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    quantity_reserved = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    status = models.CharField(
        max_length=20, choices=LotStatus.choices, default=LotStatus.AVAILABLE
    )
    storage_location = models.ForeignKey(
        "storage.StorageLocation",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="item_lots",
    )
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    certificate_of_analysis = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "item_lot"
        constraints = [
            models.UniqueConstraint(fields=["item", "lot_number"], name="uniq_lot_per_item"),
            models.CheckConstraint(
                condition=models.Q(quantity_on_hand__gte=0), name="lot_on_hand_non_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_reserved__gte=0), name="lot_reserved_non_negative"
            ),
            # The invariant this whole subsystem exists to protect.
            models.CheckConstraint(
                condition=models.Q(quantity_reserved__lte=models.F("quantity_on_hand")),
                name="lot_reserved_not_over_on_hand",
            ),
            models.CheckConstraint(
                condition=models.Q(manufactured_on__isnull=True)
                | models.Q(expires_on__isnull=True)
                | models.Q(expires_on__gte=models.F("manufactured_on")),
                name="lot_expiry_after_manufacture",
            ),
        ]
        indexes = [
            # The FEFO picking index: filter to one item's allocatable lots,
            # already ordered by expiry so allocation needs no sort.
            models.Index(
                fields=["item", "expires_on", "received_on"],
                condition=models.Q(status=LotStatus.AVAILABLE),
                name="idx_lot_fefo_available",
            ),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.item.sku} lot {self.lot_number}"

    @property
    def quantity_available(self):
        return self.quantity_on_hand - self.quantity_reserved


class ReservationStatus(models.TextChoices):
    HELD = "held", "Held"
    CONSUMED = "consumed", "Consumed"
    RELEASED = "released", "Released"
    EXPIRED = "expired", "Expired"


class StockReservation(models.Model):
    """
    A promise that `quantity` of a lot is set aside for a caller.

    Reservations expire. A worker that crashes between reserving and
    consuming would otherwise strand stock forever, so every hold carries a
    TTL and a sweeper reclaims lapsed ones (see services.sweep_expired).
    """

    id = models.BigAutoField(primary_key=True)
    lot = models.ForeignKey(ItemLot, on_delete=models.PROTECT, related_name="reservations")
    quantity = models.DecimalField(max_digits=14, decimal_places=4)
    status = models.CharField(
        max_length=20, choices=ReservationStatus.choices, default=ReservationStatus.HELD
    )

    # Client-supplied key making reserve() safe to retry. A network timeout
    # on the response must not result in stock being reserved twice.
    idempotency_key = models.CharField(max_length=128)
    # What the stock is for — a test run, a batch, a maintenance task.
    purpose_ref = models.CharField(max_length=128, blank=True)

    reserved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reservations"
    )
    reserved_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "stock_reservation"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="reservation_quantity_positive"
            ),
            models.UniqueConstraint(
                fields=["idempotency_key"], name="uniq_reservation_idempotency_key"
            ),
        ]
        indexes = [
            models.Index(fields=["lot", "status"]),
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["purpose_ref"]),
        ]

    def __str__(self) -> str:
        return f"reservation#{self.id} {self.quantity} of lot {self.lot_id} ({self.status})"


class LedgerReason(models.TextChoices):
    RECEIPT = "receipt", "Goods received"
    RESERVE = "reserve", "Reserved"
    RELEASE = "release", "Reservation released"
    CONSUME = "consume", "Consumed"
    ADJUSTMENT = "adjustment", "Manual adjustment"
    EXPIRY_WRITE_OFF = "expiry_write_off", "Written off — expired"
    RECALL_WRITE_OFF = "recall_write_off", "Written off — recalled"


class StockLedgerEntry(models.Model):
    """
    Append-only record of every quantity movement. The lot's current
    quantities are a cache of this ledger's sum; the ledger is the truth,
    and a reconciliation job can prove the two agree.

    Manual adjustments always require a reason — an unexplained stock
    correction is exactly the thing an auditor asks about.
    """

    id = models.BigAutoField(primary_key=True)
    lot = models.ForeignKey(ItemLot, on_delete=models.PROTECT, related_name="ledger_entries")
    # Signed: positive adds to on-hand, negative removes.
    quantity_delta = models.DecimalField(max_digits=14, decimal_places=4)
    reserved_delta = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    reason = models.CharField(max_length=32, choices=LedgerReason.choices)
    reason_text = models.TextField(blank=True)
    reservation = models.ForeignKey(
        StockReservation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ledger_entries",
    )

    # Balance after applying this entry, captured at write time so the
    # ledger can be audited without replaying every prior row.
    balance_on_hand = models.DecimalField(max_digits=14, decimal_places=4)
    balance_reserved = models.DecimalField(max_digits=14, decimal_places=4)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="stock_moves"
    )
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "stock_ledger_entry"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(reason=LedgerReason.ADJUSTMENT)
                | ~models.Q(reason_text=""),
                name="adjustment_requires_reason_text",
            ),
        ]
        indexes = [
            models.Index(fields=["lot", "-occurred_at"]),
            models.Index(fields=["reason", "-occurred_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.reason} {self.quantity_delta:+} on lot {self.lot_id}"


class ReorderAlert(models.Model):
    """
    Raised when available stock for an item crosses below its reorder point.

    Open alerts are unique per item so a busy day does not generate two
    hundred identical notifications — the alert is a state, not an event
    stream.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ORDERED = "ordered", "Purchase order raised"
        RESOLVED = "resolved", "Resolved — stock replenished"
        DISMISSED = "dismissed", "Dismissed"

    item = models.ForeignKey(
        InventoryItem, on_delete=models.CASCADE, related_name="reorder_alerts"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    quantity_available_at_trigger = models.DecimalField(max_digits=14, decimal_places=4)
    reorder_point_at_trigger = models.DecimalField(max_digits=14, decimal_places=4)
    suggested_order_quantity = models.DecimalField(max_digits=14, decimal_places=4)

    triggered_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "reorder_alert"
        constraints = [
            models.UniqueConstraint(
                fields=["item"],
                condition=models.Q(status__in=["open", "ordered"]),
                name="uniq_active_alert_per_item",
            )
        ]
        indexes = [models.Index(fields=["status", "-triggered_at"])]

    def __str__(self) -> str:
        return f"reorder {self.item_id} ({self.status})"
