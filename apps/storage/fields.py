"""
Minimal `ltree` support for Django.

Django has no built-in ltree field, and pulling a whole dependency in for
one column is not worth it. This wraps the type and the three operators
the storage hierarchy actually needs:

    path__descendant_of='facility.room_a'   ->  path <@ 'facility.room_a'
    path__ancestor_of='a.b.c'               ->  path @> 'a.b.c'
    path__matches='facility.*.freezer_*'    ->  path ~ 'lquery'
"""

from django.db import models


class LtreeField(models.TextField):
    description = "Postgres ltree materialised path"

    def db_type(self, connection) -> str:
        return "ltree"

    def from_db_value(self, value, expression, connection):
        return value


class _LtreeLookup(models.Lookup):
    def as_sql(self, compiler, connection):
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return f"{lhs} {self.operator} {rhs}::{self.rhs_cast}", lhs_params + rhs_params


@LtreeField.register_lookup
class DescendantOf(_LtreeLookup):
    """Rows at or below the given path — 'everything inside Freezer 3'."""

    lookup_name = "descendant_of"
    operator = "<@"
    rhs_cast = "ltree"


@LtreeField.register_lookup
class AncestorOf(_LtreeLookup):
    """Rows at or above the given path — breadcrumb for a location."""

    lookup_name = "ancestor_of"
    operator = "@>"
    rhs_cast = "ltree"


@LtreeField.register_lookup
class Matches(_LtreeLookup):
    """lquery pattern match, e.g. '*.freezer_*.*{1,2}'."""

    lookup_name = "matches"
    operator = "~"
    rhs_cast = "lquery"
