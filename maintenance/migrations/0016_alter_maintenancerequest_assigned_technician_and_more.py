from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_jobtitle_jobtitlepermission_profile_job_title_and_more'),
        ('maintenance', '0015_remove_sparepartrequest_is_available_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='maintenancerequest',
            name='assigned_technician',
            field=models.ForeignKey(blank=True, limit_choices_to={'specialty__isnull': False}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_tasks', to='accounts.profile', verbose_name='الفني المكلف'),
        ),
        migrations.AlterField(
            model_name='sparepartrequest',
            name='engineer',
            field=models.ForeignKey(blank=True, limit_choices_to={'specialty__isnull': False}, null=True, on_delete=django.db.models.deletion.SET_NULL, to='accounts.profile', verbose_name='المهندس الذي طلب القطعة'),
        ),
    ]
