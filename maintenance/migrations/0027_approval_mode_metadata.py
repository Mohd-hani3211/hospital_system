from django.db import migrations, models
import django.db.models.deletion


def populate_existing_approval_mode(apps, _schema_editor):
    Profile = apps.get_model('accounts', 'Profile')
    SparePartApproval = apps.get_model('maintenance', 'SparePartApproval')
    CompleteReportApproval = apps.get_model('maintenance', 'CompleteReportApproval')

    def populate_for_model(model):
        for approval in model.objects.select_related('approval_level'):
            required_job_title_id = (
                approval.approval_level.job_title_id
                if approval.approval_level_id
                else None
            )
            approver_job_title_id = (
                Profile.objects.filter(user_id=approval.approver_id)
                .values_list('job_title_id', flat=True)
                .first()
            )
            is_delegated = bool(
                required_job_title_id
                and approver_job_title_id
                and approver_job_title_id != required_job_title_id
            )
            approval.required_job_title_id = required_job_title_id
            approval.approval_mode = 'delegated' if is_delegated else 'normal'
            approval.delegated_for_job_title_id = required_job_title_id if is_delegated else None
            approval.save(update_fields=[
                'required_job_title',
                'approval_mode',
                'delegated_for_job_title',
            ])

    populate_for_model(SparePartApproval)
    populate_for_model(CompleteReportApproval)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0014_remove_duplicate_approver_constraint'),
        ('maintenance', '0026_dynamic_approval_levels'),
    ]

    operations = [
        migrations.AddField(
            model_name='sparepartapproval',
            name='approval_mode',
            field=models.CharField(
                choices=[('normal', 'اعتماد مباشر'), ('delegated', 'اعتماد بالتفويض')],
                default='normal',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='sparepartapproval',
            name='delegated_for_job_title',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='delegated_spare_part_approvals',
                to='accounts.jobtitle',
            ),
        ),
        migrations.AddField(
            model_name='sparepartapproval',
            name='required_job_title',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='required_spare_part_approvals',
                to='accounts.jobtitle',
            ),
        ),
        migrations.AddField(
            model_name='completereportapproval',
            name='approval_mode',
            field=models.CharField(
                choices=[('normal', 'اعتماد مباشر'), ('delegated', 'اعتماد بالتفويض')],
                default='normal',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='completereportapproval',
            name='delegated_for_job_title',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='delegated_complete_report_approvals',
                to='accounts.jobtitle',
            ),
        ),
        migrations.AddField(
            model_name='completereportapproval',
            name='required_job_title',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='required_complete_report_approvals',
                to='accounts.jobtitle',
            ),
        ),
        migrations.RunPython(populate_existing_approval_mode, migrations.RunPython.noop),
    ]
