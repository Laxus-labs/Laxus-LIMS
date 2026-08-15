"""
`CustomerSerializer` doubles as read and write validation, but it never
writes — `views.CustomerViewSet` always routes the validated data through
`apps.customers.services` (see that module's docstring, and the same
convention in apps/samples/serializers.py).
"""

from rest_framework import serializers

from .models import Customer


class CustomerSerializer(serializers.ModelSerializer):
    created_by = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = Customer
        fields = [
            "id",
            "name",
            "code",
            "category",
            "contact_person",
            "phone",
            "email",
            "billing_address",
            "shipping_address",
            "gst_number",
            "is_active",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_active", "created_by", "created_at", "updated_at"]
