from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0013_dynamic_approval_hierarchy'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='approvallevel',
            name='unique_approval_level_job_title_per_workflow',
        ),
    ]
