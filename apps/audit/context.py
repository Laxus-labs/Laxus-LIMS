"""
Carries "who is acting, and why" from the web request down to the database
triggers that write the audit trail.

The mechanism is Postgres session variables set with `set_config()`. The
audit trigger reads them with `current_setting(..., true)`, which returns
NULL instead of erroring when unset.

Why not just pass the user into a Django signal? Because signals only fire
for ORM writes. Anything that touches the database another way — a data
migration, a bulk `update()`, a psql session — bypasses them entirely, and
those are exactly the writes an auditor cares most about. Session variables
attach to the *connection*, so the trigger sees the actor no matter which
code path did the write.

Actor and reason are scoped differently on purpose:

* **Actor is session-level** (`set_config(..., false)`). It is bound by
  middleware, in `process_request`, before any service function has opened
  its own `@transaction.atomic()` block — and Django requests run in
  autocommit mode by default, so a transaction-local (`true`) value set
  there would revert the instant that single, unwrapped statement's
  implicit transaction ends, long before the view's real transaction
  begins. That is not hypothetical: it silently null-actored every
  API-originated audit row until this was caught. Session scope survives
  that boundary. It does not leak into the next request on a pooled
  connection either, because `set_actor` runs unconditionally at the very
  start of every request, overwriting whatever the previous request left
  behind before any of the new request's own writes happen.
* **Reason stays transaction-local** (`set_config(..., true)`). It is only
  ever set from inside the same `@transaction.atomic()` block as the
  writes it annotates (see `reason()` below and its callers in
  `apps/samples/services.py` / `apps/inventory/services.py`), so it is
  guaranteed to be cleared when that transaction ends even if the
  `finally` below were somehow skipped.
"""

from contextlib import contextmanager

from django.db import connection

ACTOR_ID = "lims.actor_id"
ACTOR_USERNAME = "lims.actor_username"
REQUEST_ID = "lims.request_id"
REASON_CODE = "lims.reason_code"
REASON_TEXT = "lims.reason_text"


def _set(key: str, value, *, is_local: bool = True) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config(%s, %s, %s)",
            [key, "" if value is None else str(value), is_local],
        )


def set_actor(user=None, request_id: str = "") -> None:
    """Bind the acting user to the database session (see module docstring
    for why this is session-level, not transaction-level)."""
    _set(ACTOR_ID, getattr(user, "pk", "") or "", is_local=False)
    _set(ACTOR_USERNAME, getattr(user, "username", "") or "", is_local=False)
    _set(REQUEST_ID, request_id or "", is_local=False)


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
    _set(ACTOR_ID, "", is_local=False)
    _set(ACTOR_USERNAME, "", is_local=False)
    _set(REQUEST_ID, "", is_local=False)
    _set(REASON_CODE, "")
    _set(REASON_TEXT, "")
