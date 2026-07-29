"""
Re-walk audit hash chains and report any break.

Intended to run on a schedule. Verification is O(n) in the stream length,
which is why it is a periodic job rather than something the audit trigger
does on every write.

Exits non-zero when a chain is broken, so a cron wrapper or CI job fails
loudly rather than logging into the void.
"""

from django.core.management.base import BaseCommand
from django.db import connection

from apps.audit.models import AuditChainHead


class Command(BaseCommand):
    help = "Verify the integrity of append-only audit hash chains."

    def add_arguments(self, parser):
        parser.add_argument(
            "--stream",
            help="Verify a single stream (table name). Defaults to all known streams.",
        )

    def handle(self, *args, **options):
        streams = (
            [options["stream"]]
            if options["stream"]
            else list(AuditChainHead.objects.values_list("stream", flat=True).order_by("stream"))
        )

        if not streams:
            self.stdout.write("No audit streams exist yet — nothing to verify.")
            return

        broken = []
        for stream in streams:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT broken_seq, expected_hash, stored_hash "
                    "FROM audit_verify_chain(%s)",
                    [stream],
                )
                rows = cursor.fetchall()

            if rows:
                seq, expected, stored = rows[0]
                broken.append(stream)
                self.stderr.write(
                    self.style.ERROR(
                        f"{stream}: BROKEN at seq {seq}\n"
                        f"  expected {expected}\n"
                        f"  stored   {stored}"
                    )
                )
            else:
                count = AuditChainHead.objects.get(stream=stream).last_seq
                self.stdout.write(self.style.SUCCESS(f"{stream}: intact ({count} events)"))

        if broken:
            # Non-zero exit so a scheduler treats this as the incident it is.
            raise SystemExit(1)
