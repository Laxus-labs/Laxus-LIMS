"""Attach the generic audit trigger to `customer` — a business record
change (name, GST number, deactivation) is exactly the kind of thing an
auditor asks about, same reasoning as every other table in 0002."""

from django.db import migrations

ATTACH = "SELECT audit_attach('customer', 'id');"
DETACH = "DROP TRIGGER IF EXISTS audit_customer_trg ON customer;"


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0003_timezone_stable_hashing"),
        ("customers", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=ATTACH, reverse_sql=DETACH),
    ]
