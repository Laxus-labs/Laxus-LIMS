"""Reuses apps.samples.tests.factories for users/samples (same "pw"
password, needed since e-signatures re-authenticate) and adds
testing-domain builders."""

from decimal import Decimal

from django.utils import timezone

from apps.samples.tests.factories import make_sample, make_user  # noqa: F401
from apps.testing.models import Specification, TestDefinition, TestOrder


def make_test_definition(code="GLU", name="Glucose", turnaround_hours=24, **kwargs):
    return TestDefinition.objects.create(
        code=code, name=name, turnaround_hours=turnaround_hours, unit=kwargs.pop("unit", "mg/dL"), **kwargs
    )


def make_specification(test_definition, user, sample_type="", lower=70, upper=110, **kwargs):
    return Specification.objects.create(
        test_definition=test_definition,
        sample_type=sample_type,
        lower_limit=Decimal(lower) if lower is not None else None,
        upper_limit=Decimal(upper) if upper is not None else None,
        effective_from=timezone.now(),
        created_by=user,
        **kwargs,
    )


def make_order(sample, test_definition, user, **kwargs):
    return TestOrder.objects.create(
        sample=sample, test_definition=test_definition, requested_by=user, **kwargs
    )
