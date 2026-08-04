from rest_framework.routers import DefaultRouter

from .views import SampleViewSet

router = DefaultRouter()
router.register("samples", SampleViewSet, basename="sample")

urlpatterns = router.urls
