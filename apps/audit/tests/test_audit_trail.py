"""
Tests for the audit trail's three claims: it covers every writer, it is
append-only, and tampering is detectable.
"""

from decimal import Decimal

from django.db import ProgrammingError, connection, transaction
from django.test import TestCase, TransactionTestCase

from apps.audit import context as audit_context
from apps.audit.models import AuditEvent
from apps.samples import services
from apps.samples.models import Sample, SampleState
from apps.samples.tests.factories import make_sample, make_user


class AuditCoverageTests(TestCase):
    def setUp(self):
        self.user = make_user("auditee")

    def test_orm_writes_are_audited(self):
        sample = make_sample(self.user)
        events = AuditEvent.objects.filter(table_name="sample", record_pk=str(sample.pk))
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().action, "INSERT")

    def test_raw_sql_writes_are_audited_too(self):
        """
        The point of trigger-based auditing. A bulk UPDATE never touches
        model code, so middleware or signals would miss it entirely.
        """
        sample = make_sample(self.user, state=SampleState.RECEIVED)
        Sample.objects.filter(pk=sample.pk).update(state=SampleState.ACCESSIONED)

        events = AuditEvent.objects.filter(
            table_name="sample", record_pk=str(sample.pk), action="UPDATE"
        )
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().changed_fields, ["state"])

    def test_the_acting_user_is_recorded(self):
        audit_context.set_actor(self.user, request_id="req-abc")
        sample = make_sample(self.user, barcode="BC-ACTOR")

        event = AuditEvent.objects.get(table_name="sample", record_pk=str(sample.pk))
        self.assertEqual(event.actor_id, self.user.pk)
        self.assertEqual(event.actor_username, self.user.username)
        self.assertEqual(event.request_id, "req-abc")

    def test_reason_codes_reach_the_trail(self):
        manager = make_user("disposer", role="lab_manager")
        sample = make_sample(self.user, barcode="BC-REASON", state=SampleState.IN_STORAGE)

        services.transition(
            sample=sample, new_state=SampleState.DISPOSED, user=manager,
            reason_code="STUDY_CLOSED", reason_text="Study MUM-114 closed out.",
        )

        event = AuditEvent.objects.filter(
            table_name="sample", record_pk=str(sample.pk), action="UPDATE"
        ).latest("seq")
        self.assertEqual(event.reason_code, "STUDY_CLOSED")
        self.assertIn("MUM-114", event.reason_text)

    def test_previous_and_new_state_are_both_captured(self):
        sample = make_sample(self.user, barcode="BC-DIFF", state=SampleState.RECEIVED)
        services.transition(sample=sample, new_state=SampleState.ACCESSIONED, user=self.user)

        event = AuditEvent.objects.filter(
            table_name="sample", record_pk=str(sample.pk), action="UPDATE"
        ).latest("seq")
        self.assertEqual(event.previous_state["state"], "received")
        self.assertEqual(event.new_state["state"], "accessioned")

    def test_credentials_never_enter_the_audit_trail(self):
        """app_user is audited with the password column stripped."""
        user = make_user("secret_holder")
        event = AuditEvent.objects.filter(
            table_name="app_user", record_pk=str(user.pk)
        ).first()
        self.assertIsNotNone(event)
        self.assertNotIn("password", event.new_state)


class AppendOnlyTests(TestCase):
    def setUp(self):
        self.user = make_user("immutable")
        make_sample(self.user)

    # The trigger raises SQLSTATE 42501 (insufficient_privilege), which is
    # the honest code for "you are not allowed to do this" — as opposed to
    # a constraint violation, which would imply the data was malformed.
    # Django surfaces it as ProgrammingError.

    def test_audit_rows_cannot_be_updated(self):
        with self.assertRaises(ProgrammingError):
            with transaction.atomic():
                AuditEvent.objects.filter(table_name="sample").update(actor_username="forged")

    def test_audit_rows_cannot_be_deleted(self):
        with self.assertRaises(ProgrammingError):
            with transaction.atomic():
                AuditEvent.objects.filter(table_name="sample").delete()


class HashChainTests(TransactionTestCase):
    """
    TransactionTestCase because the tamper simulations run ALTER TABLE,
    which Postgres refuses inside a transaction that still has pending
    trigger events — exactly what TestCase's enclosing transaction leaves
    behind after each write.
    """

    reset_sequences = True

    def setUp(self):
        self.user = make_user("chainer")

    def _verify(self, stream="sample"):
        with connection.cursor() as cursor:
            cursor.execute("SELECT broken_seq FROM audit_verify_chain(%s)", [stream])
            return cursor.fetchall()

    def test_a_clean_chain_verifies(self):
        sample = make_sample(self.user)
        services.transition(sample=sample, new_state=SampleState.ACCESSIONED, user=self.user)
        services.transition(sample=sample, new_state=SampleState.IN_STORAGE, user=self.user)

        self.assertEqual(self._verify(), [])

    def test_each_row_links_to_its_predecessor(self):
        sample = make_sample(self.user)
        services.transition(sample=sample, new_state=SampleState.ACCESSIONED, user=self.user)

        events = list(AuditEvent.objects.filter(stream="sample").order_by("seq"))
        self.assertEqual(events[0].prev_hash, "0" * 64)
        for earlier, later in zip(events, events[1:]):
            self.assertEqual(later.prev_hash, earlier.row_hash)

    def test_tampering_with_a_historical_row_is_detected(self):
        """
        The whole point of chaining. The immutability trigger is disabled
        for this test specifically to simulate an attacker with database
        rights — otherwise we would only be re-testing the trigger.
        """
        sample = make_sample(self.user)
        services.transition(sample=sample, new_state=SampleState.ACCESSIONED, user=self.user)
        services.transition(sample=sample, new_state=SampleState.IN_STORAGE, user=self.user)
        self.assertEqual(self._verify(), [])

        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE audit_event DISABLE TRIGGER audit_event_immutable_trg")
            cursor.execute(
                "UPDATE audit_event SET actor_username = 'someone_else' "
                "WHERE stream = 'sample' AND seq = 2"
            )
            cursor.execute("ALTER TABLE audit_event ENABLE TRIGGER audit_event_immutable_trg")

        broken = self._verify()
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0][0], 2)

    def test_deleting_a_row_breaks_the_chain(self):
        sample = make_sample(self.user)
        services.transition(sample=sample, new_state=SampleState.ACCESSIONED, user=self.user)
        services.transition(sample=sample, new_state=SampleState.IN_STORAGE, user=self.user)

        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE audit_event DISABLE TRIGGER audit_event_immutable_trg")
            cursor.execute("DELETE FROM audit_event WHERE stream = 'sample' AND seq = 2")
            cursor.execute("ALTER TABLE audit_event ENABLE TRIGGER audit_event_immutable_trg")

        # Row 3's prev_hash no longer matches the row that now precedes it.
        broken = self._verify()
        self.assertEqual(len(broken), 1)

    def test_hashing_is_stable_across_session_timezones(self):
        """
        Regression test.

        `timestamptz::text` and `to_jsonb(row)` render timestamps using the
        session's TimeZone. Before both audit functions pinned UTC, a row
        written from an Asia/Kolkata session hashed differently than it read
        back under Django's UTC connection, and verification reported a
        perfectly intact chain as broken.

        A false tamper alarm is worse than a cosmetic bug: it starts an
        integrity incident over nothing, and it means the hash was never a
        stable function of the row's content.
        """
        sample = make_sample(self.user, barcode="BC-TZ")

        # Write more history from a connection in a different timezone.
        with connection.cursor() as cursor:
            cursor.execute("SET TimeZone = 'Asia/Kolkata'")
            cursor.execute(
                "UPDATE sample SET state = 'accessioned' WHERE id = %s", [str(sample.pk)]
            )

        # ...and verify from that same non-UTC session.
        with connection.cursor() as cursor:
            cursor.execute("SELECT broken_seq FROM audit_verify_chain('sample')")
            self.assertEqual(cursor.fetchall(), [])
            cursor.execute("SET TimeZone = 'UTC'")

        # ...and again from UTC. Both must agree.
        self.assertEqual(self._verify(), [])

    def test_streams_are_chained_independently(self):
        """
        Per-table chains are what keep audit writes from serialising
        globally. Verify one stream's integrity does not depend on another.
        """
        make_sample(self.user, barcode="BC-S1")
        self.assertEqual(self._verify("sample"), [])
        self.assertEqual(self._verify("app_user"), [])

        seqs = AuditEvent.objects.filter(stream="sample").values_list("seq", flat=True)
        self.assertEqual(sorted(seqs), list(range(1, len(seqs) + 1)))
