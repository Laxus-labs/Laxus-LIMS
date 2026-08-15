from rest_framework import filters, mixins, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

from . import services
from .models import Customer
from .serializers import CustomerSerializer


class CustomerCursorPagination(CursorPagination):
    # The DRF default pagination class orders by "-created", which is
    # samples.Sample's field name, not this app's; every paginated
    # viewset needs its own ordering for its own model (see
    # samples.views.SampleCursorPagination).
    ordering = "-created_at"
    page_size = 50


class CustomerViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """
    Standard read/patch surface, but create/update are overridden to call
    apps.customers.services rather than the mixin's default
    `serializer.save()` — see the module docstring in services.py. No PUT
    and no destroy: a customer with samples on file is deactivated, not
    replaced or removed (Sample.customer is PROTECT).
    """

    http_method_names = ["get", "post", "patch", "head", "options"]
    queryset = Customer.objects.select_related("created_by").all()
    serializer_class = CustomerSerializer
    pagination_class = CustomerCursorPagination
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "code", "gst_number", "contact_person", "email"]

    def get_queryset(self):
        """
        The *list* action defaults to active customers only — inactive ones
        exist so a picker in the accessioning flow doesn't offer them, not
        so they disappear. `?is_active=false` finds them; `?is_active=all`
        lists everyone. Retrieve/patch/deactivate/reactivate always see the
        full table regardless of this filter, or `deactivate` would 404 the
        instant it had done its job and reactivate could never find its
        target.
        """
        qs = super().get_queryset()
        if self.action != "list":
            return qs
        is_active = self.request.query_params.get("is_active")
        if is_active is None:
            return qs.filter(is_active=True)
        if is_active.lower() == "all":
            return qs
        return qs.filter(is_active=is_active.lower() != "false")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = services.create_customer(user=request.user, **serializer.validated_data)
        return Response(self.get_serializer(customer).data, status=201)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        customer = services.update_customer(
            customer=instance, user=request.user, **serializer.validated_data
        )
        return Response(self.get_serializer(customer).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        customer = services.deactivate_customer(
            customer=self.get_object(),
            user=request.user,
            reason_text=request.data.get("reason_text", ""),
        )
        return Response(self.get_serializer(customer).data)

    @action(detail=True, methods=["post"])
    def reactivate(self, request, pk=None):
        customer = services.reactivate_customer(
            customer=self.get_object(),
            user=request.user,
            reason_text=request.data.get("reason_text", ""),
        )
        return Response(self.get_serializer(customer).data)
