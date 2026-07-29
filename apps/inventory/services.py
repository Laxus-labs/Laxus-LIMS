"""
Stock reservation engine.

CONCURRENCY MODEL — the reasoning, since this is the part worth arguing
about:

*The guarantee.* Available stock (`quantity_on_hand - quantity_reserved`)
can never go negative. There are three independent defences, and it is
worth being precise about which one actually provides the guarantee,
because it is not the one people usually name:

  1. `SELECT ... FOR UPDATE` on the candidate lots (this module).
  2. A conditional UPDATE — `WHERE quantity_on_hand >= quantity_reserved +
     take` — which is a compare-and-swap (this module).
  3. `CHECK (quantity_reserved <= quantity_on_hand)` on item_lot (models.py).

Layers 2 and 3 each prevent overselling *on their own*. This is not a
guess: `test_concurrency.py` was run with layer 1 removed and it still
passed, and with layers 1 and 2 both removed the CHECK constraint rejected
the write and the test failed. The database constraint is the guarantee.
Everything above it is about the *quality* of the failure.

*So what is the row lock actually for?* Two things, neither of which is
correctness. First, error shape: without the lock, losers discover the
conflict as a failed CAS or a constraint violation mid-transaction, and
the caller gets a rollback rather than a clean `InsufficientStock` it can
act on. Second, throughput under contention: blocking makes contenders
queue on the row and re-read it, where a retry-based scheme would have
twenty benches spinning on the same master mix at 9am, each burning a
transaction to discover it lost. Locking converts contention into waiting
rather than into wasted work.

This distinction matters more than it sounds. "We prevent overselling with
SELECT FOR UPDATE" is the answer most systems give, and it is the wrong
layer to be relying on — application locks are only as good as the code
paths that remember to take them. The constraint has no such gap.

*Why READ COMMITTED and not SERIALIZABLE.* Once the rows this transaction
depends on are locked FOR UPDATE, SERIALIZABLE adds nothing: there is no
read-skew window left to protect. It would add serialization failures
(SQLSTATE 40001) that every caller must be prepared to retry, and cost
throughput, in exchange for a guarantee already held. Isolation levels are
not a difficulty ladder to climb — the right one is the weakest that makes
the invariant true.

*Why no Redis lock.* A distributed lock would be strictly worse here. The
row lock lives in the same system as the data, so it cannot disagree with
it. A Redis lease can expire while the Postgres transaction is still open,
at which point two workers each believe they hold exclusive access — the
classic Redlock failure Kleppmann describes, and it needs fencing tokens
the database is already providing for free via row versions. Redis belongs
in this architecture as a read cache and as admission control (shedding
load before it reaches the connection pool), not as a correctness device.

*Deadlock avoidance.* Multi-lot allocations lock rows in a single
deterministic order — FEFO, which is `(expires_on NULLS LAST, received_on,
id)` — identical for every caller. Two transactions wanting the same two
lots therefore always contend on the same row first, so one waits rather
than each holding what the other needs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.audit import context as audit_context

from .errors import InsufficientStock, PermissionDenied, ReservationNotHeld
from .models import (
    InventoryItem,
    ItemLot,
    LedgerReason,
    LotStatus,
    ReorderAlert,
    ReservationStatus,
    StockLedgerEntry,
    StockReservation,
)


@dataclass(frozen=True)
class Allocation:
    """One lot's contribution to a reservation request."""

    lot: ItemLot
    quantity: Decimal
    reservation: StockReservation


def _allocatable_lots(item: InventoryItem, needed_until: dt.date | None):
    """
    Lots eligible for allocation, in FEFO order.

    Expiry is filtered rather than merely sorted: a lot that expires before
    the work will be finished is not "last resort" stock, it is unusable
    stock, and handing it out would produce results that have to be voided.
    """
    cutoff = needed_until or timezone.now().date()
    return (
        ItemLot.objects.select_for_update(of=("self",))
        .filter(item=item, status=LotStatus.AVAILABLE)
        .filter(Q(expires_on__isnull=True) | Q(expires_on__gte=cutoff))
        .filter(quantity_on_hand__gt=F("quantity_reserved"))
        # FEFO. NULLS LAST is explicit so the intent survives someone
        # flipping the direction later: "never expires" must sort last,
        # because using dated stock first is the entire point.
        .order_by(F("expires_on").asc(nulls_last=True), "received_on", "id")
    )


@transaction.atomic
def reserve(
    *,
    item: InventoryItem,
    quantity: Decimal,
    user,
    idempotency_key: str,
    purpose_ref: str = "",
    needed_until: dt.date | None = None,
    ttl_seconds: int | None = None,
) -> list[Allocation]:
    """
    Hold `quantity` of `item`, drawing from lots in FEFO order.

    Returns one Allocation per lot drawn from — a request can legitimately
    span lots when no single lot can cover it.

    Safe to retry: `idempotency_key` is unique, so a client that times out
    and re-sends gets back the original reservation rather than a second
    hold. This matters more than it looks — a retried reserve that silently
    double-books is exactly how "we had stock yesterday" incidents start.
    """
    quantity = Decimal(quantity)
    if quantity <= 0:
        raise ValueError("Reservation quantity must be positive.")

    existing = list(
        StockReservation.objects.filter(idempotency_key=idempotency_key).select_related("lot")
    )
    if existing:
        return [Allocation(lot=r.lot, quantity=r.quantity, reservation=r) for r in existing]

    ttl = ttl_seconds if ttl_seconds is not None else settings.RESERVATION_TTL_SECONDS
    expires_at = timezone.now() + dt.timedelta(seconds=ttl)

    allocations: list[Allocation] = []
    remaining = quantity

    # Iterating the locked queryset takes row locks in FEFO order. Rows
    # blocked by a competing transaction are re-read after that transaction
    # commits, so the quantities below are always current.
    for index, lot in enumerate(_allocatable_lots(item, needed_until)):
        if remaining <= 0:
            break

        available = lot.quantity_on_hand - lot.quantity_reserved
        if available <= 0:
            continue

        take = min(available, remaining)

        # Suffix the key per lot so a multi-lot request stays idempotent as
        # a whole while satisfying the unique constraint per row.
        key = idempotency_key if index == 0 else f"{idempotency_key}#{index}"
        reservation = StockReservation.objects.create(
            lot=lot,
            quantity=take,
            idempotency_key=key,
            purpose_ref=purpose_ref,
            reserved_by=user,
            expires_at=expires_at,
        )

        # Conditional UPDATE as a second line of defence. If anything did
        # slip past the lock, the WHERE clause matches zero rows rather
        # than writing an impossible quantity.
        updated = ItemLot.objects.filter(
            pk=lot.pk, quantity_on_hand__gte=F("quantity_reserved") + take
        ).update(quantity_reserved=F("quantity_reserved") + take)
        if not updated:  # pragma: no cover - defensive; the lock prevents this
            raise InsufficientStock(item.sku, quantity, available)

        lot.refresh_from_db(fields=["quantity_on_hand", "quantity_reserved"])
        _write_ledger(
            lot=lot,
            quantity_delta=Decimal("0"),
            reserved_delta=take,
            reason=LedgerReason.RESERVE,
            actor=user,
            reservation=reservation,
        )

        allocations.append(Allocation(lot=lot, quantity=take, reservation=reservation))
        remaining -= take

    if remaining > 0:
        # Nothing is committed: the atomic block unwinds every hold taken
        # above. Partial fulfilment is deliberately not offered — a bench
        # protocol needing 5 units cannot start with 3.
        available_total = quantity - remaining
        raise InsufficientStock(item.sku, quantity, available_total)

    check_reorder_threshold(item)
    return allocations


@transaction.atomic
def consume(reservation: StockReservation, *, user, reason_text: str = "") -> StockReservation:
    """
    Convert a hold into an actual decrement: stock leaves the shelf.

    Both `quantity_on_hand` and `quantity_reserved` drop, keeping the CHECK
    constraint satisfied throughout.
    """
    reservation = (
        StockReservation.objects.select_for_update().select_related("lot").get(pk=reservation.pk)
    )
    if reservation.status != ReservationStatus.HELD:
        raise ReservationNotHeld(
            f"Reservation {reservation.pk} is {reservation.status}, not held."
        )

    lot = ItemLot.objects.select_for_update().get(pk=reservation.lot_id)

    ItemLot.objects.filter(pk=lot.pk).update(
        quantity_on_hand=F("quantity_on_hand") - reservation.quantity,
        quantity_reserved=F("quantity_reserved") - reservation.quantity,
    )
    reservation.status = ReservationStatus.CONSUMED
    reservation.settled_at = timezone.now()
    reservation.save(update_fields=["status", "settled_at"])

    lot.refresh_from_db(fields=["quantity_on_hand", "quantity_reserved"])
    _write_ledger(
        lot=lot,
        quantity_delta=-reservation.quantity,
        reserved_delta=-reservation.quantity,
        reason=LedgerReason.CONSUME,
        actor=user,
        reservation=reservation,
        reason_text=reason_text,
    )

    if lot.quantity_on_hand <= 0:
        ItemLot.objects.filter(pk=lot.pk).update(status=LotStatus.DEPLETED)

    check_reorder_threshold(lot.item)
    return reservation


@transaction.atomic
def release(reservation: StockReservation, *, user, reason_text: str = "") -> StockReservation:
    """Give a hold back without consuming it — the run was cancelled."""
    reservation = (
        StockReservation.objects.select_for_update().select_related("lot").get(pk=reservation.pk)
    )
    if reservation.status != ReservationStatus.HELD:
        raise ReservationNotHeld(
            f"Reservation {reservation.pk} is {reservation.status}, not held."
        )

    ItemLot.objects.filter(pk=reservation.lot_id).update(
        quantity_reserved=F("quantity_reserved") - reservation.quantity
    )
    reservation.status = ReservationStatus.RELEASED
    reservation.settled_at = timezone.now()
    reservation.save(update_fields=["status", "settled_at"])

    lot = ItemLot.objects.get(pk=reservation.lot_id)
    _write_ledger(
        lot=lot,
        quantity_delta=Decimal("0"),
        reserved_delta=-reservation.quantity,
        reason=LedgerReason.RELEASE,
        actor=user,
        reservation=reservation,
        reason_text=reason_text,
    )
    return reservation


def sweep_expired(*, limit: int = 500) -> int:
    """
    Reclaim holds whose TTL lapsed.

    Without this, a worker that dies between reserve and consume strands
    stock permanently — the inventory equivalent of a leaked lock. Each
    reservation is settled in its own transaction so one problem row cannot
    block the sweep.
    """
    now = timezone.now()
    stale = StockReservation.objects.filter(
        status=ReservationStatus.HELD, expires_at__lt=now
    ).values_list("pk", flat=True)[:limit]

    swept = 0
    for pk in list(stale):
        with transaction.atomic():
            reservation = (
                StockReservation.objects.select_for_update().select_related("lot").get(pk=pk)
            )
            if reservation.status != ReservationStatus.HELD:
                continue  # settled by someone else between the scan and now

            ItemLot.objects.filter(pk=reservation.lot_id).update(
                quantity_reserved=F("quantity_reserved") - reservation.quantity
            )
            reservation.status = ReservationStatus.EXPIRED
            reservation.settled_at = now
            reservation.save(update_fields=["status", "settled_at"])

            lot = ItemLot.objects.get(pk=reservation.lot_id)
            _write_ledger(
                lot=lot,
                quantity_delta=Decimal("0"),
                reserved_delta=-reservation.quantity,
                reason=LedgerReason.RELEASE,
                actor=None,
                reservation=reservation,
                reason_text="TTL expired; reclaimed by sweeper.",
            )
            swept += 1
    return swept


@transaction.atomic
def receive_stock(
    *,
    lot: ItemLot,
    quantity: Decimal,
    user,
    reason_text: str = "",
) -> ItemLot:
    """Book incoming goods against a lot."""
    quantity = Decimal(quantity)
    if quantity <= 0:
        raise ValueError("Received quantity must be positive.")

    lot = ItemLot.objects.select_for_update().get(pk=lot.pk)
    ItemLot.objects.filter(pk=lot.pk).update(
        quantity_on_hand=F("quantity_on_hand") + quantity
    )
    lot.refresh_from_db(fields=["quantity_on_hand", "quantity_reserved"])

    _write_ledger(
        lot=lot,
        quantity_delta=quantity,
        reserved_delta=Decimal("0"),
        reason=LedgerReason.RECEIPT,
        actor=user,
        reason_text=reason_text,
    )
    _resolve_reorder_alert(lot.item)
    return lot


@transaction.atomic
def adjust_stock(
    *, lot: ItemLot, new_quantity: Decimal, user, reason_text: str
) -> ItemLot:
    """
    Manual correction after a physical count.

    Restricted and always reasoned: an unexplained stock adjustment is the
    first thing an auditor asks about, so the reason is mandatory at both
    the service layer and the database (see the ledger CHECK constraint).
    """
    if not reason_text.strip():
        raise ValueError("Stock adjustments require a reason.")
    if not getattr(user, "can_adjust_inventory", False):
        raise PermissionDenied("This role may not adjust stock levels.")

    lot = ItemLot.objects.select_for_update().get(pk=lot.pk)
    delta = Decimal(new_quantity) - lot.quantity_on_hand

    with audit_context.reason("STOCK_ADJUSTMENT", reason_text):
        ItemLot.objects.filter(pk=lot.pk).update(quantity_on_hand=Decimal(new_quantity))
        lot.refresh_from_db(fields=["quantity_on_hand", "quantity_reserved"])
        _write_ledger(
            lot=lot,
            quantity_delta=delta,
            reserved_delta=Decimal("0"),
            reason=LedgerReason.ADJUSTMENT,
            actor=user,
            reason_text=reason_text,
        )

    check_reorder_threshold(lot.item)
    return lot


def available_quantity(item: InventoryItem, *, as_of: dt.date | None = None) -> Decimal:
    """Allocatable stock across all usable lots — excludes expired stock."""
    cutoff = as_of or timezone.now().date()
    rows = (
        ItemLot.objects.filter(item=item, status=LotStatus.AVAILABLE)
        .filter(Q(expires_on__isnull=True) | Q(expires_on__gte=cutoff))
        .values_list("quantity_on_hand", "quantity_reserved")
    )
    return sum((on_hand - reserved for on_hand, reserved in rows), Decimal("0"))


def check_reorder_threshold(item: InventoryItem) -> ReorderAlert | None:
    """
    Raise an alert when allocatable stock falls to or below the reorder
    point. The unique partial index on ReorderAlert keeps this idempotent,
    so calling it after every allocation costs nothing but the check.
    """
    available = available_quantity(item)
    if available > item.reorder_point:
        return None

    active = ReorderAlert.objects.filter(
        item=item, status__in=[ReorderAlert.Status.OPEN, ReorderAlert.Status.ORDERED]
    ).first()
    if active is not None:
        return active

    # Two concurrent allocations can both find no active alert and both try
    # to create one. The partial unique index settles it; the loser reads
    # back the winner's row rather than failing the allocation it was part of.
    try:
        with transaction.atomic():
            return ReorderAlert.objects.create(
                item=item,
                quantity_available_at_trigger=available,
                reorder_point_at_trigger=item.reorder_point,
                suggested_order_quantity=item.reorder_quantity,
            )
    except IntegrityError:
        return ReorderAlert.objects.filter(
            item=item, status__in=[ReorderAlert.Status.OPEN, ReorderAlert.Status.ORDERED]
        ).first()


def _resolve_reorder_alert(item: InventoryItem) -> None:
    """Close open alerts once stock is back above the reorder point."""
    if available_quantity(item) <= item.reorder_point:
        return
    ReorderAlert.objects.filter(
        item=item, status__in=[ReorderAlert.Status.OPEN, ReorderAlert.Status.ORDERED]
    ).update(status=ReorderAlert.Status.RESOLVED, resolved_at=timezone.now())


def _write_ledger(
    *,
    lot: ItemLot,
    quantity_delta: Decimal,
    reserved_delta: Decimal,
    reason: str,
    actor,
    reservation: StockReservation | None = None,
    reason_text: str = "",
) -> StockLedgerEntry:
    """
    Append a movement, capturing the resulting balance.

    Storing the balance alongside the delta means the ledger can be audited
    at any point without replaying every prior row, and lets a
    reconciliation job assert that the lot's cached quantities still match
    the ledger's running total.
    """
    return StockLedgerEntry.objects.create(
        lot=lot,
        quantity_delta=quantity_delta,
        reserved_delta=reserved_delta,
        reason=reason,
        reason_text=reason_text,
        reservation=reservation,
        balance_on_hand=lot.quantity_on_hand,
        balance_reserved=lot.quantity_reserved,
        actor=actor,
    )
