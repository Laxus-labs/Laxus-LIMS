"""FEFO selection, reservation lifecycle, expiry sweeping, reorder alerts."""

import datetime as dt
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.inventory import services
from apps.inventory.errors import InsufficientStock, ReservationNotHeld
from apps.inventory.models import (
    ItemLot,
    LotStatus,
    ReorderAlert,
    ReservationStatus,
    StockLedgerEntry,
)

from .factories import make_item, make_lot, make_user


class FEFOSelectionTests(TestCase):
    def setUp(self):
        self.user = make_user("picker")
        self.item = make_item()

    def test_picks_the_earliest_expiring_lot_first(self):
        late = make_lot(self.item, lot_number="LATE", quantity=10, expires_in_days=180)
        early = make_lot(self.item, lot_number="EARLY", quantity=10, expires_in_days=7)
        make_lot(self.item, lot_number="MID", quantity=10, expires_in_days=60)

        allocs = services.reserve(
            item=self.item, quantity=Decimal("5"), user=self.user, idempotency_key="k1"
        )

        self.assertEqual(len(allocs), 1)
        self.assertEqual(allocs[0].lot.pk, early.pk)
        late.refresh_from_db()
        self.assertEqual(late.quantity_reserved, Decimal("0"))

    def test_spans_lots_in_expiry_order_when_one_is_not_enough(self):
        early = make_lot(self.item, lot_number="EARLY", quantity=4, expires_in_days=7)
        mid = make_lot(self.item, lot_number="MID", quantity=10, expires_in_days=60)

        allocs = services.reserve(
            item=self.item, quantity=Decimal("9"), user=self.user, idempotency_key="k2"
        )

        self.assertEqual([a.lot.pk for a in allocs], [early.pk, mid.pk])
        self.assertEqual([a.quantity for a in allocs], [Decimal("4"), Decimal("5")])

    def test_non_expiring_stock_sorts_last(self):
        """
        A lot with no expiry must be used *after* dated stock, not first.
        Getting the NULL ordering backwards silently wastes perishable
        reagent, which is the exact failure FEFO exists to prevent.
        """
        forever = make_lot(self.item, lot_number="FOREVER", quantity=10, expires_in_days=None)
        dated = make_lot(self.item, lot_number="DATED", quantity=10, expires_in_days=200)

        allocs = services.reserve(
            item=self.item, quantity=Decimal("3"), user=self.user, idempotency_key="k3"
        )

        self.assertEqual(allocs[0].lot.pk, dated.pk)
        forever.refresh_from_db()
        self.assertEqual(forever.quantity_reserved, Decimal("0"))

    def test_already_expired_stock_is_not_allocatable(self):
        make_lot(self.item, lot_number="STALE", quantity=50, expires_in_days=-1)

        with self.assertRaises(InsufficientStock):
            services.reserve(
                item=self.item, quantity=Decimal("1"), user=self.user, idempotency_key="k4"
            )

    def test_stock_expiring_before_it_is_needed_is_excluded(self):
        """Reserving for work three weeks out must skip a lot expiring in one."""
        make_lot(self.item, lot_number="SOON", quantity=10, expires_in_days=7)
        good = make_lot(self.item, lot_number="GOOD", quantity=10, expires_in_days=90)

        allocs = services.reserve(
            item=self.item,
            quantity=Decimal("5"),
            user=self.user,
            idempotency_key="k5",
            needed_until=timezone.now().date() + dt.timedelta(days=21),
        )

        self.assertEqual(allocs[0].lot.pk, good.pk)

    def test_quarantined_lots_are_not_allocatable(self):
        make_lot(self.item, lot_number="QC", quantity=50, status=LotStatus.QUARANTINED)

        with self.assertRaises(InsufficientStock):
            services.reserve(
                item=self.item, quantity=Decimal("1"), user=self.user, idempotency_key="k6"
            )

    def test_failed_reservation_leaves_no_partial_holds(self):
        """A request that cannot be filled must roll back cleanly."""
        lot = make_lot(self.item, quantity=3)

        with self.assertRaises(InsufficientStock):
            services.reserve(
                item=self.item, quantity=Decimal("10"), user=self.user, idempotency_key="k7"
            )

        lot.refresh_from_db()
        self.assertEqual(lot.quantity_reserved, Decimal("0"))
        self.assertEqual(StockLedgerEntry.objects.count(), 0)


class ReservationLifecycleTests(TestCase):
    def setUp(self):
        self.user = make_user("lifecycle")
        self.item = make_item()
        self.lot = make_lot(self.item, quantity=10)

    def test_consume_decrements_both_on_hand_and_reserved(self):
        allocs = services.reserve(
            item=self.item, quantity=Decimal("4"), user=self.user, idempotency_key="c1"
        )
        services.consume(allocs[0].reservation, user=self.user)

        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_on_hand, Decimal("6"))
        self.assertEqual(self.lot.quantity_reserved, Decimal("0"))

    def test_release_returns_the_hold_without_consuming(self):
        allocs = services.reserve(
            item=self.item, quantity=Decimal("4"), user=self.user, idempotency_key="r1"
        )
        services.release(allocs[0].reservation, user=self.user)

        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_on_hand, Decimal("10"))
        self.assertEqual(self.lot.quantity_reserved, Decimal("0"))

    def test_a_reservation_cannot_be_settled_twice(self):
        allocs = services.reserve(
            item=self.item, quantity=Decimal("1"), user=self.user, idempotency_key="d1"
        )
        services.consume(allocs[0].reservation, user=self.user)

        with self.assertRaises(ReservationNotHeld):
            services.consume(allocs[0].reservation, user=self.user)
        with self.assertRaises(ReservationNotHeld):
            services.release(allocs[0].reservation, user=self.user)

    def test_depleting_a_lot_marks_it_depleted(self):
        allocs = services.reserve(
            item=self.item, quantity=Decimal("10"), user=self.user, idempotency_key="dep"
        )
        services.consume(allocs[0].reservation, user=self.user)

        self.lot.refresh_from_db()
        self.assertEqual(self.lot.status, LotStatus.DEPLETED)

    def test_expired_holds_are_reclaimed_by_the_sweeper(self):
        """A worker that dies mid-run must not strand stock forever."""
        allocs = services.reserve(
            item=self.item,
            quantity=Decimal("6"),
            user=self.user,
            idempotency_key="ttl",
            ttl_seconds=-1,  # already lapsed
        )

        swept = services.sweep_expired()

        self.assertEqual(swept, 1)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_reserved, Decimal("0"))
        allocs[0].reservation.refresh_from_db()
        self.assertEqual(allocs[0].reservation.status, ReservationStatus.EXPIRED)

    def test_ledger_balances_reconcile_with_the_lot(self):
        """
        The lot's quantities are a cache of the ledger. If they can drift,
        the ledger is decorative.
        """
        a = services.reserve(
            item=self.item, quantity=Decimal("3"), user=self.user, idempotency_key="led1"
        )
        services.consume(a[0].reservation, user=self.user)
        b = services.reserve(
            item=self.item, quantity=Decimal("2"), user=self.user, idempotency_key="led2"
        )
        services.release(b[0].reservation, user=self.user)

        self.lot.refresh_from_db()
        entries = StockLedgerEntry.objects.filter(lot=self.lot).order_by("id")
        self.assertEqual(
            sum(e.quantity_delta for e in entries), self.lot.quantity_on_hand - Decimal("10")
        )
        self.assertEqual(sum(e.reserved_delta for e in entries), self.lot.quantity_reserved)
        self.assertEqual(entries.last().balance_on_hand, self.lot.quantity_on_hand)


class ReorderAlertTests(TestCase):
    def setUp(self):
        self.user = make_user("reorder")
        self.item = make_item(reorder_point=5, reorder_quantity=100)
        self.lot = make_lot(self.item, quantity=10)

    def test_alert_raised_when_available_drops_to_the_reorder_point(self):
        services.reserve(
            item=self.item, quantity=Decimal("5"), user=self.user, idempotency_key="ro1"
        )
        alert = ReorderAlert.objects.get(item=self.item)
        self.assertEqual(alert.status, ReorderAlert.Status.OPEN)
        self.assertEqual(alert.suggested_order_quantity, Decimal("100"))

    def test_no_alert_while_stock_is_comfortable(self):
        services.reserve(
            item=self.item, quantity=Decimal("1"), user=self.user, idempotency_key="ro2"
        )
        self.assertFalse(ReorderAlert.objects.exists())

    def test_repeated_allocations_do_not_pile_up_duplicate_alerts(self):
        for i in range(5):
            services.reserve(
                item=self.item, quantity=Decimal("1"), user=self.user, idempotency_key=f"ro3-{i}"
            )
        self.assertEqual(ReorderAlert.objects.filter(item=self.item).count(), 1)

    def test_receiving_stock_resolves_the_alert(self):
        services.reserve(
            item=self.item, quantity=Decimal("6"), user=self.user, idempotency_key="ro4"
        )
        self.assertTrue(
            ReorderAlert.objects.filter(item=self.item, status=ReorderAlert.Status.OPEN).exists()
        )

        services.receive_stock(lot=self.lot, quantity=Decimal("50"), user=self.user)

        alert = ReorderAlert.objects.get(item=self.item)
        self.assertEqual(alert.status, ReorderAlert.Status.RESOLVED)
        self.assertIsNotNone(alert.resolved_at)


class StockAdjustmentTests(TestCase):
    def setUp(self):
        self.item = make_item()
        self.lot = make_lot(self.item, quantity=10)

    def test_adjustment_requires_the_right_role(self):
        analyst = make_user("analyst_user", role="analyst")
        with self.assertRaises(services.PermissionDenied):
            services.adjust_stock(
                lot=self.lot, new_quantity=Decimal("8"), user=analyst, reason_text="count"
            )

    def test_adjustment_requires_a_reason(self):
        manager = make_user("manager_user", role="lab_manager")
        with self.assertRaises(ValueError):
            services.adjust_stock(
                lot=self.lot, new_quantity=Decimal("8"), user=manager, reason_text="   "
            )

    def test_adjustment_records_the_delta_and_reason(self):
        manager = make_user("manager2", role="lab_manager")
        services.adjust_stock(
            lot=self.lot,
            new_quantity=Decimal("7"),
            user=manager,
            reason_text="Physical count 2026-07-28: three vials broken in transit.",
        )

        self.lot.refresh_from_db()
        self.assertEqual(self.lot.quantity_on_hand, Decimal("7"))
        entry = StockLedgerEntry.objects.get(lot=self.lot)
        self.assertEqual(entry.quantity_delta, Decimal("-3"))
        self.assertIn("broken in transit", entry.reason_text)
