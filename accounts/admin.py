from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import AuditLog, JobTitle, JobTitlePermission, Profile, Specialty, SystemSettings

# Register your models here.


# تجعل بيانات البروفايل تظهر تحت بيانات المستخدم في نفس الصفحة
class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'بيانات إضافية (البروفايل)'

# تخصيص عرض المستخدم في الآدمن
class UserAdmin(BaseUserAdmin):
    inlines = (ProfileInline,)

# إعادة تسجيل موديل المستخدم مع الإضافات الجديدة
admin.site.unregister(User)
admin.site.register(User, UserAdmin)
admin.site.register(Profile)  # تسجيل البروفايل بشكل منفصل إذا أردت تعديله مباشرة من هنا 
admin.site.register(Specialty)
admin.site.register(JobTitle)
admin.site.register(JobTitlePermission)


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    list_display = ('system_name', 'organization_name', 'phone', 'email', 'updated_at')

    def has_add_permission(self, request):
        return not SystemSettings.objects.exists()


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'action_type', 'model_name', 'object_id', 'ip_address')
    list_filter = ('action_type', 'model_name', 'created_at')
    search_fields = ('user__username', 'model_name', 'object_repr', 'description')
    readonly_fields = (
        'user', 'action_type', 'model_name', 'object_id', 'object_repr',
        'old_data', 'new_data', 'description', 'ip_address', 'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
