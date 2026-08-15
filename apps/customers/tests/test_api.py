from rest_framework.test import APITestCase

from apps.customers.models import Customer
from apps.customers.tests.factories import make_customer, make_user


class CustomerAPITests(APITestCase):
    def setUp(self):
        self.user = make_user("frontdesk")
        self.client.force_authenticate(user=self.user)

    def test_create_customer(self):
        resp = self.client.post(
            "/api/customers/",
            {"name": "Acme Pharma", "code": "ACME-01", "category": "pharma_manufacturer"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(resp.data["code"], "ACME-01")
        self.assertTrue(resp.data["is_active"])
        self.assertEqual(resp.data["created_by"], "frontdesk")

    def test_duplicate_code_rejected(self):
        make_customer(self.user, code="DUPE-1")
        resp = self.client.post(
            "/api/customers/", {"name": "Other", "code": "DUPE-1"}, format="json"
        )
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_partial_update(self):
        customer = make_customer(self.user, code="UPD-1")
        resp = self.client.patch(
            f"/api/customers/{customer.pk}/", {"phone": "555-0100"}, format="json"
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["phone"], "555-0100")

    def test_deactivate_hides_from_default_list_but_not_when_requested(self):
        customer = make_customer(self.user, code="DEACT-1")
        resp = self.client.post(f"/api/customers/{customer.pk}/deactivate/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.data["is_active"])

        active_only = self.client.get("/api/customers/")
        codes = [c["code"] for c in active_only.data["results"]]
        self.assertNotIn("DEACT-1", codes)

        all_customers = self.client.get("/api/customers/?is_active=false")
        codes_inactive = [c["code"] for c in all_customers.data["results"]]
        self.assertIn("DEACT-1", codes_inactive)

    def test_reactivate(self):
        customer = make_customer(self.user, code="REACT-1", is_active=False)
        resp = self.client.post(f"/api/customers/{customer.pk}/reactivate/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["is_active"])

    def test_search_by_name(self):
        make_customer(self.user, code="SRCH-1", name="Beacon Hospital")
        resp = self.client.get("/api/customers/?search=Beacon")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["results"]), 1)
