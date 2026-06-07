from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_jobtitle_jobtitlepermission_profile_job_title_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='jobtitlepermission',
            name='is_department_manager',
            field=models.BooleanField(default=False, verbose_name='رئيس قسم'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='is_engineer',
            field=models.BooleanField(default=False, verbose_name='مهندس'),
        ),
    ]
