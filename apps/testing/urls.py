from rest_framework.routers import DefaultRouter

from .views import (
    CertificateViewSet,
    SpecificationViewSet,
    TestDefinitionViewSet,
    TestOrderViewSet,
    TestResultViewSet,
)

router = DefaultRouter()
router.register("test-definitions", TestDefinitionViewSet, basename="test-definition")
router.register("specifications", SpecificationViewSet, basename="specification")
router.register("test-orders", TestOrderViewSet, basename="test-order")
router.register("test-results", TestResultViewSet, basename="test-result")
router.register("certificates", CertificateViewSet, basename="certificate")

urlpatterns = router.urls
