from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0013_dynamic_approval_hierarchy'),
        ('maintenance', '0025_alter_notification_notification_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='completereportapproval',
            name='approval_level',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='complete_report_approvals', to='accounts.approvallevel'),
        ),
        migrations.AddField(
            model_name='sparepartapproval',
            name='approval_level',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='spare_part_approvals', to='accounts.approvallevel'),
        ),
        migrations.AlterUniqueTogether(
            name='completereportapproval',
            unique_together=set(),
        ),
        migrations.AlterUniqueTogether(
            name='sparepartapproval',
            unique_together=set(),
        ),
        migrations.AlterField(
            model_name='complete_report',
            name='acceptance',
            field=models.IntegerField(default=0, validators=[MinValueValidator(0)], verbose_name=' مستوائ التعميد'),
        ),
        migrations.AlterField(
            model_name='sparepartrequest',
            name='acceptance',
            field=models.IntegerField(default=0, validators=[MinValueValidator(0)], verbose_name=' مستوائ التعميد'),
        ),
        migrations.AlterField(
            model_name='spareparts',
            name='acceptance',
            field=models.IntegerField(default=0, validators=[MinValueValidator(0)], verbose_name=' مستوائ التعميد'),
        ),
        migrations.AlterField(
            model_name='spareparts',
            name='Issued_confirmation',
            field=models.IntegerField(default=0, validators=[MinValueValidator(-1), MaxValueValidator(1)], verbose_name=' مستوائ التعميد'),
        ),
        migrations.AddConstraint(
            model_name='sparepartapproval',
            constraint=models.UniqueConstraint(condition=models.Q(approval_level__isnull=False), fields=('request', 'approval_level'), name='unique_spare_part_approval_level_per_request'),
        ),
        migrations.AddConstraint(
            model_name='completereportapproval',
            constraint=models.UniqueConstraint(condition=models.Q(approval_level__isnull=False), fields=('report', 'approval_level'), name='unique_complete_report_approval_level_per_report'),
        ),
    ]
