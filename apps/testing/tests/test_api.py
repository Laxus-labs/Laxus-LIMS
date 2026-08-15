from decimal import Decimal

from rest_framework.test import APITestCase

from apps.samples.models import SampleState

from .factories import make_order, make_sample, make_specification, make_test_definition, make_user


class TestOrderAPITests(APITestCase):
    def setUp(self):
        self.user = make_user("api_orderer")
        self.sample = make_sample(self.user, state=SampleState.ACCESSIONED)
        self.test_def = make_test_definition(code="CREAT", name="Creatinine")
        self.client.force_authenticate(user=self.user)

    def test_place_order(self):
        resp = self.client.post(
            "/api/test-orders/",
            {"sample_id": str(self.sample.pk), "test_definition_id": self.test_def.pk},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "requested")
        self.assertEqual(resp.data["sample"]["barcode"], self.sample.barcode)

    def test_start_enter_result_and_lifecycle(self):
        order = make_order(self.sample, self.test_def, self.user)
        resp = self.client.post(f"/api/test-orders/{order.pk}/start/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "in_progress")

        resp = self.client.post(
            f"/api/test-orders/{order.pk}/enter-result/",
            {"numeric_value": "1.2", "instrument_ref": "COBAS-1"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["status"], "draft")

        order_resp = self.client.get(f"/api/test-orders/{order.pk}/")
        self.assertEqual(order_resp.data["status"], "complete")

    def test_cancel_requires_reason(self):
        order = make_order(self.sample, self.test_def, self.user)
        resp = self.client.post(f"/api/test-orders/{order.pk}/cancel/", {}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

        resp = self.client.post(
            f"/api/test-orders/{order.pk}/cancel/", {"reason_text": "Duplicate order."}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "cancelled")


class ResultReviewAPITests(APITestCase):
    def setUp(self):
        self.author = make_user("api_author")
        self.reviewer = make_user("api_reviewer", role="reviewer")
        self.qa = make_user("api_qa", role="qa_approver")
        self.sample = make_sample(self.author, state=SampleState.ACCESSIONED, sample_type="serum")
        self.test_def = make_test_definition(code="NA", name="Sodium")
        make_specification(self.test_def, self.author, sample_type="serum", lower=135, upper=145)
        self.order = make_order(self.sample, self.test_def, self.author)

    def _enter_and_submit(self, value="140"):
        self.client.force_authenticate(user=self.author)
        enter = self.client.post(
            f"/api/test-orders/{self.order.pk}/enter-result/", {"numeric_value": value}, format="json"
        )
        result_id = enter.data["id"]
        self.client.post(f"/api/test-results/{result_id}/submit/")
        return result_id

    def test_review_and_approve_require_signature(self):
        result_id = self._enter_and_submit()

        self.client.force_authenticate(user=self.reviewer)
        no_password = self.client.post(f"/api/test-results/{result_id}/review/", {}, format="json")
        self.assertEqual(no_password.status_code, 400, no_password.data)

        wrong_password = self.client.post(
            f"/api/test-results/{result_id}/review/", {"password": "nope"}, format="json"
        )
        self.assertEqual(wrong_password.status_code, 401, wrong_password.data)

        reviewed = self.client.post(
            f"/api/test-results/{result_id}/review/", {"password": "pw"}, format="json"
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        self.assertEqual(reviewed.data["status"], "reviewed")

        self.client.force_authenticate(user=self.qa)
        approved = self.client.post(
            f"/api/test-results/{result_id}/approve/", {"password": "pw"}, format="json"
        )
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data["status"], "approved")

    def test_out_of_spec_requires_oos_closure(self):
        result_id = self._enter_and_submit(value="200")
        self.client.force_authenticate(user=self.reviewer)
        self.client.post(f"/api/test-results/{result_id}/review/", {"password": "pw"}, format="json")

        self.client.force_authenticate(user=self.qa)
        resp = self.client.get(f"/api/test-results/{result_id}/")
        self.assertTrue(resp.data["is_out_of_spec"])

        close = self.client.post(
            f"/api/test-results/{result_id}/close-oos/",
            {"password": "pw", "resolution_text": "Confirmed on repeat analysis."},
            format="json",
        )
        self.assertEqual(close.status_code, 200, close.data)
        self.assertIsNotNone(close.data["oos_closed_at"])


class CertificateAPITests(APITestCase):
    def setUp(self):
        self.author = make_user("api_coa_author")
        self.qa = make_user("api_coa_qa", role="qa_approver")
        self.sample = make_sample(self.author, state=SampleState.ACCESSIONED, sample_type="urine")
        self.test_def = make_test_definition(code="PROT", name="Protein")
        self.order = make_order(self.sample, self.test_def, self.author)

    def _approve_result(self):
        self.client.force_authenticate(user=self.author)
        enter = self.client.post(
            f"/api/test-orders/{self.order.pk}/enter-result/", {"numeric_value": "10"}, format="json"
        )
        result_id = enter.data["id"]
        self.client.post(f"/api/test-results/{result_id}/submit/")
        reviewer = make_user("api_coa_reviewer", role="reviewer")
        self.client.force_authenticate(user=reviewer)
        self.client.post(f"/api/test-results/{result_id}/review/", {"password": "pw"}, format="json")
        self.client.force_authenticate(user=self.qa)
        self.client.post(f"/api/test-results/{result_id}/approve/", {"password": "pw"}, format="json")

    def test_generate_release_and_render_certificate(self):
        self._approve_result()

        self.client.force_authenticate(user=self.author)
        gen = self.client.post(f"/api/samples/{self.sample.pk}/generate-coa/")
        self.assertEqual(gen.status_code, 201, gen.data)
        coa_id = gen.data["id"]
        self.assertEqual(gen.data["status"], "draft")

        self.client.force_authenticate(user=self.qa)
        released = self.client.post(
            f"/api/certificates/{coa_id}/release/", {"password": "pw"}, format="json"
        )
        self.assertEqual(released.status_code, 200, released.data)
        self.assertEqual(released.data["status"], "released")

        html = self.client.get(f"/api/certificates/{coa_id}/html/")
        self.assertEqual(html.status_code, 200)
        self.assertIn(b"Certificate of Analysis", html.content)

        sample_resp = self.client.get(f"/api/samples/{self.sample.pk}/")
        self.assertIsNotNone(sample_resp.data["actual_completion_date"])
