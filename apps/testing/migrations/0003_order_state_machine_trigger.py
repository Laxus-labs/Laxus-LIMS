"""
Enforce the test-order lifecycle in the database — same pattern, and same
reasoning, as apps/samples/migrations/0002_state_machine_trigger.py: one
Python dict (ALLOWED_ORDER_TRANSITIONS) is projected into a lookup table a
trigger consults, so `TestOrder.objects.filter(...).update(status=...)`, a
data migration, or a psql session cannot produce an illegal status any more
than the service layer can.

test_python_and_database_order_state_machines_agree (apps/testing/tests)
asserts the table matches the dict exactly.
"""

from django.db import migrations

TRANSITION_TABLE = """
CREATE TABLE test_order_status_transition (
    from_status varchar(20) NOT NULL,
    to_status   varchar(20) NOT NULL,
    PRIMARY KEY (from_status, to_status)
);

COMMENT ON TABLE test_order_status_transition IS
    'Generated from apps.testing.models.ALLOWED_ORDER_TRANSITIONS. Do not edit by hand.';
"""

ENFORCE = """
CREATE OR REPLACE FUNCTION test_order_enforce_status_transition() RETURNS trigger AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT EXISTS (
            SELECT 1 FROM test_order_status_transition
            WHERE from_status = OLD.status AND to_status = NEW.status
        ) THEN
            RAISE EXCEPTION
                'illegal test order status transition: % -> % (order %)',
                OLD.status, NEW.status, OLD.id
                USING ERRCODE = 'check_violation',
                      HINT = 'See ALLOWED_ORDER_TRANSITIONS in apps/testing/models.py';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER test_order_status_transition_trg
    BEFORE UPDATE OF status ON test_order
    FOR EACH ROW EXECUTE FUNCTION test_order_enforce_status_transition();
"""

REVERSE = """
DROP TRIGGER IF EXISTS test_order_status_transition_trg ON test_order;
DROP FUNCTION IF EXISTS test_order_enforce_status_transition();
DROP TABLE IF EXISTS test_order_status_transition;
"""


def seed_transitions(apps, schema_editor):
    from apps.testing.models import ALLOWED_ORDER_TRANSITIONS

    rows = [
        (str(from_status), str(to_status))
        for from_status, targets in ALLOWED_ORDER_TRANSITIONS.items()
        for to_status in targets
    ]
    with schema_editor.connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO test_order_status_transition (from_status, to_status) "
            "VALUES (%s, %s) ON CONFLICT DO NOTHING",
            rows,
        )


def unseed_transitions(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DELETE FROM test_order_status_transition")


class Migration(migrations.Migration):
    dependencies = [("testing", "0002_certificateofanalysis_testorder_assigned_to_and_more")]

    operations = [
        migrations.RunSQL(
            sql=TRANSITION_TABLE, reverse_sql="DROP TABLE IF EXISTS test_order_status_transition;"
        ),
        migrations.RunPython(seed_transitions, unseed_transitions),
        migrations.RunSQL(sql=ENFORCE, reverse_sql=REVERSE),
    ]
