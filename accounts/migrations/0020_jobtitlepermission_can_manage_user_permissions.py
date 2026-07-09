from django.db import migrations, models


def preserve_system_admin_user_permission_access(apps, _schema_editor):
    JobTitlePermission = apps.get_model('accounts', 'JobTitlePermission')
    JobTitlePermission.objects.filter(can_manage_system_setup=True).update(
        can_manage_user_permissions=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0019_userpermissionoverride'),
    ]

    operations = [
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_manage_user_permissions',
            field=models.BooleanField(default=False, verbose_name='إدارة صلاحيات المستخدمين الخاصة'),
        ),
        migrations.RunPython(
            preserve_system_admin_user_permission_access,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
