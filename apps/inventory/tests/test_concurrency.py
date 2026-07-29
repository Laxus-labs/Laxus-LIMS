"""
Concurrency tests for the reservation engine.

These use TransactionTestCase, not TestCase, and the distinction is the
whole point: TestCase wraps each test in a transaction that is rolled back,
so every "thread" would share one connection and nothing would actually
race. TransactionTestCase commits for real, letting each thread hold its
own connection and contend on real row locks.

Each thread closes its connection in a finally block. Django opens a
connection per thread and does not clean them up for you; leaking them
exhausts the pool and turns a passing suite into a flaky one.
"""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import threading

from django.db import connection, connections
from django.test import TransactionTestCase

from apps.inventory import services
from apps.inventory.errors import InsufficientStock
from apps.inventory.models import ItemLot, ReservationStatus, StockReservation

from .factories import make_item, make_lot, make_user


class ReservationConcurrencyTests(TransactionTestCase):
    # Every thread runs its own transaction, so the test DB must be reset
    # between tests rather than rolled back.
    reset_sequences = True

    def _run_concurrently(self, fn, count):
        """
        Fire `count` calls to `fn` as simultaneously as the GIL allows.

        The barrier is what makes this a real test: without it, threads
        drift apart and the first few finish before the rest start, so the
        contention window never opens and the test passes for the wrong
        reason.
        """
        barrier = threading.Barrier(count)

        def wrapped(i):
            try:
                barrier.wait(timeout=30)
                return fn(i)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(wrapped, range(count)))

    def test_fifty_threads_cannot_oversell_five_units(self):
        """
        The headline case: 50 concurrent requests for 1 unit each, against
        a lot holding exactly 5. Exactly 5 must succeed.
        """
        user = make_user("racer")
        item = make_item()
        make_lot(item, quantity=5)

        def attempt(i):
            try:
                services.reserve(
                    item=item,
                    quantity=Decimal("1"),
                    user=user,
                    idempotency_key=f"race-{i}",
                )
                return "reserved"
            except InsufficientStock:
                return "rejected"

        outcomes = self._run_concurrently(attempt, 50)

        self.assertEqual(outcomes.count("reserved"), 5)
        self.assertEqual(outcomes.count("rejected"), 45)

        lot = ItemLot.objects.get(item=item)
        self.assertEqual(lot.quantity_reserved, Decimal("5"))
        self.assertEqual(lot.quantity_on_hand, Decimal("5"))
        # The invariant, stated directly.
        self.assertGreaterEqual(lot.quantity_on_hand - lot.quantity_reserved, Decimal("0"))
        self.assertEqual(
            StockReservation.objects.filter(status=ReservationStatus.HELD).count(), 5
        )

    def test_concurrent_uneven_requests_never_go_negative(self):
        """
        Mixed request sizes against limited stock. The exact winners depend
        on scheduling — which is fine — but the invariant must hold for
        every interleaving.
        """
        user = make_user("racer2")
        item = make_item(sku="TIPS-200")
        make_lot(item, quantity=20)

        sizes = [1, 2, 3, 5, 8] * 6  # 114 units of demand against 20 available

        def attempt(i):
            try:
                allocs = services.reserve(
                    item=item,
                    quantity=Decimal(sizes[i]),
                    user=user,
                    idempotency_key=f"uneven-{i}",
                )
                return sum(a.quantity for a in allocs)
            except InsufficientStock:
                return Decimal("0")

        granted = sum(self._run_concurrently(attempt, len(sizes)))

        lot = ItemLot.objects.get(item=item)
        self.assertEqual(lot.quantity_reserved, granted)
        self.assertLessEqual(granted, Decimal("20"))
        self.assertGreaterEqual(lot.quantity_on_hand - lot.quantity_reserved, Decimal("0"))

    def test_concurrent_multi_lot_allocation_does_not_deadlock(self):
        """
        Multi-lot requests lock rows in FEFO order, identically for every
        caller, so contenders queue rather than deadlock. If the ordering
        were nondeterministic this test would hang or raise a deadlock
        error instead of completing.
        """
        user = make_user("racer3")
        item = make_item(sku="MM-MULTI")
        make_lot(item, lot_number="A", quantity=10, expires_in_days=10)
        make_lot(item, lot_number="B", quantity=10, expires_in_days=20)
        make_lot(item, lot_number="C", quantity=10, expires_in_days=30)

        def attempt(i):
            try:
                allocs = services.reserve(
                    item=item,
                    quantity=Decimal("7"),  # forces spanning lots
                    user=user,
                    idempotency_key=f"multi-{i}",
                )
                return sum(a.quantity for a in allocs)
            except InsufficientStock:
                return Decimal("0")

        granted = sum(self._run_concurrently(attempt, 12))

        total_reserved = sum(l.quantity_reserved for l in ItemLot.objects.filter(item=item))
        self.assertEqual(granted, total_reserved)
        self.assertLessEqual(total_reserved, Decimal("30"))
        for lot in ItemLot.objects.filter(item=item):
            self.assertGreaterEqual(
                lot.quantity_on_hand - lot.quantity_reserved, Decimal("0")
            )

    def test_retrying_the_same_idempotency_key_does_not_double_book(self):
        """
        A client that times out and retries must not consume stock twice.
        Ten concurrent retries of one logical request reserve one unit.
        """
        user = make_user("retrier")
        item = make_item(sku="IDEM-1")
        make_lot(item, quantity=10)

        def attempt(i):
            try:
                services.reserve(
                    item=item,
                    quantity=Decimal("1"),
                    user=user,
                    idempotency_key="same-key-every-time",
                )
                return "ok"
            except Exception as exc:  # noqa: BLE001 - surfaced in the assertion below
                return f"error:{type(exc).__name__}"

        outcomes = self._run_concurrently(attempt, 10)

        lot = ItemLot.objects.get(item=item)
        self.assertEqual(
            lot.quantity_reserved,
            Decimal("1"),
            f"idempotency failed; outcomes were {outcomes}",
        )
