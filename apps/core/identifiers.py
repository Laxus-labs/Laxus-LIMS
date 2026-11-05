"""
Public ID generation — Global Standards v1.0 §2.

Format: {PREFIX}-{YYYY}-{seq:06d}, e.g. MED-2026-000123
Sequence is per organization, per prefix, per year.

Generated server-side inside the caller's transaction and never editable.
Uses a dedicated counter row locked with SELECT FOR UPDATE so two concurrent
requests cannot be handed the same number.
"""
from django.db import connection, models, transaction
from django.utils import timezone


class IdSequence(models.Model):
    """One counter row per (organization, prefix, year)."""

    organization_id = models.BigIntegerField()
    prefix = models.CharField(max_length=8)
    year = models.PositiveSmallIntegerField()
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization_id", "prefix", "year"], name="uniq_id_sequence"
            )
        ]

    def __str__(self):
        return f"{self.prefix}-{self.year}: {self.last_value}"


# Prefixes defined in Global Standards §2. Extend here, not inline at call sites.
PREFIXES = {
    "media_lot": "MED",
    "culture_lot": "CUL",
    "sample": "SMP",
    "test_instance": "TST",
    "worksheet": "WKS",
    "calculation": "CAL",
    "result": "RES",
    "coa": "COA",
    "em_sample": "ENV",
    "excursion": "EXC",
}


@transaction.atomic
def next_public_id(kind: str, organization_id: int, year: int | None = None) -> str:
    """Return the next public ID for `kind` within an organization.

    Must be called inside the same transaction as the record creation so a
    rolled-back create does not burn a number.
    """
    try:
        prefix = PREFIXES[kind]
    except KeyError:
        raise ValueError(
            f"Unknown ID kind {kind!r}. Add it to PREFIXES in apps/core/identifiers.py."
        )

    year = year or timezone.localdate().year

    qs = IdSequence.objects.all()
    # Postgres (production) takes a row lock so concurrent requests serialise.
    # SQLite has no SELECT FOR UPDATE; its single-writer lock covers us in the
    # test harness. Never rely on SQLite for concurrency in production.
    if connection.features.has_select_for_update:
        qs = qs.select_for_update()

    counter, _ = qs.get_or_create(
        organization_id=organization_id, prefix=prefix, year=year
    )
    counter.last_value += 1
    counter.save(update_fields=["last_value"])

    return f"{prefix}-{year}-{counter.last_value:06d}"
