from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


def migrate_existing_approval_config(apps, schema_editor):
    JobTitlePermission = apps.get_model('accounts', 'JobTitlePermission')
    ApprovalLevel = apps.get_model('accounts', 'ApprovalLevel')
    ApprovalDelegation = apps.get_model('accounts', 'ApprovalDelegation')

    workflows = (
        {
            'workflow_type': 'store_issue',
            'approve_field': 'can_approve_store_requisition',
            'order_field': 'store_approval_order',
            'bypass_field': 'can_bypass_store_approval',
            'max_bypass_field': 'max_store_bypass_order',
        },
        {
            'workflow_type': 'purchase',
            'approve_field': 'can_approve_purchase_order',
            'order_field': 'purchase_approval_order',
            'bypass_field': 'can_bypass_purchase_approval',
            'max_bypass_field': 'max_purchase_bypass_order',
        },
        {
            'workflow_type': 'achievement_report',
            'approve_field': 'can_approve_achievement_report',
            'order_field': 'achievement_approval_order',
            'bypass_field': 'can_bypass_achievement_approval',
            'max_bypass_field': 'max_achievement_bypass_order',
        },
    )

    for workflow in workflows:
        permissions = JobTitlePermission.objects.filter(
            **{
                workflow['approve_field']: True,
                f"{workflow['order_field']}__isnull": False,
            }
        )
        for permission in permissions:
            order_number = getattr(permission, workflow['order_field'])
            order_exists = ApprovalLevel.objects.filter(
                workflow_type=workflow['workflow_type'],
                order_number=order_number,
            ).exists()
            job_title_exists = ApprovalLevel.objects.filter(
                workflow_type=workflow['workflow_type'],
                job_title_id=permission.job_title_id,
            ).exists()
            if not order_exists and not job_title_exists:
                ApprovalLevel.objects.create(
                    workflow_type=workflow['workflow_type'],
                    order_number=order_number,
                    job_title_id=permission.job_title_id,
                )

    for workflow in workflows:
        levels_by_order = {
            level.order_number: level
            for level in ApprovalLevel.objects.filter(workflow_type=workflow['workflow_type'])
        }
        permissions = JobTitlePermission.objects.filter(
            **{
                workflow['approve_field']: True,
                workflow['bypass_field']: True,
                f"{workflow['order_field']}__isnull": False,
                f"{workflow['max_bypass_field']}__isnull": False,
            }
        )
        for permission in permissions:
            own_order = getattr(permission, workflow['order_field'])
            first_delegated_order = getattr(permission, workflow['max_bypass_field'])
            for order_number in range(first_delegated_order, own_order):
                approval_level = levels_by_order.get(order_number)
                if approval_level is None:
                    continue
                ApprovalDelegation.objects.get_or_create(
                    source_job_title_id=permission.job_title_id,
                    approval_level_id=approval_level.id,
                )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_systemsettings'),
    ]

    operations = [
        migrations.CreateModel(
            name='ApprovalLevel',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('workflow_type', models.CharField(choices=[('purchase', 'Spare part purchase approval'), ('store_issue', 'Spare part store issue approval'), ('achievement_report', 'Achievement report approval')], db_index=True, max_length=40)),
                ('order_number', models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('job_title', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='approval_levels', to='accounts.jobtitle')),
            ],
            options={
                'ordering': ['workflow_type', 'order_number'],
            },
        ),
        migrations.CreateModel(
            name='ApprovalDelegation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('approval_level', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='delegations', to='accounts.approvallevel')),
                ('source_job_title', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='approval_delegations', to='accounts.jobtitle')),
            ],
            options={
                'ordering': ['approval_level__workflow_type', 'approval_level__order_number', 'source_job_title__title_name'],
            },
        ),
        migrations.AlterField(
            model_name='jobtitlepermission',
            name='achievement_approval_order',
            field=models.PositiveSmallIntegerField(blank=True, null=True, validators=[MinValueValidator(1)], verbose_name='ترتيب تعميد التقرير'),
        ),
        migrations.AlterField(
            model_name='jobtitlepermission',
            name='purchase_approval_order',
            field=models.PositiveSmallIntegerField(blank=True, null=True, validators=[MinValueValidator(1)], verbose_name='ترتيب تعميد الشراء'),
        ),
        migrations.AlterField(
            model_name='jobtitlepermission',
            name='store_approval_order',
            field=models.PositiveSmallIntegerField(blank=True, null=True, validators=[MinValueValidator(1)], verbose_name='ترتيب تعميد الصرف'),
        ),
        migrations.AddConstraint(
            model_name='approvallevel',
            constraint=models.UniqueConstraint(fields=('workflow_type', 'order_number'), name='unique_approval_level_order_per_workflow'),
        ),
        migrations.AddConstraint(
            model_name='approvallevel',
            constraint=models.UniqueConstraint(fields=('workflow_type', 'job_title'), name='unique_approval_level_job_title_per_workflow'),
        ),
        migrations.AddConstraint(
            model_name='approvaldelegation',
            constraint=models.UniqueConstraint(fields=('source_job_title', 'approval_level'), name='unique_approval_delegation_per_level'),
        ),
        migrations.RunPython(migrate_existing_approval_config, migrations.RunPython.noop),
    ]
