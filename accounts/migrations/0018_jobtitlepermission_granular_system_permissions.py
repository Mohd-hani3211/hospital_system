from django.db import migrations, models


NEW_PERMISSION_FIELDS = [
    'can_manage_system_settings',
    'can_view_employees',
    'can_add_employees',
    'can_edit_employees',
    'can_delete_employees',
    'can_view_job_titles',
    'can_add_job_titles',
    'can_edit_job_titles_permissions',
    'can_delete_job_titles',
    'can_view_organization_structure',
    'can_manage_departments',
    'can_manage_buildings',
    'can_manage_floors',
    'can_manage_engineer_specialties',
    'can_view_audit_logs',
    'can_manage_backups',
]


def preserve_existing_system_setup_permissions(apps, _schema_editor):
    JobTitlePermission = apps.get_model('accounts', 'JobTitlePermission')
    update_values = {field: True for field in NEW_PERMISSION_FIELDS}
    JobTitlePermission.objects.filter(can_manage_system_setup=True).update(**update_values)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0017_alter_approvallevel_job_title'),
    ]

    operations = [
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_manage_system_settings',
            field=models.BooleanField(default=False, verbose_name='إدارة إعدادات النظام'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_view_employees',
            field=models.BooleanField(default=False, verbose_name='عرض الموظفين'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_add_employees',
            field=models.BooleanField(default=False, verbose_name='إضافة الموظفين'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_edit_employees',
            field=models.BooleanField(default=False, verbose_name='تعديل الموظفين'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_delete_employees',
            field=models.BooleanField(default=False, verbose_name='حذف الموظفين'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_view_job_titles',
            field=models.BooleanField(default=False, verbose_name='عرض المسميات الوظيفية'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_add_job_titles',
            field=models.BooleanField(default=False, verbose_name='إضافة المسميات الوظيفية'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_edit_job_titles_permissions',
            field=models.BooleanField(default=False, verbose_name='تعديل صلاحيات المسميات الوظيفية'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_delete_job_titles',
            field=models.BooleanField(default=False, verbose_name='حذف المسميات الوظيفية'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_view_organization_structure',
            field=models.BooleanField(default=False, verbose_name='عرض الهيكل التنظيمي'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_manage_departments',
            field=models.BooleanField(default=False, verbose_name='إدارة الأقسام'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_manage_buildings',
            field=models.BooleanField(default=False, verbose_name='إدارة المباني'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_manage_floors',
            field=models.BooleanField(default=False, verbose_name='إدارة الطوابق'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_manage_engineer_specialties',
            field=models.BooleanField(default=False, verbose_name='إدارة تخصصات المهندسين'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_view_audit_logs',
            field=models.BooleanField(default=False, verbose_name='عرض سجلات العمليات'),
        ),
        migrations.AddField(
            model_name='jobtitlepermission',
            name='can_manage_backups',
            field=models.BooleanField(default=False, verbose_name='إدارة النسخ الاحتياطية'),
        ),
        migrations.RunPython(
            preserve_existing_system_setup_permissions,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
