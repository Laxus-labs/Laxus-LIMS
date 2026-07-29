"""
Carries "who is acting, and why" from the web request down to the database
triggers that write the audit trail.

The mechanism is Postgres session variables set with `set_config(..., true)`
— the `true` makes them transaction-local, so they are discarded on commit
or rollback and cannot leak into the next request that reuses the pooled
connection. The audit trigger reads them with `current_setting(..., true)`,
which returns NULL instead of erroring when unset.

Why not just pass the user into a Django signal? Because signals only fire
for ORM writes. Anything that touches the database another way — a data
migration, a bulk `update()`, a psql session — bypasses them entirely, and
those are exactly the writes an auditor cares most about. Session variables
attach to the *connection*, so the trigger sees the actor no matter which
code path did the write.
"""

from contextlib import contextmanager

from django.db import connection

ACTOR_ID = "lims.actor_id"
ACTOR_USERNAME = "lims.actor_username"
REQUEST_ID = "lims.request_id"
REASON_CODE = "lims.reason_code"
REASON_TEXT = "lims.reason_text"


def _set(key: str, value) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [key, "" if value is None else str(value)])


def set_actor(user=None, request_id: str = "") -> None:
    """Bind the acting user to the current transaction."""
    _set(ACTOR_ID, getattr(user, "pk", "") or "")
    _set(ACTOR_USERNAME, getattr(user, "username", "") or "")
    _set(REQUEST_ID, request_id or "")


def set_reason(code: str = "", text: str = "") -> None:
    """
    Attach a reason to the next writes in this transaction.

    Regulated changes need a *why*, not just a *what*: disposing a sample,
    adjusting stock, or overriding a result all require a reason code that
    lands in the audit row.
    """
    _set(REASON_CODE, code)
    _set(REASON_TEXT, text)


@contextmanager
def reason(code: str, text: str = ""):
    """Scope a reason to a block of writes, then clear it."""
    set_reason(code, text)
    try:
        yield
    finally:
        set_reason("", "")


def clear() -> None:
    for key in (ACTOR_ID, ACTOR_USERNAME, REQUEST_ID, REASON_CODE, REASON_TEXT):
        _set(key, "")
