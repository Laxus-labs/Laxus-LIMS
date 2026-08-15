"""
Customer records — the labs, hospitals, and manufacturers samples are
submitted on behalf of.

Kept deliberately thin: this is reference/master data a front-desk user
edits routinely, not a regulated lifecycle like `Sample`. It does not carry
its own login — samples are linked to a customer record so accessioning and
reporting can be filtered/billed by client, but nothing here is
customer-facing. See docs/architecture-decisions.md for the shape other
domain models take; this one intentionally does not need a state machine.
"""

from django.conf import settings
from django.db import models


class CustomerCategory(models.TextChoices):
    PHARMA_MANUFACTURER = "pharma_manufacturer", "Pharmaceutical manufacturer"
    HOSPITAL = "hospital", "Hospital"
    CRO = "cro", "Contract research organisation"
    RESEARCH_INSTITUTE = "research_institute", "Research institute"
    GOVERNMENT = "government", "Government body"
    INDIVIDUAL = "individual", "Individual"
    OTHER = "other", "Other"


class Customer(models.Model):
    id = models.BigAutoField(primary_key=True)

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=32, unique=True)
    category = models.CharField(
        max_length=30, choices=CustomerCategory.choices, default=CustomerCategory.OTHER
    )

    contact_person = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)

    billing_address = models.TextField(blank=True)
    shipping_address = models.TextField(blank=True)
    gst_number = models.CharField(max_length=15, blank=True)

    # No hard delete — a customer with samples on file cannot vanish out
    # from under them (Sample.customer is PROTECT), and regulated labs
    # need the record of who a historical sample belonged to. Deactivating
    # just hides it from pickers for new accessioning.
    is_active = models.BooleanField(default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_customers"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "customer"
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"
