from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


def migrate_roles_to_job_titles(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    JobTitle = apps.get_model('accounts', 'JobTitle')
    Profile = apps.get_model('accounts', 'Profile')

    for role in Role.objects.all():
        job_title, _ = JobTitle.objects.get_or_create(title_name=role.name)
        Profile.objects.filter(role_id=role.id).update(job_title_id=job_title.id)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_role_description_specialty_description'),
    ]

    operations = [
        migrations.CreateModel(
            name='JobTitle',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title_name', models.CharField(max_length=100, unique=True, verbose_name='المسمى الوظيفي')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name='JobTitlePermission',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('can_add_maintenance', models.BooleanField(default=False, verbose_name='إضافة طلب صيانة')),
                ('can_approve_maintenance', models.BooleanField(default=False, verbose_name='قبول/رفض وتكليف مهندس')),
                ('can_edit_pending_maintenance', models.BooleanField(default=False, verbose_name='تعديل طلب الصيانة (Pending)')),
                ('can_view_all_departments', models.BooleanField(default=False, verbose_name='عرض ومراقبة جميع الأقسام')),
                ('can_add_spare_parts', models.BooleanField(default=False, verbose_name='إضافة طلب قطع غيار')),
                ('can_edit_delete_pending_parts', models.BooleanField(default=False, verbose_name='تعديل/حذف طلب قطع غيار (Pending)')),
                ('can_approve_store_requisition', models.BooleanField(default=False, verbose_name='تعميد طلب صرف مخزني')),
                ('store_approval_order', models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(8)], verbose_name='ترتيب تعميد الصرف (1-8)')),
                ('can_bypass_store_approval', models.BooleanField(default=False, verbose_name='صلاحية تجاوز التعميدات السابقة')),
                ('max_store_bypass_order', models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='أقصى رقم ترتيب يمكن تجاوزه')),
                ('can_approve_purchase_order', models.BooleanField(default=False, verbose_name='تعميد طلب الشراء')),
                ('purchase_approval_order', models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(8)], verbose_name='ترتيب تعميد الشراء (1-8)')),
                ('can_bypass_purchase_approval', models.BooleanField(default=False, verbose_name='صلاحية تجاوز تعميدات الشراء السابقة')),
                ('max_purchase_bypass_order', models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='أقصى رقم ترتيب شراء يمكن تجاوزه')),
                ('can_issue_from_store', models.BooleanField(default=False, verbose_name='صرف من المخازن')),
                ('can_confirm_issuance', models.BooleanField(default=False, verbose_name='تأكيد عملية الصرف')),
                ('can_add_achievement_report', models.BooleanField(default=False, verbose_name='إضافة تقرير إنجاز')),
                ('can_edit_delete_pending_report', models.BooleanField(default=False, verbose_name='تعديل/حذف تقرير إنجاز (Pending)')),
                ('can_approve_achievement_report', models.BooleanField(default=False, verbose_name='تعميد تقرير الإنجاز')),
                ('achievement_approval_order', models.PositiveSmallIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(5)], verbose_name='ترتيب تعميد التقرير (1-5)')),
                ('can_bypass_achievement_approval', models.BooleanField(default=False, verbose_name='صلاحية تجاوز تعميدات التقرير السابقة')),
                ('max_achievement_bypass_order', models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='أقصى رقم ترتيب تقرير يمكن تجاوزه')),
                ('job_title', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='permissions', to='accounts.jobtitle')),
            ],
        ),
        migrations.AddField(
            model_name='profile',
            name='job_title',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='job_titles_profiles', to='accounts.jobtitle', verbose_name='المسمى الوظيفي'),
        ),
        migrations.RunPython(migrate_roles_to_job_titles, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='profile',
            name='role',
        ),
        migrations.DeleteModel(
            name='Role',
        ),
    ]
