"""
Enforce the sample lifecycle in the database.

The legal transitions live in one place — `ALLOWED_TRANSITIONS` in
apps/samples/models.py — and this migration projects that dict into a
lookup table the trigger consults. Generating the table from the Python
definition rather than retyping it in SQL means the two cannot disagree;
`test_state_machine.py` asserts that they still match, so a future edit to
the dict that forgets a migration fails the suite instead of silently
diverging.

Why enforce it here at all when the service layer already checks? Because
the service layer is one door. `Sample.objects.filter(...).update(state=...)`
is another, a data migration is a third, and psql is a fourth. A trigger
covers all of them.
"""

from django.db import migrations

TRANSITION_TABLE = """
CREATE TABLE sample_state_transition (
    from_state varchar(20) NOT NULL,
    to_state   varchar(20) NOT NULL,
    PRIMARY KEY (from_state, to_state)
);

COMMENT ON TABLE sample_state_transition IS
    'Generated from apps.samples.models.ALLOWED_TRANSITIONS. Do not edit by hand.';
"""

ENFORCE = """
CREATE OR REPLACE FUNCTION sample_enforce_state_transition() RETURNS trigger AS $$
BEGIN
    IF NEW.state IS DISTINCT FROM OLD.state THEN
        IF NOT EXISTS (
            SELECT 1 FROM sample_state_transition
            WHERE from_state = OLD.state AND to_state = NEW.state
        ) THEN
            RAISE EXCEPTION
                'illegal sample state transition: % -> % (sample %)',
                OLD.state, NEW.state, OLD.id
                USING ERRCODE = 'check_violation',
                      HINT = 'See ALLOWED_TRANSITIONS in apps/samples/models.py';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER sample_state_transition_trg
    BEFORE UPDATE OF state ON sample
    FOR EACH ROW EXECUTE FUNCTION sample_enforce_state_transition();
"""

REVERSE = """
DROP TRIGGER IF EXISTS sample_state_transition_trg ON sample;
DROP FUNCTION IF EXISTS sample_enforce_state_transition();
DROP TABLE IF EXISTS sample_state_transition;
"""


def seed_transitions(apps, schema_editor):
    from apps.samples.models import ALLOWED_TRANSITIONS

    rows = [
        (str(from_state), str(to_state))
        for from_state, targets in ALLOWED_TRANSITIONS.items()
        for to_state in targets
    ]
    with schema_editor.connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO sample_state_transition (from_state, to_state) VALUES (%s, %s)",
            rows,
        )


def unseed_transitions(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DELETE FROM sample_state_transition")


class Migration(migrations.Migration):
    dependencies = [("samples", "0001_initial")]

    operations = [
        migrations.RunSQL(sql=TRANSITION_TABLE, reverse_sql="DROP TABLE IF EXISTS sample_state_transition;"),
        migrations.RunPython(seed_transitions, unseed_transitions),
        migrations.RunSQL(sql=ENFORCE, reverse_sql=REVERSE),
    ]
