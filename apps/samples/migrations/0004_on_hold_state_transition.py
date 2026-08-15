"""
Extend the state-machine trigger's lookup table for ON_HOLD.

Same split as 0002_state_machine_trigger: schema (0003) is separate from
the trigger data (here), and the rows inserted are exactly the delta
between the old and new `ALLOWED_TRANSITIONS` in apps/samples/models.py.
The trigger function and the table itself already exist; nothing about
them changes, only its contents grow by three rows.

The insert is `ON CONFLICT DO NOTHING` rather than a plain INSERT: 0002's
`seed_transitions` does a live `from apps.samples.models import
ALLOWED_TRANSITIONS`, not a frozen historical snapshot, so on a database
migrated from empty it already seeds today's full dict — ON_HOLD rows
included — and this migration would otherwise collide with its own rows.
On a database that ran 0002 before ON_HOLD existed, this is what actually
adds them. Both paths converge on the same table contents either way.

test_python_and_database_state_machines_agree asserts the table matches
ALLOWED_TRANSITIONS exactly, so a mismatch here fails the suite rather than
drifting silently.
"""

from django.db import migrations

NEW_TRANSITIONS = [
    ("received", "on_hold"),
    ("on_hold", "accessioned"),
    ("on_hold", "rejected"),
]


def seed(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO sample_state_transition (from_state, to_state) "
            "VALUES (%s, %s) ON CONFLICT (from_state, to_state) DO NOTHING",
            NEW_TRANSITIONS,
        )


def unseed(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.executemany(
            "DELETE FROM sample_state_transition WHERE from_state = %s AND to_state = %s",
            NEW_TRANSITIONS,
        )


class Migration(migrations.Migration):
    dependencies = [("samples", "0003_customer_and_acceptance_review")]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
