"""
Customer creation, updates, and deactivation.

Thin compared to apps/samples/services.py — customer records have no state
machine — but mutation still goes through named functions rather than the
serializer calling `.save()` directly, so every write carries an audit
reason and there is one place to add a rule later (e.g. GST format
validation) without touching the view.
"""

from __future__ import annotations

from django.db import transaction

from apps.audit import context as audit_context

from .models import Customer


@transaction.atomic
def create_customer(*, user, reason_code: str = "CUSTOMER_REGISTERED", **fields) -> Customer:
    with audit_context.reason(reason_code):
        return Customer.objects.create(created_by=user, **fields)


@transaction.atomic
def update_customer(
    *, customer: Customer, user, reason_code: str = "CUSTOMER_UPDATED", **fields
) -> Customer:
    with audit_context.reason(reason_code):
        for field, value in fields.items():
            setattr(customer, field, value)
        customer.save()
    return customer


@transaction.atomic
def deactivate_customer(
    *, customer: Customer, user, reason_text: str = "", reason_code: str = "CUSTOMER_DEACTIVATED"
) -> Customer:
    with audit_context.reason(reason_code, reason_text):
        customer.is_active = False
        customer.save(update_fields=["is_active", "updated_at"])
    return customer


@transaction.atomic
def reactivate_customer(
    *, customer: Customer, user, reason_text: str = "", reason_code: str = "CUSTOMER_REACTIVATED"
) -> Customer:
    with audit_context.reason(reason_code, reason_text):
        customer.is_active = True
        customer.save(update_fields=["is_active", "updated_at"])
    return customer
