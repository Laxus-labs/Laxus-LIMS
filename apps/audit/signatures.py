"""
The one place an `ElectronicSignature` row gets created.

Every regulated sign-off in this system — reviewing a result, approving it,
releasing a COA, rejecting or disposing a sample, closing out an
out-of-specification result — goes through `sign()` rather than each
service constructing the row itself. That keeps re-authentication and
record-state hashing (see the model docstring in apps/audit/models.py)
uniform, and means a signer's password is checked in exactly one function
in the whole codebase.
"""

from __future__ import annotations

import hashlib
import json

from django.contrib.auth import authenticate

from .errors import ReauthenticationFailed
from .models import AuditEvent, ElectronicSignature


def _hash_state(state: dict) -> str:
    # sort_keys makes the hash independent of dict construction order;
    # default=str covers Decimal/UUID/datetime values that json can't
    # serialise natively — this is a fingerprint, not a wire format, so
    # lossy string coercion is fine as long as it's deterministic.
    payload = json.dumps(state, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def sign(
    *,
    signer,
    password: str,
    meaning: str,
    table_name: str,
    record_pk,
    record_state: dict,
    audit_event: AuditEvent | None = None,
) -> ElectronicSignature:
    """
    Re-authenticate `signer` with `password`, then record a signature bound
    to `record_state` — typically the fields a reader would need to know
    what was actually signed off on. Re-authentication is what makes this a
    signature rather than a checkbox: being logged in is not the same as
    having just proven presence, and a browser tab left open across a lunch
    break should not be enough to approve a result.

    Raises ReauthenticationFailed, and writes nothing, if the password does
    not match — a failed attempt must not itself look like a signing event
    in the trail.
    """
    if not password or authenticate(username=signer.get_username(), password=password) is None:
        raise ReauthenticationFailed("Password did not match; signature not recorded.")

    return ElectronicSignature.objects.create(
        signer=signer,
        signer_username=signer.get_username(),
        signer_full_name=signer.get_full_name(),
        meaning=meaning,
        table_name=table_name,
        record_pk=str(record_pk),
        record_hash=_hash_state(record_state),
        audit_event=audit_event,
    )
