from django.contrib import admin
from django.utils import timezone
from .models import MaintenanceRequest, Notification

# Register your models here.

@admin.register(MaintenanceRequest)
class MaintenanceRequestAdmin(admin.ModelAdmin):
    # الحقول التي ستظهر في الجدول الرئيسي
    list_display = ('id', 'description', 'department', 'required_specialty', 'assigned_technician', 'status', 'priority', 'created_at')
    
    # فلاتر جانبية لتسهيل البحث
    list_filter = ('status', 'priority', 'required_specialty', 'department__floor__building')  # يمكنك إضافة المزيد من الفلاتر حسب الحاجة
    
    # حقول البحث
    search_fields = ( 'description', 'requester__username', 'department__name')
    
    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        if request.user.is_authenticated:
            initial['requester'] = request.user # تعيين مقدم الطلب تلقائياً إلى المستخدم الحالي
            
            if hasattr(request.user, 'profile') and request.user.profile.managing_department:
                initial['department'] = request.user.profile.managing_department  # تعيين القسم بناءً على اختيار المستخدم
        return initial

    # تنظيم الحقول داخل صفحة التعديل
    fieldsets = (
        ('معلومات القسم و مقدم الطلب', {
            'fields': ('requester', 'department','get_building_and_floor')  # حقل وهمي لعرض المبنى والطابق بناءً على القسم
        }),
        ('تفاصيل العطل', {
            'fields': ('required_specialty' , 'description', 'priority')
        }),
        ('التوجيه والإسناد', {
            'fields': ( 'assigned_technician', 'status')
        }),
        ('التتبع الزمني', {
            'fields': ('created_at', 'updated_at', 'completed_at'),
            'classes': ('collapse',), # جعلها مخفية وقابلة للتوسيع
        }),
    )
    
    readonly_fields = ('created_at', 'updated_at','completed_at','requester','department','get_building_and_floor') # جعل هذه الحقول للقراءة فقط لأنها تملأ تلقائياً

    def save_model(self, request, obj, form, change):
        # إذا تم تغيير الحالة إلى "مكتمل" ولم يكن مكتمل من قبل، قم بتعيين تاريخ الانتهاء
        if 'status' in form.changed_data and obj.status == 'completed' and not obj.completed_at:
            obj.completed_at = timezone.now()
        if not change:  # إذا كان هذا هو إنشاء طلب جديد
            # يمكنك إضافة أي إجراءات إضافية هنا، مثل إرسال إشعارات أو تسجيل الأحداث
            obj.requester = request.user  # تعيين مقدم الطلب تلقائياً إلى المستخدم الحالي
            if request.user.profile.managing_department:
                obj.department = request.user.profile.managing_department  # تأكد من تعيين القسم بناءً على اختيار المستخدم

        super().save_model(request, obj, form, change)


    def get_building_and_floor(self, obj):
        if obj.department and obj.department.floor and obj.department.floor.building:
            return f"{obj.department.floor.building.name} - {obj.department.floor.name}"
        return "غير محدد"
    get_building_and_floor.short_description = "المبنى والطابق"


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('id', 'recipient', 'title', 'notification_type', 'is_read', 'created_at')
    list_filter = ('notification_type', 'is_read', 'created_at')
    search_fields = ('title', 'message', 'recipient__username', 'recipient__first_name', 'recipient__last_name')
    readonly_fields = ('created_at',)
