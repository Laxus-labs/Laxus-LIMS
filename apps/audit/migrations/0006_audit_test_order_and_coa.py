"""Attach the generic audit trigger to `test_order` and
`certificate_of_analysis` — both were mutation-worthy gaps (see the
"missing" column of the sample-management gap analysis this phase closes):
who reassigned/cancelled an order, and who generated/superseded a
certificate, are exactly the questions an auditor asks."""

from django.db import migrations

ATTACH = """
SELECT audit_attach('test_order', 'id');
SELECT audit_attach('certificate_of_analysis', 'id');
"""

DETACH = """
DROP TRIGGER IF EXISTS audit_test_order_trg ON test_order;
DROP TRIGGER IF EXISTS audit_certificate_of_analysis_trg ON certificate_of_analysis;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0005_alter_electronicsignature_meaning"),
        ("testing", "0003_order_state_machine_trigger"),
    ]

    operations = [
        migrations.RunSQL(sql=ATTACH, reverse_sql=DETACH),
    ]
