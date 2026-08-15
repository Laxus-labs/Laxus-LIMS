"""
Schema for the customer link and the pre-testing acceptance review:
ON_HOLD is added to the state machine here as a column-level, no-op change
(state was always a plain CharField) — the transition table that actually
enforces it is seeded by the next migration, 0004, following the same
split used by 0001/0002 for the original state machine.

`RejectionReasonCode` is seeded with the lab's fixed list (R001-R012 plus a
catch-all "Other") by the RunPython step at the end of this migration.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

REJECTION_REASONS = [
    ("R001", "Broken Container"),
    ("R002", "Leaking Sample"),
    ("R003", "Wrong Sample"),
    ("R004", "Insufficient Quantity"),
    ("R005", "Missing Documentation"),
    ("R006", "Label Mismatch"),
    ("R007", "Temperature Excursion"),
    ("R008", "Expired Sample"),
    ("R009", "Contaminated Sample"),
    ("R010", "Improper Packaging"),
    ("R011", "Unknown Source"),
    ("R012", "Client Cancellation"),
    ("OTHER", "Other"),
]


def seed_rejection_reasons(apps, schema_editor):
    RejectionReasonCode = apps.get_model("samples", "RejectionReasonCode")
    RejectionReasonCode.objects.bulk_create(
        RejectionReasonCode(
            code=code, label=label, sort_order=i, requires_details=(code == "OTHER")
        )
        for i, (code, label) in enumerate(REJECTION_REASONS)
    )


def unseed_rejection_reasons(apps, schema_editor):
    apps.get_model("samples", "RejectionReasonCode").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0001_initial'),
        ('samples', '0002_state_machine_trigger'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RejectionReasonCode',
            fields=[
                ('code', models.CharField(max_length=16, primary_key=True, serialize=False)),
                ('label', models.CharField(max_length=200)),
                ('requires_details', models.BooleanField(default=False)),
                ('is_active', models.BooleanField(default=True)),
                ('sort_order', models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                'db_table': 'rejection_reason_code',
                'ordering': ['sort_order', 'code'],
            },
        ),
        migrations.AddField(
            model_name='sample',
            name='acceptance_decision',
            field=models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('accepted_with_deviation', 'Accepted with deviation'), ('on_hold', 'On hold'), ('rejected', 'Rejected')], default='pending', max_length=30),
        ),
        migrations.AddField(
            model_name='sample',
            name='client_notified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sample',
            name='container_integrity_ok',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sample',
            name='customer',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='samples', to='customers.customer'),
        ),
        migrations.AddField(
            model_name='sample',
            name='deviation_notes',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='sample',
            name='documentation_ok',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sample',
            name='label_ok',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sample',
            name='qa_approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sample',
            name='qa_approved_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='qa_approved_samples', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='sample',
            name='quantity_ok',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sample',
            name='rejection_reason_other',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='sample',
            name='seal_integrity_ok',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sample',
            name='transport_conditions_ok',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='sample',
            name='state',
            field=models.CharField(choices=[('received', 'Received'), ('on_hold', 'On hold'), ('accessioned', 'Accessioned'), ('in_storage', 'In storage'), ('in_testing', 'In testing'), ('aliquoted', 'Aliquoted'), ('exhausted', 'Exhausted'), ('disposed', 'Disposed'), ('rejected', 'Rejected')], default='received', max_length=20),
        ),
        migrations.AddField(
            model_name='sample',
            name='rejection_reason',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='samples', to='samples.rejectionreasoncode'),
        ),
        migrations.AddConstraint(
            model_name='sample',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('state', 'rejected'), _negated=True), ('rejection_reason__isnull', False), _connector='OR'), name='sample_rejected_requires_reason'),
        ),
        migrations.AddConstraint(
            model_name='sample',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('state', 'rejected'), _negated=True), ('qa_approved_by__isnull', False), _connector='OR'), name='sample_rejected_requires_qa_approval'),
        ),
        migrations.RunPython(seed_rejection_reasons, unseed_rejection_reasons),
    ]
