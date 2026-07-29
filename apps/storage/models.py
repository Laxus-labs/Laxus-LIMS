"""
N-tier physical storage hierarchy: Facility > Room > Freezer > Shelf > Rack
> Box > Well.

Why a materialised path (ltree) and not a closure table or plain adjacency
list — the trade-off, stated plainly:

  * Adjacency list alone (just `parent_id`) makes "show me every sample
    under Freezer 3" a recursive CTE on every read. Storage browsing is the
    single most common query in a LIMS UI, so paying that repeatedly is the
    wrong default.
  * A closure table gives O(1) subtree reads and clean re-parenting, at the
    cost of a second table with O(depth) rows per node and a join on every
    query. It wins when the tree is deep and re-parented constantly.
  * ltree gives indexed subtree queries directly on the row, with no join,
    and a GiST index that handles both ancestor and descendant directions.
    The cost is that re-parenting a subtree must rewrite the `path` of
    every descendant.

Physical lab storage is re-parented rarely (you move a rack between
freezers occasionally; you do not restructure a building) and read
constantly. ltree is the right trade here. `parent` is kept alongside the
path because the FK is what actually guarantees referential integrity —
the path is a derived index, maintained by trigger, and must never be the
only source of truth.
"""

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from .fields import LtreeField

# An ltree label is [A-Za-z0-9_] only. Rather than silently rewriting codes
# inside the path trigger — which would let two distinct codes collide into
# one path label — the code itself is constrained to what ltree accepts.
LTREE_LABEL = RegexValidator(
    regex=r"^[a-z0-9_]+$",
    message="Use lowercase letters, digits and underscores only (ltree label rules).",
)


class LocationType(models.TextChoices):
    FACILITY = "facility", "Facility"
    ROOM = "room", "Room"
    FREEZER = "freezer", "Freezer"
    SHELF = "shelf", "Shelf"
    RACK = "rack", "Rack"
    BOX = "box", "Box"


# Which container types may nest inside which. Enforced in
# StorageLocation.clean() so a shelf cannot end up inside a box. Tiers are
# skippable (a freezer directly in a facility is fine); order is not.
TIER_ORDER = [
    LocationType.FACILITY,
    LocationType.ROOM,
    LocationType.FREEZER,
    LocationType.SHELF,
    LocationType.RACK,
    LocationType.BOX,
]


class StorageLocation(models.Model):
    code = models.CharField(
        max_length=63,
        validators=[LTREE_LABEL],
        help_text="Path-safe identifier, unique among siblings. Becomes one ltree label.",
    )
    name = models.CharField(max_length=200)
    location_type = models.CharField(max_length=20, choices=LocationType.choices)

    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    # Derived from parent chain by a trigger; see the storage integrity
    # migration. Never written by application code.
    path = LtreeField(editable=False, db_index=False)

    # Boxes carry a grid. Rows are letters (A..), columns are 1-based.
    # Null on non-box types.
    grid_rows = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(26)]
    )
    grid_cols = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(99)]
    )

    # Freezers and rooms declare a setpoint; excursions are checked against it.
    temperature_c = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True
    )

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "storage_location"
        constraints = [
            # Sibling codes must be unique so the derived path is unambiguous.
            # Two separate constraints because NULL parent never compares
            # equal in a plain unique index, which would let duplicate roots in.
            models.UniqueConstraint(
                fields=["parent", "code"],
                condition=models.Q(parent__isnull=False),
                name="uniq_code_within_parent",
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(parent__isnull=True),
                name="uniq_code_at_root",
            ),
            models.CheckConstraint(
                condition=models.Q(parent__isnull=True) | ~models.Q(parent=models.F("id")),
                name="location_not_own_parent",
            ),
            # Mirrors the validator at the database level, so a bulk import
            # or a shell session cannot write a code the path trigger would
            # choke on.
            models.CheckConstraint(
                condition=models.Q(code__regex=r"^[a-z0-9_]+$"),
                name="location_code_is_ltree_label",
            ),
            # A grid is all-or-nothing, and only boxes have one.
            models.CheckConstraint(
                condition=(
                    models.Q(grid_rows__isnull=True, grid_cols__isnull=True)
                    | models.Q(
                        grid_rows__isnull=False,
                        grid_cols__isnull=False,
                        location_type=LocationType.BOX,
                    )
                ),
                name="grid_only_on_boxes_and_complete",
            ),
        ]
        indexes = [
            models.Index(fields=["location_type"]),
            models.Index(fields=["parent"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.code}]"

    def save(self, *args, **kwargs):
        """
        Read `path` back after writing, because a trigger computes it.

        Without this, an in-memory instance keeps the empty string Django
        sent, and `path` only looks correct after a manual refresh. That is
        a nasty failure mode rather than an inconvenient one: an empty
        ltree is an ancestor of *every* path, so a subtree query built from
        a stale instance silently matches the entire tree instead of
        matching nothing. Bugs that fail open are worth one extra SELECT.

        Locations are written rarely and read constantly, so the cost is
        negligible where it lands.
        """
        super().save(*args, **kwargs)
        if kwargs.get("update_fields") is None or "path" not in kwargs["update_fields"]:
            refreshed = (
                StorageLocation.objects.filter(pk=self.pk).values_list("path", flat=True).first()
            )
            if refreshed is not None:
                self.path = refreshed

    @property
    def capacity(self) -> int | None:
        if self.grid_rows and self.grid_cols:
            return self.grid_rows * self.grid_cols
        return None

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.parent is None:
            return
        if self.parent_id == self.id:
            raise ValidationError({"parent": "A location cannot contain itself."})

        parent_tier = TIER_ORDER.index(self.parent.location_type)
        own_tier = TIER_ORDER.index(self.location_type)
        if own_tier <= parent_tier:
            raise ValidationError(
                {
                    "parent": (
                        f"A {self.get_location_type_display().lower()} cannot be placed "
                        f"inside a {self.parent.get_location_type_display().lower()}."
                    )
                }
            )
        if self.parent.location_type == LocationType.BOX:
            raise ValidationError({"parent": "Boxes hold samples, not other locations."})

    def valid_position(self, row: str, col: int) -> bool:
        """True if (row, col) is inside this box's grid."""
        if not (self.grid_rows and self.grid_cols):
            return False
        if not row or len(row) != 1 or not row.isalpha():
            return False
        row_index = ord(row.upper()) - ord("A")
        return 0 <= row_index < self.grid_rows and 1 <= col <= self.grid_cols
