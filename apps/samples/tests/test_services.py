"""Accessioning, custody transfer, aliquoting, and lifecycle enforcement."""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.samples import services
from apps.samples.errors import (
    IllegalTransition,
    InsufficientVolume,
    InvalidPosition,
    PermissionDenied,
    PositionOccupied,
    ReasonRequired,
    RejectionReasonRequired,
)
from apps.samples.models import (
    ALLOWED_TRANSITIONS,
    AcceptanceDecision,
    Sample,
    SamplePlacement,
    SampleState,
)

from .factories import make_customer, make_hierarchy, make_sample, make_user


class BatchAccessioningTests(TestCase):
    def setUp(self):
        self.user = make_user("accessioner")

    def test_ingests_a_batch_of_scans(self):
        scans = [{"barcode": f"BC-{i:03d}", "sample_type": "serum", "volume": 500} for i in range(5)]
        result = services.accession_batch(scans=scans, user=self.user)

        self.assertEqual(len(result.created), 5)
        self.assertEqual(len(result.duplicates), 0)
        self.assertEqual(Sample.objects.count(), 5)

    def test_duplicate_scans_within_one_payload_are_collapsed(self):
        """A scanner that double-fires must not create two samples."""
        scans = [
            {"barcode": "BC-001", "sample_type": "serum"},
            {"barcode": "BC-001", "sample_type": "serum"},
            {"barcode": "BC-002", "sample_type": "serum"},
        ]
        result = services.accession_batch(scans=scans, user=self.user)

        self.assertEqual(len(result.created), 2)
        self.assertEqual(len(result.duplicates), 1)
        self.assertEqual(Sample.objects.count(), 2)

    def test_resubmitting_the_whole_batch_creates_nothing_new(self):
        """The retry case: a client times out and sends the rack again."""
        scans = [{"barcode": f"BC-{i:03d}", "sample_type": "serum"} for i in range(10)]
        services.accession_batch(scans=scans, user=self.user)

        second = services.accession_batch(scans=scans, user=self.user)

        self.assertEqual(len(second.created), 0)
        self.assertEqual(len(second.duplicates), 10)
        self.assertEqual(Sample.objects.count(), 10)

    def test_a_duplicate_does_not_abort_the_rest_of_the_batch(self):
        """One re-scanned tube must not force a 96-well rack to be redone."""
        services.accession_batch(
            scans=[{"barcode": "BC-EXISTING", "sample_type": "serum"}], user=self.user
        )

        result = services.accession_batch(
            scans=[
                {"barcode": "BC-NEW-1", "sample_type": "serum"},
                {"barcode": "BC-EXISTING", "sample_type": "serum"},
                {"barcode": "BC-NEW-2", "sample_type": "serum"},
            ],
            user=self.user,
        )

        self.assertEqual(len(result.created), 2)
        self.assertEqual(len(result.duplicates), 1)
        self.assertEqual(result.total_scanned, 3)

    def test_accession_numbers_are_unique_and_sequential(self):
        scans = [{"barcode": f"BC-{i}", "sample_type": "serum"} for i in range(3)]
        created = services.accession_batch(scans=scans, user=self.user).created

        numbers = [services.accession(sample=s, user=self.user).accession_number for s in created]
        self.assertEqual(len(set(numbers)), 3)
        self.assertTrue(all(n.startswith("ACC-") for n in numbers))


class TransferTests(TestCase):
    def setUp(self):
        self.user = make_user("mover")
        self.loc = make_hierarchy()
        self.sample = make_sample(self.user, state=SampleState.IN_STORAGE)

    def test_placing_a_sample_opens_a_placement(self):
        placement = services.transfer(
            sample=self.sample, location=self.loc["box"], user=self.user,
            row_label="C", column_number=7,
        )
        self.assertEqual(placement.row_label, "C")
        self.assertIsNone(placement.removed_at)

    def test_moving_closes_the_previous_placement(self):
        first = services.transfer(
            sample=self.sample, location=self.loc["box"], user=self.user,
            row_label="A", column_number=1,
        )
        services.transfer(
            sample=self.sample, location=self.loc["box"], user=self.user,
            row_label="B", column_number=2,
        )

        first.refresh_from_db()
        self.assertIsNotNone(first.removed_at)
        self.assertEqual(
            SamplePlacement.objects.filter(sample=self.sample, removed_at__isnull=True).count(), 1
        )

    def test_two_samples_cannot_share_a_position(self):
        other = make_sample(self.user, barcode="BC-2", state=SampleState.IN_STORAGE)
        services.transfer(
            sample=self.sample, location=self.loc["box"], user=self.user,
            row_label="D", column_number=4,
        )

        with self.assertRaises(PositionOccupied):
            services.transfer(
                sample=other, location=self.loc["box"], user=self.user,
                row_label="D", column_number=4,
            )

    def test_vacated_positions_become_reusable(self):
        other = make_sample(self.user, barcode="BC-3", state=SampleState.IN_STORAGE)
        services.transfer(
            sample=self.sample, location=self.loc["box"], user=self.user,
            row_label="E", column_number=5,
        )
        services.transfer(
            sample=self.sample, location=self.loc["freezer"], user=self.user
        )

        placement = services.transfer(
            sample=other, location=self.loc["box"], user=self.user,
            row_label="E", column_number=5,
        )
        self.assertIsNotNone(placement.pk)

    def test_position_outside_the_grid_is_rejected(self):
        with self.assertRaises(InvalidPosition):
            services.transfer(
                sample=self.sample, location=self.loc["box"], user=self.user,
                row_label="Z", column_number=99,
            )

    def test_a_box_requires_a_coordinate(self):
        with self.assertRaises(InvalidPosition):
            services.transfer(sample=self.sample, location=self.loc["box"], user=self.user)

    def test_disposed_samples_cannot_be_moved(self):
        manager = make_user("manager", role="lab_manager")
        services.transition(
            sample=self.sample, new_state=SampleState.DISPOSED, user=manager,
            reason_code="STUDY_CLOSED",
        )
        with self.assertRaises(IllegalTransition):
            services.transfer(
                sample=self.sample, location=self.loc["box"], user=self.user,
                row_label="A", column_number=1,
            )


class AliquotTests(TestCase):
    def setUp(self):
        self.user = make_user("aliquoter")
        self.parent = make_sample(self.user, state=SampleState.IN_STORAGE, volume=1000)

    def test_creates_children_and_draws_volume_from_the_parent(self):
        children = services.aliquot(
            parent=self.parent, count=4, volume_each=Decimal("100"), user=self.user
        )

        self.parent.refresh_from_db()
        self.assertEqual(len(children), 4)
        self.assertEqual(self.parent.volume, Decimal("600"))
        self.assertEqual(self.parent.state, SampleState.ALIQUOTED)
        self.assertTrue(all(c.volume == Decimal("100") for c in children))

    def test_children_inherit_subject_and_record_lineage(self):
        self.parent.subject_ref = "SUBJ-7f3a"
        self.parent.save(update_fields=["subject_ref"])

        children = services.aliquot(
            parent=self.parent, count=2, volume_each=Decimal("50"), user=self.user
        )

        for child in children:
            self.assertEqual(child.subject_ref, "SUBJ-7f3a")
            self.assertEqual(child.parent_id, self.parent.pk)
            self.assertEqual(child.root_id, self.parent.pk)
            self.assertEqual(child.generation, 1)

    def test_aliquoting_an_aliquot_keeps_the_original_root(self):
        child = services.aliquot(
            parent=self.parent, count=1, volume_each=Decimal("400"), user=self.user
        )[0]
        grandchild = services.aliquot(
            parent=child, count=1, volume_each=Decimal("100"), user=self.user
        )[0]

        self.assertEqual(grandchild.generation, 2)
        self.assertEqual(grandchild.root_id, self.parent.pk)
        self.assertEqual(services.lineage(grandchild).count(), 3)

    def test_cannot_draw_more_volume_than_remains(self):
        with self.assertRaises(InsufficientVolume):
            services.aliquot(
                parent=self.parent, count=11, volume_each=Decimal("100"), user=self.user
            )

        self.parent.refresh_from_db()
        self.assertEqual(self.parent.volume, Decimal("1000"))
        self.assertEqual(Sample.objects.filter(parent=self.parent).count(), 0)

    def test_fully_drawn_parent_becomes_exhausted_not_aliquoted(self):
        services.aliquot(
            parent=self.parent, count=10, volume_each=Decimal("100"), user=self.user
        )
        self.parent.refresh_from_db()
        self.assertEqual(self.parent.volume, Decimal("0"))
        self.assertEqual(self.parent.state, SampleState.EXHAUSTED)

    def test_volume_can_never_go_negative(self):
        """The DB constraint backing the volume accounting."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Sample.objects.filter(pk=self.parent.pk).update(volume=Decimal("-1"))


class LifecycleTests(TestCase):
    def setUp(self):
        self.user = make_user("lifer")
        self.manager = make_user("boss", role="lab_manager")
        self.sample = make_sample(self.user)

    def test_legal_path_through_the_lifecycle(self):
        services.transition(sample=self.sample, new_state=SampleState.ACCESSIONED, user=self.user)
        services.transition(sample=self.sample, new_state=SampleState.IN_STORAGE, user=self.user)
        services.transition(sample=self.sample, new_state=SampleState.IN_TESTING, user=self.user)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.state, SampleState.IN_TESTING)

    def test_illegal_transition_is_refused_by_the_service(self):
        with self.assertRaises(IllegalTransition):
            services.transition(
                sample=self.sample, new_state=SampleState.DISPOSED, user=self.manager,
                reason_code="X",
            )

    def test_illegal_transition_is_also_refused_by_the_database(self):
        """
        The service check is a courtesy; this is the guarantee. A bulk
        update bypasses every line of Python in the service layer.
        """
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Sample.objects.filter(pk=self.sample.pk).update(state=SampleState.DISPOSED)

    def test_disposal_requires_a_reason(self):
        services.transition(sample=self.sample, new_state=SampleState.ACCESSIONED, user=self.user)
        services.transition(sample=self.sample, new_state=SampleState.IN_STORAGE, user=self.user)

        with self.assertRaises(ReasonRequired):
            services.transition(
                sample=self.sample, new_state=SampleState.DISPOSED, user=self.manager
            )

    def test_disposal_requires_the_right_role(self):
        services.transition(sample=self.sample, new_state=SampleState.ACCESSIONED, user=self.user)
        services.transition(sample=self.sample, new_state=SampleState.IN_STORAGE, user=self.user)

        with self.assertRaises(PermissionDenied):
            services.transition(
                sample=self.sample, new_state=SampleState.DISPOSED, user=self.user,
                reason_code="STUDY_CLOSED",
            )

    def test_disposal_closes_the_open_placement(self):
        loc = make_hierarchy()
        services.transition(sample=self.sample, new_state=SampleState.ACCESSIONED, user=self.user)
        services.transition(sample=self.sample, new_state=SampleState.IN_STORAGE, user=self.user)
        services.transfer(
            sample=self.sample, location=loc["box"], user=self.user,
            row_label="A", column_number=1,
        )

        services.transition(
            sample=self.sample, new_state=SampleState.DISPOSED, user=self.manager,
            reason_code="STUDY_CLOSED", reason_text="Study MUM-114 closed out.",
        )

        self.assertEqual(
            SamplePlacement.objects.filter(sample=self.sample, removed_at__isnull=True).count(), 0
        )

    def test_python_and_database_state_machines_agree(self):
        """
        ALLOWED_TRANSITIONS is projected into sample_state_transition by a
        migration. If someone edits the dict without a migration, this
        fails rather than letting the two definitions drift apart.
        """
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT from_state, to_state FROM sample_state_transition")
            in_db = set(cursor.fetchall())

        in_python = {
            (str(src), str(dst))
            for src, targets in ALLOWED_TRANSITIONS.items()
            for dst in targets
        }
        self.assertEqual(in_python, in_db)


class AcceptanceReviewTests(TestCase):
    def setUp(self):
        self.analyst = make_user("frontdesk", role="analyst")
        self.qa = make_user("qa1", role="qa_approver")
        self.customer = make_customer(self.analyst)

    def test_accept_advances_to_accessioned_and_records_checklist(self):
        sample = make_sample(
            self.analyst, barcode="BC-ACPT-1", state=SampleState.RECEIVED, customer=self.customer
        )
        result = services.accept_sample(
            sample=sample,
            user=self.analyst,
            checklist={"container_integrity_ok": True, "label_ok": True},
        )
        self.assertEqual(result.state, SampleState.ACCESSIONED)
        self.assertEqual(result.acceptance_decision, AcceptanceDecision.ACCEPTED)
        self.assertTrue(result.container_integrity_ok)
        self.assertTrue(result.label_ok)
        self.assertIsNone(result.seal_integrity_ok)
        self.assertIsNotNone(result.accession_number)

    def test_accept_with_deviation_requires_notes(self):
        sample = make_sample(self.analyst, barcode="BC-DEV-1", state=SampleState.RECEIVED)
        with self.assertRaises(ReasonRequired):
            services.accept_sample(sample=sample, user=self.analyst, with_deviation=True)

        result = services.accept_sample(
            sample=sample, user=self.analyst, with_deviation=True, deviation_notes="Cap loose."
        )
        self.assertEqual(result.acceptance_decision, AcceptanceDecision.ACCEPTED_WITH_DEVIATION)
        self.assertEqual(result.deviation_notes, "Cap loose.")

    def test_put_on_hold_requires_reason_and_can_later_be_accepted(self):
        sample = make_sample(self.analyst, barcode="BC-HOLD-1", state=SampleState.RECEIVED)
        with self.assertRaises(ReasonRequired):
            services.put_on_hold(sample=sample, user=self.analyst, reason_code="")

        held = services.put_on_hold(
            sample=sample, user=self.analyst, reason_code="AWAITING_DOCS"
        )
        self.assertEqual(held.state, SampleState.ON_HOLD)
        self.assertEqual(held.acceptance_decision, AcceptanceDecision.ON_HOLD)

        accepted = services.accept_sample(sample=held, user=self.analyst)
        self.assertEqual(accepted.state, SampleState.ACCESSIONED)

    def test_reject_requires_qa_capability(self):
        sample = make_sample(self.analyst, barcode="BC-REJ-1", state=SampleState.RECEIVED)
        with self.assertRaises(PermissionDenied):
            services.reject_sample(
                sample=sample, user=self.analyst, rejection_reason_code="R001"
            )

    def test_reject_requires_known_reason_code(self):
        sample = make_sample(self.analyst, barcode="BC-REJ-2", state=SampleState.RECEIVED)
        with self.assertRaises(RejectionReasonRequired):
            services.reject_sample(
                sample=sample, user=self.qa, rejection_reason_code="NOT_A_CODE"
            )

    def test_reject_other_requires_details(self):
        sample = make_sample(self.analyst, barcode="BC-REJ-3", state=SampleState.RECEIVED)
        with self.assertRaises(RejectionReasonRequired):
            services.reject_sample(sample=sample, user=self.qa, rejection_reason_code="OTHER")

    def test_reject_sets_qa_approval_reason_and_closes_placement(self):
        box = make_hierarchy()["box"]
        sample = make_sample(self.analyst, barcode="BC-REJ-4", state=SampleState.RECEIVED)
        services.transfer(sample=sample, location=box, user=self.analyst, row_label="A", column_number=1)

        result = services.reject_sample(
            sample=sample,
            user=self.qa,
            rejection_reason_code="R001",
            checklist={"container_integrity_ok": False},
        )
        self.assertEqual(result.state, SampleState.REJECTED)
        self.assertEqual(result.rejection_reason_id, "R001")
        self.assertEqual(result.qa_approved_by, self.qa)
        self.assertIsNotNone(result.qa_approved_at)
        self.assertFalse(result.container_integrity_ok)
        self.assertEqual(
            SamplePlacement.objects.filter(sample=sample, removed_at__isnull=True).count(), 0
        )

    def test_rejected_sample_can_then_be_disposed(self):
        manager = make_user("manager2", role="lab_manager")
        sample = make_sample(self.analyst, barcode="BC-REJ-5", state=SampleState.RECEIVED)
        services.reject_sample(sample=sample, user=self.qa, rejection_reason_code="R004")

        disposed = services.transition(
            sample=sample,
            new_state=SampleState.DISPOSED,
            user=manager,
            reason_code="REJECTED_SAMPLE_DISPOSAL",
        )
        self.assertEqual(disposed.state, SampleState.DISPOSED)

    def test_mark_client_notified(self):
        sample = make_sample(self.analyst, barcode="BC-REJ-6", state=SampleState.RECEIVED)
        services.reject_sample(sample=sample, user=self.qa, rejection_reason_code="R001")
        self.assertIsNone(Sample.objects.get(pk=sample.pk).client_notified_at)

        notified = services.mark_client_notified(sample=sample, user=self.qa)
        self.assertIsNotNone(notified.client_notified_at)


class StorageQueryTests(TestCase):
    def setUp(self):
        self.user = make_user("browser")
        self.loc = make_hierarchy()

    def test_contents_of_a_freezer_spans_nested_containers(self):
        """The ltree subtree query the materialised path exists to serve."""
        in_box = make_sample(self.user, barcode="IN-BOX", state=SampleState.IN_STORAGE)
        on_freezer = make_sample(self.user, barcode="ON-FRZ", state=SampleState.IN_STORAGE)
        elsewhere = make_sample(self.user, barcode="OTHER", state=SampleState.IN_STORAGE)

        other_freezer = self.loc["freezer"].__class__.objects.create(
            code="frz_09", name="Freezer 9", location_type="freezer", parent=self.loc["room"]
        )
        services.transfer(
            sample=in_box, location=self.loc["box"], user=self.user,
            row_label="A", column_number=1,
        )
        services.transfer(sample=on_freezer, location=self.loc["freezer"], user=self.user)
        services.transfer(sample=elsewhere, location=other_freezer, user=self.user)

        found = set(services.contents_of(self.loc["freezer"]).values_list("barcode", flat=True))
        self.assertEqual(found, {"IN-BOX", "ON-FRZ"})

    def test_contents_can_be_restricted_to_one_level(self):
        in_box = make_sample(self.user, barcode="IN-BOX", state=SampleState.IN_STORAGE)
        services.transfer(
            sample=in_box, location=self.loc["box"], user=self.user,
            row_label="A", column_number=1,
        )

        direct = services.contents_of(self.loc["freezer"], include_descendants=False)
        self.assertEqual(direct.count(), 0)
