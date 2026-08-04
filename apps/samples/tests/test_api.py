from rest_framework.test import APITestCase

from apps.samples.models import SampleState, SampleType
from apps.samples.tests.factories import make_hierarchy, make_sample, make_user


class SampleAPITests(APITestCase):
    def setUp(self):
        self.user = make_user("analyst1", role="analyst")
        self.client.force_authenticate(user=self.user)

    def test_accession_batch_is_idempotent(self):
        payload = {"scans": [{"barcode": "BC-BATCH-1", "sample_type": SampleType.SERUM}]}
        first = self.client.post("/api/samples/accession-batch/", payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(len(first.data["created"]), 1)
        self.assertEqual(len(first.data["duplicates"]), 0)

        second = self.client.post("/api/samples/accession-batch/", payload, format="json")
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(len(second.data["created"]), 0)
        self.assertEqual(len(second.data["duplicates"]), 1)

    def test_accession_advances_state_and_assigns_accession_number(self):
        sample = make_sample(self.user, barcode="BC-ACC-1", state=SampleState.RECEIVED)
        resp = self.client.post(f"/api/samples/{sample.pk}/accession/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["state"], SampleState.ACCESSIONED)
        self.assertIsNotNone(resp.data["accession_number"])

    def test_transfer_places_sample_then_conflicts_on_the_same_position(self):
        box = make_hierarchy()["box"]
        sample1 = make_sample(self.user, barcode="BC-T1")
        sample2 = make_sample(self.user, barcode="BC-T2")
        payload = {"location_id": box.pk, "row_label": "A", "column_number": 1}

        first = self.client.post(f"/api/samples/{sample1.pk}/transfer/", payload, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["location"]["id"], box.pk)

        second = self.client.post(f"/api/samples/{sample2.pk}/transfer/", payload, format="json")
        self.assertEqual(second.status_code, 409, second.data)

    def test_aliquot_splits_volume_then_rejects_insufficient_volume(self):
        sample = make_sample(
            self.user, barcode="BC-AL-1", state=SampleState.ACCESSIONED, volume=100
        )
        resp = self.client.post(
            f"/api/samples/{sample.pk}/aliquot/",
            {"count": 2, "volume_each": "10"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(len(resp.data), 2)

        resp2 = self.client.post(
            f"/api/samples/{sample.pk}/aliquot/",
            {"count": 100, "volume_each": "10"},
            format="json",
        )
        self.assertEqual(resp2.status_code, 400, resp2.data)

    def test_transition_requires_reason_for_disposal(self):
        sample = make_sample(self.user, barcode="BC-DR-1", state=SampleState.IN_STORAGE)
        resp = self.client.post(
            f"/api/samples/{sample.pk}/transition/",
            {"new_state": SampleState.DISPOSED},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_transition_rejects_illegal_state_change(self):
        sample = make_sample(self.user, barcode="BC-IL-1", state=SampleState.RECEIVED)
        resp = self.client.post(
            f"/api/samples/{sample.pk}/transition/",
            {"new_state": SampleState.DISPOSED, "reason_code": "X"},
            format="json",
        )
        self.assertEqual(resp.status_code, 409, resp.data)

    def test_transition_dispose_requires_permission(self):
        sample = make_sample(self.user, barcode="BC-PD-1", state=SampleState.IN_STORAGE)
        resp = self.client.post(
            f"/api/samples/{sample.pk}/transition/",
            {"new_state": SampleState.DISPOSED, "reason_code": "STUDY_CLOSED"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403, resp.data)

    def test_lab_manager_can_dispose_with_reason(self):
        manager = make_user("manager1", role="lab_manager")
        self.client.force_authenticate(user=manager)
        sample = make_sample(manager, barcode="BC-DISP-1", state=SampleState.IN_STORAGE)
        resp = self.client.post(
            f"/api/samples/{sample.pk}/transition/",
            {"new_state": SampleState.DISPOSED, "reason_code": "STUDY_CLOSED"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["state"], SampleState.DISPOSED)

    def test_lineage_and_placements_endpoints(self):
        parent = make_sample(
            self.user, barcode="BC-LIN-1", state=SampleState.ACCESSIONED, volume=50
        )
        self.client.post(
            f"/api/samples/{parent.pk}/aliquot/",
            {"count": 1, "volume_each": "10"},
            format="json",
        )
        lineage_resp = self.client.get(f"/api/samples/{parent.pk}/lineage/")
        self.assertEqual(lineage_resp.status_code, 200)
        self.assertEqual(len(lineage_resp.data), 2)

        box = make_hierarchy()["box"]
        self.client.post(
            f"/api/samples/{parent.pk}/transfer/",
            {"location_id": box.pk, "row_label": "B", "column_number": 2},
            format="json",
        )
        placements_resp = self.client.get(f"/api/samples/{parent.pk}/placements/")
        self.assertEqual(placements_resp.status_code, 200)
        self.assertEqual(len(placements_resp.data), 1)

    def test_expiring_and_search(self):
        from django.utils import timezone
        import datetime

        make_sample(
            self.user,
            barcode="BC-EXP-1",
            state=SampleState.ACCESSIONED,
            expires_at=timezone.now() + datetime.timedelta(days=5),
        )

        resp = self.client.get("/api/samples/expiring/?days=30")
        self.assertEqual(resp.status_code, 200)
        barcodes = [s["barcode"] for s in resp.data]
        self.assertIn("BC-EXP-1", barcodes)

        search_resp = self.client.get("/api/samples/?search=BC-EXP-1")
        self.assertEqual(search_resp.status_code, 200)
        self.assertEqual(len(search_resp.data["results"]), 1)


class LocationAndMeAPITests(APITestCase):
    def setUp(self):
        self.user = make_user("analyst2", role="analyst")
        self.client.force_authenticate(user=self.user)

    def test_me_reports_role_and_capabilities(self):
        resp = self.client.get("/api/me/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["username"], "analyst2")
        self.assertFalse(resp.data["can_dispose_samples"])

    def test_location_list_and_contents(self):
        hierarchy = make_hierarchy()
        box = hierarchy["box"]
        sample = make_sample(self.user, barcode="BC-LOC-1")
        self.client.post(
            f"/api/samples/{sample.pk}/transfer/",
            {"location_id": box.pk, "row_label": "C", "column_number": 3},
            format="json",
        )

        list_resp = self.client.get("/api/locations/")
        self.assertEqual(list_resp.status_code, 200)
        self.assertGreaterEqual(len(list_resp.data), 4)

        contents_resp = self.client.get(f"/api/locations/{box.pk}/contents/")
        self.assertEqual(contents_resp.status_code, 200)
        self.assertEqual(len(contents_resp.data), 1)
        self.assertEqual(contents_resp.data[0]["barcode"], "BC-LOC-1")
