"""
Test ordering, result entry/review/approval, OOS closure, and COA
generation/release.

Same shape as apps/samples/services.py and apps/inventory/services.py:
every mutation is a named domain operation, not a CRUD write, and every
regulated sign-off (review, approval, COA release, OOS closure) goes
through apps.audit.signatures.sign() so re-authentication and record
hashing are uniform across the whole system rather than reimplemented per
app.

Reagent consumption ties into apps.inventory: placing an order reserves the
consumables a TestDefinition declares (TestConsumable), and entering a
result consumes that reservation — the stock actually left the shelf the
moment the test was run, not when someone later files paperwork about it.
"""

from __future__ import annotations

import datetime as dt

from django.db import transaction
from django.utils import timezone

from apps.audit import context as audit_context
from apps.audit import signatures as audit_signatures
from apps.audit.models import ElectronicSignature
from apps.inventory import services as inventory_services
from apps.inventory.errors import InsufficientStock
from apps.inventory.models import ReservationStatus, StockReservation

from .errors import (
    IllegalOrderTransition,
    IncompleteCertificate,
    PermissionDenied,
    ReasonRequired,
    ResultNotReady,
)
from .models import (
    ALLOWED_ORDER_TRANSITIONS,
    CertificateOfAnalysis,
    CoaStatus,
    ResultStatus,
    Specification,
    TestConsumable,
    TestOrder,
    TestOrderStatus,
    TestResult,
)


def _purpose_ref(order: TestOrder) -> str:
    return f"test_order:{order.pk}"


def _assert_order_transition(order: TestOrder, new_status: str) -> None:
    if new_status not in ALLOWED_ORDER_TRANSITIONS.get(order.status, set()):
        raise IllegalOrderTransition(order.pk, order.status, new_status)


@transaction.atomic
def place_order(
    *,
    sample,
    test_definition,
    user,
    priority: int = 5,
    due_at=None,
    assigned_to=None,
    reason_code: str = "TEST_ORDER_PLACED",
) -> TestOrder:
    """
    Request a test against a sample, reserving whatever reagents the test
    consumes in the same transaction — an order that cannot be stocked is
    not created at all, rather than sitting in the queue as a promise the
    lab cannot keep.
    """
    due_at = due_at or (timezone.now() + dt.timedelta(hours=test_definition.turnaround_hours))

    with audit_context.reason(reason_code):
        order = TestOrder.objects.create(
            sample=sample,
            test_definition=test_definition,
            priority=priority,
            due_at=due_at,
            assigned_to=assigned_to,
            requested_by=user,
        )

        for consumable in TestConsumable.objects.filter(test_definition=test_definition):
            inventory_services.reserve(
                item=consumable.item,
                quantity=consumable.quantity_per_run,
                user=user,
                idempotency_key=f"{_purpose_ref(order)}:{consumable.item_id}",
                purpose_ref=_purpose_ref(order),
            )

    return order


@transaction.atomic
def reassign_order(*, order: TestOrder, assigned_to, user) -> TestOrder:
    if order.status in {TestOrderStatus.COMPLETE, TestOrderStatus.CANCELLED}:
        raise IllegalOrderTransition(order.pk, order.status, "reassigned")
    order.assigned_to = assigned_to
    order.save(update_fields=["assigned_to"])
    return order


@transaction.atomic
def start_order(*, order: TestOrder, user, reason_code: str = "TEST_STARTED") -> TestOrder:
    order = TestOrder.objects.select_for_update().get(pk=order.pk)
    _assert_order_transition(order, TestOrderStatus.IN_PROGRESS)
    with audit_context.reason(reason_code):
        order.status = TestOrderStatus.IN_PROGRESS
        order.save(update_fields=["status"])
    return order


@transaction.atomic
def cancel_order(*, order: TestOrder, user, reason_text: str) -> TestOrder:
    """Cancel an order and release whatever stock it was holding — the run
    is not happening, so the reagents are not spoken for any more."""
    if not reason_text.strip():
        raise ReasonRequired("Cancelling a test order requires a reason.")

    order = TestOrder.objects.select_for_update().get(pk=order.pk)
    _assert_order_transition(order, TestOrderStatus.CANCELLED)

    with audit_context.reason("TEST_ORDER_CANCELLED", reason_text):
        order.status = TestOrderStatus.CANCELLED
        order.cancellation_reason = reason_text
        order.save(update_fields=["status", "cancellation_reason"])

        held = StockReservation.objects.filter(
            purpose_ref=_purpose_ref(order), status=ReservationStatus.HELD
        )
        for reservation in held:
            inventory_services.release(reservation, user=user, reason_text="Test order cancelled.")

    return order


def _active_specification(test_definition, sample_type: str) -> Specification | None:
    """The spec in force for this test, preferring one scoped to the
    sample's exact type over a blank (applies-to-everything) one."""
    qs = Specification.objects.filter(test_definition=test_definition, superseded_at__isnull=True)
    return (
        qs.filter(sample_type=sample_type).first()
        or qs.filter(sample_type="").first()
    )


@transaction.atomic
def enter_result(
    *,
    order: TestOrder,
    user,
    numeric_value=None,
    text_value: str = "",
    instrument_ref: str = "",
    comments: str = "",
    reason_code: str = "RESULT_ENTRY",
) -> TestResult:
    """
    Record a result, consume the reagents the order reserved, and mark the
    order's work complete. Result *review and approval* are separate steps
    (review_result/approve_result) — entering a result is "the bench work
    is done", not "the lab stands behind this number".
    """
    order = TestOrder.objects.select_for_update().get(pk=order.pk)
    if order.status == TestOrderStatus.CANCELLED:
        raise IllegalOrderTransition(order.pk, order.status, "result entry")
    if hasattr(order, "result"):
        raise ResultNotReady(f"Test order {order.pk} already has a result.")

    spec = _active_specification(order.test_definition, order.sample.sample_type)
    is_out_of_spec = bool(spec) and numeric_value is not None and not spec.evaluate(numeric_value)

    with audit_context.reason(reason_code):
        result = TestResult.objects.create(
            order=order,
            specification=spec,
            numeric_value=numeric_value,
            text_value=text_value,
            is_out_of_spec=is_out_of_spec,
            entered_by=user,
            instrument_ref=instrument_ref,
            comments=comments,
        )

        for reservation in StockReservation.objects.filter(
            purpose_ref=_purpose_ref(order), status=ReservationStatus.HELD
        ):
            inventory_services.consume(reservation, user=user, reason_text=f"Consumed by order {order.pk}.")

        # Multi-hop but each individual UPDATE is a legal single-step
        # transition, so the DB trigger validates each hop rather than the
        # jump as a whole — see ALLOWED_ORDER_TRANSITIONS.
        if order.status in {TestOrderStatus.REQUESTED, TestOrderStatus.SCHEDULED}:
            order.status = TestOrderStatus.IN_PROGRESS
            order.save(update_fields=["status"])
        order.status = TestOrderStatus.COMPLETE
        order.completed_at = timezone.now()
        order.save(update_fields=["status", "completed_at"])

    return result


def submit_result(*, result: TestResult, user) -> TestResult:
    if result.status != ResultStatus.DRAFT:
        raise ResultNotReady(f"Result {result.pk} is {result.status}, not draft.")
    result.status = ResultStatus.SUBMITTED
    result.save(update_fields=["status", "updated_at"])
    return result


@transaction.atomic
def review_result(*, result: TestResult, user, password: str, reason_text: str = "") -> TestResult:
    result = TestResult.objects.select_for_update().get(pk=result.pk)
    if not getattr(user, "can_review_results", False):
        raise PermissionDenied("This role may not review results.")
    if result.status != ResultStatus.SUBMITTED:
        raise ResultNotReady(f"Result {result.pk} is {result.status}, not submitted.")
    if _entered_by(result, user):
        raise PermissionDenied("A result cannot be reviewed by the analyst who entered it.")

    audit_signatures.sign(
        signer=user,
        password=password,
        meaning=ElectronicSignature.Meaning.REVIEWED,
        table_name="test_result",
        record_pk=result.pk,
        record_state=_result_snapshot(result),
    )
    with audit_context.reason("RESULT_REVIEWED", reason_text):
        result.status = ResultStatus.REVIEWED
        result.reviewed_by = user
        result.reviewed_at = timezone.now()
        result.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    return result


@transaction.atomic
def approve_result(*, result: TestResult, user, password: str, reason_text: str = "") -> TestResult:
    result = TestResult.objects.select_for_update().get(pk=result.pk)
    if not getattr(user, "can_approve_results", False):
        raise PermissionDenied("This role may not approve results.")
    if result.status != ResultStatus.REVIEWED:
        raise ResultNotReady(f"Result {result.pk} is {result.status}, not reviewed.")
    if _entered_by(result, user):
        raise PermissionDenied("A result cannot be approved by the analyst who entered it.")

    audit_signatures.sign(
        signer=user,
        password=password,
        meaning=ElectronicSignature.Meaning.APPROVED,
        table_name="test_result",
        record_pk=result.pk,
        record_state=_result_snapshot(result),
    )
    with audit_context.reason("RESULT_APPROVED", reason_text):
        result.status = ResultStatus.APPROVED
        result.approved_by = user
        result.approved_at = timezone.now()
        result.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    return result


@transaction.atomic
def reject_result(*, result: TestResult, user, reason_text: str) -> TestResult:
    """Send a result back — a reviewer or approver found a problem with it
    that entering it again, not editing it, has to fix."""
    if not reason_text.strip():
        raise ReasonRequired("Rejecting a result requires a reason.")
    if not getattr(user, "can_review_results", False):
        raise PermissionDenied("This role may not reject results.")
    if result.status not in {ResultStatus.SUBMITTED, ResultStatus.REVIEWED}:
        raise ResultNotReady(f"Result {result.pk} is {result.status}, not reviewable.")

    with audit_context.reason("RESULT_REJECTED", reason_text):
        result.status = ResultStatus.REJECTED
        result.save(update_fields=["status", "updated_at"])
    return result


@transaction.atomic
def close_oos(*, result: TestResult, user, password: str, resolution_text: str) -> TestResult:
    """Disposition an out-of-specification result: investigated, and here
    is why it is being closed out. Distinct from approving the result —
    see the OOS-closure fields' docstring on TestResult."""
    result = TestResult.objects.select_for_update().get(pk=result.pk)
    if not result.is_out_of_spec:
        raise ResultNotReady(f"Result {result.pk} is not out of specification.")
    if not resolution_text.strip():
        raise ReasonRequired("Closing an OOS result requires a resolution.")
    if not getattr(user, "can_approve_results", False):
        raise PermissionDenied("This role may not close OOS investigations.")

    audit_signatures.sign(
        signer=user,
        password=password,
        meaning=ElectronicSignature.Meaning.OOS_CLOSED,
        table_name="test_result",
        record_pk=result.pk,
        record_state=_result_snapshot(result) | {"oos_resolution": resolution_text},
    )
    with audit_context.reason("OOS_CLOSED", resolution_text):
        result.oos_closed_by = user
        result.oos_closed_at = timezone.now()
        result.oos_resolution = resolution_text
        result.save(update_fields=["oos_closed_by", "oos_closed_at", "oos_resolution", "updated_at"])
    return result


# --- certificates of analysis -----------------------------------------------

def _next_coa_number(prefix: str = "COA") -> str:
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("CREATE SEQUENCE IF NOT EXISTS coa_number_seq")
        cursor.execute("SELECT nextval('coa_number_seq')")
        value = cursor.fetchone()[0]
    return f"{prefix}-{timezone.now():%Y}-{value:06d}"


def _result_snapshot(result: TestResult) -> dict:
    return {
        "order_id": result.order_id,
        "test_code": result.order.test_definition.code,
        "test_name": result.order.test_definition.name,
        "numeric_value": result.numeric_value,
        "text_value": result.text_value,
        "unit": result.order.test_definition.unit,
        "is_out_of_spec": result.is_out_of_spec,
        "specification_id": result.specification_id,
        "entered_by": result.entered_by.get_username(),
        "reviewed_by": result.reviewed_by.get_username() if result.reviewed_by else None,
        "approved_by": result.approved_by.get_username() if result.approved_by else None,
        "approved_at": result.approved_at,
        "instrument_ref": result.instrument_ref,
    }


@transaction.atomic
def generate_coa(*, sample, user, reason_code: str = "COA_GENERATED") -> CertificateOfAnalysis:
    """
    Snapshot every approved result for a sample into a draft certificate.

    Refuses if any non-cancelled order for the sample lacks an approved
    result — a COA is a claim that testing is done, and generating one
    while work is still outstanding would make that claim false.
    """
    orders = list(
        sample.test_orders.exclude(status=TestOrderStatus.CANCELLED).select_related(
            "result", "test_definition"
        )
    )
    if not orders:
        raise IncompleteCertificate(f"Sample {sample.pk} has no test orders to certify.")

    snapshot = []
    for order in orders:
        result = getattr(order, "result", None)
        if result is None or result.status != ResultStatus.APPROVED:
            raise IncompleteCertificate(
                f"Order {order.pk} ({order.test_definition.code}) has no approved result yet."
            )
        snapshot.append(_result_snapshot(result))

    with audit_context.reason(reason_code):
        coa = CertificateOfAnalysis.objects.create(
            coa_number=_next_coa_number(),
            sample=sample,
            results_snapshot=snapshot,
            generated_by=user,
        )
    return coa


@transaction.atomic
def release_coa(*, coa: CertificateOfAnalysis, user, password: str, reason_text: str = "") -> CertificateOfAnalysis:
    """Sign and release a certificate, superseding any certificate already
    released for this sample, and record the sample's actual COA date."""
    coa = CertificateOfAnalysis.objects.select_for_update().get(pk=coa.pk)
    if coa.status != CoaStatus.DRAFT:
        raise IncompleteCertificate(f"Certificate {coa.pk} is {coa.status}, not draft.")
    if not getattr(user, "can_approve_results", False):
        raise PermissionDenied("This role may not release certificates of analysis.")

    audit_signatures.sign(
        signer=user,
        password=password,
        meaning=ElectronicSignature.Meaning.COA_RELEASED,
        table_name="certificate_of_analysis",
        record_pk=coa.pk,
        record_state={"coa_number": coa.coa_number, "sample_id": coa.sample_id, "results": coa.results_snapshot},
    )

    with audit_context.reason("COA_RELEASED", reason_text):
        now = timezone.now()
        previous = CertificateOfAnalysis.objects.filter(
            sample=coa.sample, status=CoaStatus.RELEASED
        ).first()
        if previous:
            previous.status = CoaStatus.SUPERSEDED
            previous.superseded_at = now
            previous.superseded_by = coa
            previous.save(update_fields=["status", "superseded_at", "superseded_by"])

        coa.status = CoaStatus.RELEASED
        coa.released_by = user
        coa.released_at = now
        coa.save(update_fields=["status", "released_by", "released_at"])

        type(coa.sample).objects.filter(pk=coa.sample_id).update(actual_completion_date=now)

    return coa


def _entered_by(result: TestResult, user) -> bool:
    return result.entered_by_id == getattr(user, "pk", None)


def overdue_orders():
    return TestOrder.objects.filter(
        status__in=[TestOrderStatus.REQUESTED, TestOrderStatus.SCHEDULED, TestOrderStatus.IN_PROGRESS],
        due_at__lt=timezone.now(),
    ).order_by("due_at")


def due_soon_orders(hours: int = 24):
    now = timezone.now()
    horizon = now + dt.timedelta(hours=hours)
    return TestOrder.objects.filter(
        status__in=[TestOrderStatus.REQUESTED, TestOrderStatus.SCHEDULED, TestOrderStatus.IN_PROGRESS],
        due_at__gte=now,
        due_at__lte=horizon,
    ).order_by("due_at")


def tat_summary(*, start=None, end=None) -> dict:
    """Average turnaround and on-time compliance over completed orders in
    a window — the KPI the spec calls for, computed rather than stored so
    it always reflects the current data."""
    qs = TestOrder.objects.filter(status=TestOrderStatus.COMPLETE, completed_at__isnull=False)
    if start:
        qs = qs.filter(completed_at__gte=start)
    if end:
        qs = qs.filter(completed_at__lte=end)

    orders = list(qs.values("requested_at", "completed_at", "due_at"))
    total = len(orders)
    if total == 0:
        return {"total_completed": 0, "average_tat_hours": None, "tat_compliance_pct": None}

    total_hours = sum(
        (o["completed_at"] - o["requested_at"]).total_seconds() / 3600 for o in orders
    )
    with_due = [o for o in orders if o["due_at"] is not None]
    on_time = sum(1 for o in with_due if o["completed_at"] <= o["due_at"])

    return {
        "total_completed": total,
        "average_tat_hours": round(total_hours / total, 2),
        "tat_compliance_pct": round(100 * on_time / len(with_due), 2) if with_due else None,
    }
