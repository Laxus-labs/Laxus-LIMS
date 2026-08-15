"""
Append-only, tamper-evident audit trail.

Design notes (the reasoning matters more than the code here):

*Where the writes come from.* Audit rows are written by database triggers,
not by application middleware. Middleware only records what remembers to
call it; a trigger records every write, including a `manage.py shell`
session or a DBA typing UPDATE in psql. That difference is what separates
a real audit trail from a decorative one.

*What "tamper-evident" actually buys you.* Each row carries the hash of the
previous row in its stream, so altering or deleting a historical row breaks
every hash after it and is detectable by re-walking the chain. This is
tamper-*evident*, not tamper-*proof*: someone with UPDATE rights on the
table who can also run the hash function could rewrite the chain forward.
Detecting that requires anchoring chain heads somewhere the database role
cannot reach — append-only object storage (S3 Object Lock) or a separate
account, on a schedule. That anchoring job is out of scope here, and the
README says so rather than implying a guarantee the code does not deliver.

*Why chains are per-stream.* A single global chain would serialise every
mutation in the system behind one row lock — every sample move queued
behind every stock decrement. Chaining per stream (one per table) keeps
contention scoped to writes that already touch the same table.
"""

from django.conf import settings
from django.db import models


class AuditAction(models.TextChoices):
    INSERT = "INSERT", "Insert"
    UPDATE = "UPDATE", "Update"
    DELETE = "DELETE", "Delete"


class AuditChainHead(models.Model):
    """
    Last hash and sequence number per stream. The audit trigger locks the
    matching row, which is what orders concurrent writes within a stream.
    """

    stream = models.CharField(max_length=100, primary_key=True)
    last_hash = models.CharField(max_length=64)
    last_seq = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "audit_chain_head"

    def __str__(self) -> str:
        return f"{self.stream}@{self.last_seq}"


class AuditEvent(models.Model):
    """
    One row per mutation. Never updated, never deleted — enforced by a
    trigger and by revoking UPDATE/DELETE from the application role.
    """

    id = models.BigAutoField(primary_key=True)
    stream = models.CharField(max_length=100)
    seq = models.BigIntegerField(help_text="Position within the stream, gapless.")

    occurred_at = models.DateTimeField()
    action = models.CharField(max_length=10, choices=AuditAction.choices)
    table_name = models.CharField(max_length=100)
    record_pk = models.CharField(max_length=64)

    # Denormalised actor: the FK goes null if an account is ever removed,
    # but the audit row must still name who acted. Storing both means the
    # trail survives even a hard delete on the user table.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    actor_username = models.CharField(max_length=150, blank=True)

    previous_state = models.JSONField(null=True, blank=True)
    new_state = models.JSONField(null=True, blank=True)
    # Which columns actually changed — lets a reviewer scan a trail without
    # diffing two JSON blobs by eye.
    changed_fields = models.JSONField(default=list, blank=True)

    reason_code = models.CharField(max_length=64, blank=True)
    reason_text = models.TextField(blank=True)
    # Correlates every row written while serving one API call.
    request_id = models.CharField(max_length=64, blank=True)

    prev_hash = models.CharField(max_length=64)
    row_hash = models.CharField(max_length=64)

    class Meta:
        db_table = "audit_event"
        constraints = [
            models.UniqueConstraint(fields=["stream", "seq"], name="uniq_audit_stream_seq"),
        ]
        indexes = [
            models.Index(fields=["table_name", "record_pk", "-occurred_at"]),
            models.Index(fields=["actor", "-occurred_at"]),
            models.Index(fields=["-occurred_at"]),
            models.Index(fields=["request_id"]),
        ]

    def __str__(self) -> str:
        return (
            f"{self.occurred_at:%Y-%m-%d %H:%M:%S} {self.action} "
            f"{self.table_name}#{self.record_pk}"
        )


class ElectronicSignature(models.Model):
    """
    An e-signature in the 21 CFR Part 11 sense is not a checkbox: it is the
    signer's identity, the timestamp, and *the meaning of the signing*,
    captured together and bound to the record — with the signer having
    re-authenticated at the moment of signing.

    This model stores those components; re-authentication happens in the
    service layer before a row is written (see apps.testing.services).

    What this is not: a cryptographic digital signature. There is no PKI or
    per-user private key here, so non-repudiation rests on account control,
    not on asymmetric crypto. Per-user signing keys are the natural upgrade
    and are listed under the README's limitations.
    """

    class Meaning(models.TextChoices):
        AUTHORED = "authored", "Authored / entered by"
        REVIEWED = "reviewed", "Reviewed by"
        APPROVED = "approved", "Approved by"
        REJECTED = "rejected", "Rejected by"
        DISPOSED = "disposed", "Disposal authorised by"
        COA_RELEASED = "coa_released", "COA released by"
        OOS_CLOSED = "oos_closed", "OOS closure authorised by"

    signer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    signer_username = models.CharField(max_length=150)
    signer_full_name = models.CharField(max_length=300, blank=True)
    meaning = models.CharField(max_length=20, choices=Meaning.choices)
    signed_at = models.DateTimeField(auto_now_add=True)

    table_name = models.CharField(max_length=100)
    record_pk = models.CharField(max_length=64)
    # Hash of the record's state at signing time, so a later edit to a
    # signed record is detectable.
    record_hash = models.CharField(max_length=64)

    audit_event = models.ForeignKey(
        AuditEvent, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        db_table = "electronic_signature"
        indexes = [
            models.Index(fields=["table_name", "record_pk"]),
            models.Index(fields=["signer", "-signed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.signer_username} {self.meaning} {self.table_name}#{self.record_pk}"
