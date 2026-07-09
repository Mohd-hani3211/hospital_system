from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0018_jobtitlepermission_granular_system_permissions'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserPermissionOverride',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('permission_name', models.CharField(db_index=True, max_length=120, verbose_name='اسم الصلاحية')),
                ('action', models.CharField(choices=[('grant', 'منح'), ('deny', 'سحب')], db_index=True, max_length=10, verbose_name='نوع التعديل')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الإنشاء')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='آخر تحديث')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='permission_overrides', to=settings.AUTH_USER_MODEL, verbose_name='المستخدم')),
            ],
            options={
                'verbose_name': 'صلاحية خاصة بالمستخدم',
                'verbose_name_plural': 'صلاحيات خاصة بالمستخدمين',
                'ordering': ['user__username', 'permission_name', 'action'],
                'indexes': [models.Index(fields=['user', 'permission_name'], name='accounts_us_user_id_52f852_idx')],
                'constraints': [models.UniqueConstraint(fields=('user', 'permission_name', 'action'), name='unique_user_permission_override_action')],
            },
        ),
    ]
