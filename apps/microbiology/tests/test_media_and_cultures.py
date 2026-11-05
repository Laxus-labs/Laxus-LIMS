"""
Tests for microbiology master data.

These encode the business rules from the Module 2 spec §5.5. If one of these
fails, a rule that a regulator would check has been broken.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.microbiology import services
from apps.microbiology.models import (
    CultureLot,
    MediaLot,
    MediaMaster,
    MediaQualification,
    Organization,
    OrganismMaster,
)

User = get_user_model()


class MicrobiologyTestCase(TestCase):
    """Shared setup: two tenants, a user, one organism and one medium.

    Two organisations exist in every test so that tenant isolation is
    exercised incidentally, not only in the tests that name it.
    """

    def setUp(self):
        self.org = Organization.objects.create(name="Laxus Labs")
        self.other_org = Organization.objects.create(name="Someone Else Ltd")
        self.user = User.objects.create_user(username="analyst1", password="x")

        self.organism = OrganismMaster.objects.create(
            organization=self.org,
            organism_code="ATCC-6538",
            organism_name="Staphylococcus aureus",
            organism_type=OrganismMaster.OrganismType.BACTERIA,
            reference_collection="ATCC",
            max_passage_number=5,
            created_by=self.user,
        )
        self.media = MediaMaster.objects.create(
            organization=self.org,
            media_code="TSA",
            media_name="Tryptone Soya Agar",
            media_type=MediaMaster.MediaType.AGAR,
            default_shelf_life_days=30,
            requires_growth_promotion=True,
            created_by=self.user,
        )

    # ------------------------------------------------------- helpers ----

    def make_lot(self, org=None, media=None, **overrides):
        fields = dict(qty_prepared=1000, unit="mL")
        fields.update(overrides)
        return services.create_media_lot(
            organization=org or self.org,
            media=media or self.media,
            prepared_by=self.user,
            user=self.user,
            **fields,
        )

    def make_culture(self, **overrides):
        fields = dict(
            received_date=timezone.localdate() - timedelta(days=10),
            expiry_review_date=timezone.localdate() + timedelta(days=365),
            passage_number=1,
        )
        fields.update(overrides)
        return services.create_culture_lot(
            organization=self.org,
            organism=self.organism,
            user=self.user,
            **fields,
        )

    def qualify(self, lot, result=None, **extra):
        return services.record_media_qualification(
            media_lot=lot,
            test_organism=self.organism,
            result=result or MediaQualification.Result.PASS,
            user=self.user,
            **extra,
        )


class PublicIdTests(MicrobiologyTestCase):
    def test_public_id_is_generated_and_sequential(self):
        first = self.make_lot()
        second = self.make_lot()

        year = timezone.localdate().year
        self.assertEqual(first.public_id, f"MED-{year}-000001")
        self.assertEqual(second.public_id, f"MED-{year}-000002")

    def test_sequences_are_isolated_per_organization(self):
        other_media = MediaMaster.objects.create(
            organization=self.other_org,
            media_code="TSA",
            media_name="Tryptone Soya Agar",
            media_type=MediaMaster.MediaType.AGAR,
            created_by=self.user,
        )
        self.make_lot()
        their_lot = self.make_lot(org=self.other_org, media=other_media)

        year = timezone.localdate().year
        # Each tenant starts its own count — their first lot is 000001, not
        # 000002. A lab must not be able to infer another tenant's volume.
        self.assertEqual(their_lot.public_id, f"MED-{year}-000001")


class MediaLotTests(MicrobiologyTestCase):
    def test_new_lot_requiring_growth_promotion_is_not_usable(self):
        lot = self.make_lot()
        self.assertEqual(lot.status, MediaLot.Status.PENDING_QUALIFICATION)
        self.assertFalse(lot.is_usable())

    def test_expiry_defaults_to_shelf_life(self):
        lot = self.make_lot()
        expected = timezone.localdate(lot.prepared_at) + timedelta(days=30)
        self.assertEqual(lot.expiry_date, expected)

    def test_custom_expiry_requires_a_reason(self):
        with self.assertRaises(ValidationError) as ctx:
            self.make_lot(expiry_date=timezone.localdate() + timedelta(days=90))
        self.assertIn("expiry_override_reason", ctx.exception.message_dict)

    def test_passing_growth_promotion_makes_lot_usable(self):
        lot = self.make_lot()
        self.qualify(lot, MediaQualification.Result.PASS)

        lot.refresh_from_db()
        self.assertEqual(lot.status, MediaLot.Status.QUALIFIED)
        self.assertTrue(lot.is_usable())

    def test_failed_growth_promotion_blocks_the_lot(self):
        """Spec §5.5: failed media is excluded from routine use."""
        lot = self.make_lot()
        self.qualify(
            lot, MediaQualification.Result.FAIL, reason_code="gp_test_failed"
        )

        lot.refresh_from_db()
        self.assertEqual(lot.status, MediaLot.Status.BLOCKED)
        self.assertFalse(lot.is_usable())
        self.assertNotIn(lot, services.usable_media_lots(self.org))

    def test_failed_qualification_requires_a_reason_code(self):
        lot = self.make_lot()
        with self.assertRaises(ValidationError) as ctx:
            self.qualify(lot, MediaQualification.Result.FAIL)
        self.assertIn("reason_code", ctx.exception.message_dict)

    def test_expired_lot_is_not_usable(self):
        lot = self.make_lot()
        self.qualify(lot)
        MediaLot.objects.filter(pk=lot.pk).update(
            expiry_date=timezone.localdate() - timedelta(days=1)
        )
        lot.refresh_from_db()

        self.assertFalse(lot.is_usable())
        self.assertNotIn(lot, services.usable_media_lots(self.org))

    def test_fully_consumed_lot_is_not_usable(self):
        lot = self.make_lot()
        self.qualify(lot)
        MediaLot.objects.filter(pk=lot.pk).update(qty_used=1000)
        lot.refresh_from_db()

        self.assertEqual(lot.qty_remaining, 0)
        self.assertFalse(lot.is_usable())
        self.assertNotIn(lot, services.usable_media_lots(self.org))

    def test_usable_lots_do_not_leak_across_tenants(self):
        lot = self.make_lot()
        self.qualify(lot)

        self.assertIn(lot, services.usable_media_lots(self.org))
        self.assertNotIn(lot, services.usable_media_lots(self.other_org))


class CultureLotTests(MicrobiologyTestCase):
    def test_culture_can_be_issued(self):
        lot = self.make_culture()
        movement = services.issue_culture(
            culture_lot=lot, issued_to=self.user, user=self.user
        )
        self.assertEqual(movement.passage_number_at_action, 1)
        self.assertEqual(lot.movements.count(), 1)

    def test_expired_culture_cannot_be_issued(self):
        lot = self.make_culture(
            expiry_review_date=timezone.localdate() + timedelta(days=1)
        )
        CultureLot.objects.filter(pk=lot.pk).update(
            expiry_review_date=timezone.localdate() - timedelta(days=1)
        )
        lot.refresh_from_db()

        with self.assertRaises(services.TransitionError) as ctx:
            services.issue_culture(
                culture_lot=lot, issued_to=self.user, user=self.user
            )
        self.assertIn("expiry", str(ctx.exception).lower())

    def test_culture_beyond_passage_limit_cannot_be_issued(self):
        """max_passage_number is 5; a culture at passage 6 is out of bounds."""
        lot = self.make_culture(passage_number=6)

        self.assertFalse(lot.is_usable())
        with self.assertRaises(services.TransitionError) as ctx:
            services.issue_culture(
                culture_lot=lot, issued_to=self.user, user=self.user
            )
        self.assertIn("passage", str(ctx.exception).lower())

    def test_disposal_requires_a_reason_code(self):
        lot = self.make_culture()
        with self.assertRaises(ValidationError):
            services.dispose_culture(
                culture_lot=lot, reason_code="", user=self.user
            )

    def test_disposal_sets_status_and_keeps_history(self):
        """No hard deletes (Global §4) — the record and its movements remain."""
        lot = self.make_culture()
        services.issue_culture(
            culture_lot=lot, issued_to=self.user, user=self.user
        )
        services.dispose_culture(
            culture_lot=lot, reason_code="expired", user=self.user
        )

        lot.refresh_from_db()
        self.assertEqual(lot.status, CultureLot.Status.DISPOSED)
        self.assertEqual(lot.disposal_reason_code, "expired")
        self.assertEqual(lot.movements.count(), 2)  # issue + dispose, retained
        self.assertTrue(CultureLot.objects.filter(pk=lot.pk).exists())

    def test_disposed_culture_cannot_be_issued(self):
        lot = self.make_culture()
        services.dispose_culture(
            culture_lot=lot, reason_code="contamination", user=self.user
        )
        lot.refresh_from_db()

        with self.assertRaises(services.TransitionError):
            services.issue_culture(
                culture_lot=lot, issued_to=self.user, user=self.user
            )

    def test_receipt_date_cannot_be_in_the_future(self):
        with self.assertRaises(ValidationError) as ctx:
            self.make_culture(
                received_date=timezone.localdate() + timedelta(days=1)
            )
        self.assertIn("received_date", ctx.exception.message_dict)
