"""
Regression test for the AuditContextMiddleware JWT-resolution fix.

DRF's JWTAuthentication normally resolves `request.user` inside
APIView.dispatch(), which runs after Django's middleware phase — so
without the fix in apps/audit/middleware.py, a JWT-authenticated API
write would bind a null actor to its audit row. This proves it doesn't.
"""

from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.models import AuditEvent
from apps.samples.models import SampleType
from apps.samples.tests.factories import make_user


class JWTActorBindingTests(APITestCase):
    def setUp(self):
        self.user = make_user("api_actor")
        token = RefreshToken.for_user(self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

    def test_api_write_records_the_jwt_authenticated_actor(self):
        response = self.client.post(
            "/api/samples/accession-batch/",
            {"scans": [{"barcode": "BC-JWT-1", "sample_type": SampleType.SERUM}]},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        sample_id = response.data["created"][0]["id"]

        event = AuditEvent.objects.filter(
            table_name="sample", record_pk=str(sample_id), action="INSERT"
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor_id, self.user.pk)
        self.assertEqual(event.actor_username, self.user.username)
