"""Order placement/lifecycle, result entry/review/approval, OOS closure,
and COA generation/release."""

import datetime as dt
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.audit.errors import ReauthenticationFailed
from apps.inventory.models import ReservationStatus, StockReservation
from apps.inventory.tests.factories import make_item, make_lot
from apps.samples.models import SampleState
from apps.testing import services
from apps.testing.errors import (
    IllegalOrderTransition,
    IncompleteCertificate,
    PermissionDenied,
    ReasonRequired,
    ResultNotReady,
)
from apps.testing.models import (
    ALLOWED_ORDER_TRANSITIONS,
    CoaStatus,
    ResultStatus,
    TestConsumable,
    TestOrderStatus,
)

from .factories import make_order, make_sample, make_specification, make_test_definition, make_user


class OrderPlacementTests(TestCase):
    def setUp(self):
        self.user = make_user("orderer")
        self.sample = make_sample(self.user, state=SampleState.ACCESSIONED)
        self.test_def = make_test_definition()

    def test_place_order_defaults_due_at_from_turnaround(self):
        order = services.place_order(sample=self.sample, test_definition=self.test_def, user=self.user)
        self.assertIsNotNone(order.due_at)
        self.assertAlmostEqual(
            (order.due_at - order.requested_at).total_seconds(), 24 * 3600, delta=5
        )

    def test_place_order_reserves_consumables(self):
        item = make_item(sku="REAGENT-1")
        make_lot(item, quantity=100)
        TestConsumable.objects.create(test_definition=self.test_def, item=item, quantity_per_run=Decimal("2"))

        order = services.place_order(sample=self.sample, test_definition=self.test_def, user=self.user)
        reservation = StockReservation.objects.get(purpose_ref=f"test_order:{order.pk}")
        self.assertEqual(reservation.quantity, Decimal("2"))
        self.assertEqual(reservation.status, ReservationStatus.HELD)

    def test_place_order_fails_when_reagent_unavailable(self):
        item = make_item(sku="SCARCE-1")
        make_lot(item, quantity=1)
        TestConsumable.objects.create(test_definition=self.test_def, item=item, quantity_per_run=Decimal("5"))

        from apps.inventory.errors import InsufficientStock

        with self.assertRaises(InsufficientStock):
            services.place_order(sample=self.sample, test_definition=self.test_def, user=self.user)
        self.assertEqual(self.sample.test_orders.count(), 0)


class OrderLifecycleTests(TestCase):
    def setUp(self):
        self.user = make_user("bench")
        self.sample = make_sample(self.user, state=SampleState.ACCESSIONED)
        self.test_def = make_test_definition()
        self.order = make_order(self.sample, self.test_def, self.user)

    def test_python_and_database_order_state_machines_agree(self):
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT from_status, to_status FROM test_order_status_transition")
            in_db = set(cursor.fetchall())

        in_python = {
            (str(src), str(dst)) for src, targets in ALLOWED_ORDER_TRANSITIONS.items() for dst in targets
        }
        self.assertEqual(in_python, in_db)

    def test_start_then_cancel_releases_reservation(self):
        item = make_item(sku="REAGENT-2")
        make_lot(item, quantity=10)
        TestConsumable.objects.create(test_definition=self.test_def, item=item, quantity_per_run=Decimal("1"))
        order = services.place_order(sample=self.sample, test_definition=self.test_def, user=self.user)

        services.start_order(order=order, user=self.user)
        order.refresh_from_db()
        self.assertEqual(order.status, TestOrderStatus.IN_PROGRESS)

        services.cancel_order(order=order, user=self.user, reason_text="Wrong test ordered.")
        order.refresh_from_db()
        self.assertEqual(order.status, TestOrderStatus.CANCELLED)
        reservation = StockReservation.objects.get(purpose_ref=f"test_order:{order.pk}")
        self.assertEqual(reservation.status, ReservationStatus.RELEASED)

    def test_cancel_requires_a_reason(self):
        with self.assertRaises(ReasonRequired):
            services.cancel_order(order=self.order, user=self.user, reason_text="")

    def test_illegal_order_transition_is_refused(self):
        services.cancel_order(order=self.order, user=self.user, reason_text="Not needed.")
        with self.assertRaises(IllegalOrderTransition):
            services.start_order(order=self.order, user=self.user)

    def test_reassign(self):
        analyst2 = make_user("analyst2")
        order = services.reassign_order(order=self.order, assigned_to=analyst2, user=self.user)
        self.assertEqual(order.assigned_to, analyst2)


class ResultEntryTests(TestCase):
    def setUp(self):
        self.author = make_user("author")
        self.reviewer = make_user("reviewer", role="reviewer")
        self.qa = make_user("qa", role="qa_approver")
        self.sample = make_sample(self.author, state=SampleState.ACCESSIONED, sample_type="serum")
        self.test_def = make_test_definition()
        make_specification(self.test_def, self.author, sample_type="serum", lower=70, upper=110)
        self.order = make_order(self.sample, self.test_def, self.author)

    def test_enter_result_flags_out_of_spec_and_completes_order(self):
        result = services.enter_result(order=self.order, user=self.author, numeric_value=Decimal("150"))
        self.assertTrue(result.is_out_of_spec)
        self.assertEqual(result.specification.sample_type, "serum")

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, TestOrderStatus.COMPLETE)
        self.assertIsNotNone(self.order.completed_at)

    def test_enter_result_within_spec(self):
        result = services.enter_result(order=self.order, user=self.author, numeric_value=Decimal("90"))
        self.assertFalse(result.is_out_of_spec)

    def test_cannot_enter_result_twice(self):
        services.enter_result(order=self.order, user=self.author, numeric_value=Decimal("90"))
        with self.assertRaises(ResultNotReady):
            services.enter_result(order=self.order, user=self.author, numeric_value=Decimal("95"))

    def test_review_requires_a_different_person_and_capability(self):
        result = services.enter_result(order=self.order, user=self.author, numeric_value=Decimal("90"))
        services.submit_result(result=result, user=self.author)

        with self.assertRaises(PermissionDenied):
            services.review_result(result=result, user=self.author, password="pw")

        result = services.review_result(result=result, user=self.reviewer, password="pw")
        self.assertEqual(result.status, ResultStatus.REVIEWED)
        self.assertEqual(result.reviewed_by, self.reviewer)

    def test_review_fails_with_wrong_password(self):
        result = services.enter_result(order=self.order, user=self.author, numeric_value=Decimal("90"))
        services.submit_result(result=result, user=self.author)
        with self.assertRaises(ReauthenticationFailed):
            services.review_result(result=result, user=self.reviewer, password="wrong-password")

    def test_full_review_approve_flow(self):
        result = services.enter_result(order=self.order, user=self.author, numeric_value=Decimal("90"))
        services.submit_result(result=result, user=self.author)
        result = services.review_result(result=result, user=self.reviewer, password="pw")

        with self.assertRaises(PermissionDenied):
            services.approve_result(result=result, user=self.author, password="pw")

        result = services.approve_result(result=result, user=self.qa, password="pw")
        self.assertEqual(result.status, ResultStatus.APPROVED)
        self.assertEqual(result.approved_by, self.qa)

    def test_close_oos_requires_resolution_and_capability(self):
        result = services.enter_result(order=self.order, user=self.author, numeric_value=Decimal("150"))
        self.assertTrue(result.is_out_of_spec)

        with self.assertRaises(ReasonRequired):
            services.close_oos(result=result, user=self.qa, password="pw", resolution_text="")
        with self.assertRaises(PermissionDenied):
            services.close_oos(
                result=result, user=self.author, password="pw", resolution_text="Retested, confirmed high."
            )

        result = services.close_oos(
            result=result, user=self.qa, password="pw", resolution_text="Retested, confirmed high."
        )
        self.assertIsNotNone(result.oos_closed_at)
        self.assertEqual(result.oos_closed_by, self.qa)


class CoaTests(TestCase):
    def setUp(self):
        self.author = make_user("coa_author")
        self.qa = make_user("coa_qa", role="qa_approver")
        self.sample = make_sample(self.author, state=SampleState.ACCESSIONED, sample_type="serum")
        self.test_def = make_test_definition()
        self.order = make_order(self.sample, self.test_def, self.author)

    def _approved_result(self, value=Decimal("90")):
        result = services.enter_result(order=self.order, user=self.author, numeric_value=value)
        services.submit_result(result=result, user=self.author)
        reviewer = make_user("coa_reviewer", role="reviewer")
        result = services.review_result(result=result, user=reviewer, password="pw")
        return services.approve_result(result=result, user=self.qa, password="pw")

    def test_generate_coa_refuses_when_incomplete(self):
        with self.assertRaises(IncompleteCertificate):
            services.generate_coa(sample=self.sample, user=self.author)

    def test_generate_and_release_coa(self):
        self._approved_result()
        coa = services.generate_coa(sample=self.sample, user=self.author)
        self.assertEqual(coa.status, CoaStatus.DRAFT)
        self.assertEqual(len(coa.results_snapshot), 1)
        self.assertTrue(coa.coa_number.startswith("COA-"))

        released = services.release_coa(coa=coa, user=self.qa, password="pw")
        self.assertEqual(released.status, CoaStatus.RELEASED)
        self.sample.refresh_from_db()
        self.assertIsNotNone(self.sample.actual_completion_date)

    def test_reissue_supersedes_previous_release(self):
        self._approved_result()
        first = services.generate_coa(sample=self.sample, user=self.author)
        services.release_coa(coa=first, user=self.qa, password="pw")

        second = services.generate_coa(sample=self.sample, user=self.author)
        services.release_coa(coa=second, user=self.qa, password="pw")

        first.refresh_from_db()
        self.assertEqual(first.status, CoaStatus.SUPERSEDED)
        self.assertEqual(first.superseded_by_id, second.pk)


class TatReportingTests(TestCase):
    def setUp(self):
        self.user = make_user("tat_user")
        self.sample = make_sample(self.user, state=SampleState.ACCESSIONED)
        self.test_def = make_test_definition(turnaround_hours=1)

    def test_overdue_orders(self):
        order = make_order(
            self.sample, self.test_def, self.user, due_at=timezone.now() - dt.timedelta(hours=1)
        )
        self.assertIn(order, list(services.overdue_orders()))

    def test_tat_summary_computes_average_and_compliance(self):
        order = make_order(self.sample, self.test_def, self.user, due_at=timezone.now() + dt.timedelta(hours=5))
        order.status = TestOrderStatus.IN_PROGRESS
        order.save(update_fields=["status"])
        order.status = TestOrderStatus.COMPLETE
        order.completed_at = timezone.now()
        order.save(update_fields=["status", "completed_at"])

        summary = services.tat_summary()
        self.assertEqual(summary["total_completed"], 1)
        self.assertEqual(summary["tat_compliance_pct"], 100.0)
