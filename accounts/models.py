from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.core.validators import MinValueValidator
from django.db import models

from hospital_units.models import Department


class Specialty(models.Model):
    name = models.CharField(max_length=50, verbose_name="اسم التخصص")
    description = models.CharField(max_length=50, null=True, verbose_name="وصف التخصص")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "تخصصات الفنيين"


class JobTitle(models.Model):
    title_name = models.CharField(max_length=100, unique=True, verbose_name="المسمى الوظيفي")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title_name


class ApprovalWorkflow:
    PURCHASE = 'purchase'
    STORE_ISSUE = 'store_issue'
    ACHIEVEMENT_REPORT = 'achievement_report'

    CHOICES = (
        (PURCHASE, 'Spare part purchase approval'),
        (STORE_ISSUE, 'Spare part store issue approval'),
        (ACHIEVEMENT_REPORT, 'Achievement report approval'),
    )

    LABELS = dict(CHOICES)


class ApprovalLevel(models.Model):
    workflow_type = models.CharField(max_length=40, choices=ApprovalWorkflow.CHOICES, db_index=True)
    order_number = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    job_title = models.ForeignKey(JobTitle, on_delete=models.PROTECT, related_name='approval_levels')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['workflow_type', 'order_number']
        constraints = [
            models.UniqueConstraint(
                fields=['workflow_type', 'order_number'],
                name='unique_approval_level_order_per_workflow',
            ),
        ]

    def __str__(self):
        workflow_label = ApprovalWorkflow.LABELS.get(self.workflow_type, self.workflow_type)
        return f"{workflow_label} - Level {self.order_number}: {self.job_title}"


class ApprovalDelegation(models.Model):
    source_job_title = models.ForeignKey(JobTitle, on_delete=models.CASCADE, related_name='approval_delegations')
    approval_level = models.ForeignKey(ApprovalLevel, on_delete=models.CASCADE, related_name='delegations')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['approval_level__workflow_type', 'approval_level__order_number', 'source_job_title__title_name']
        constraints = [
            models.UniqueConstraint(
                fields=['source_job_title', 'approval_level'],
                name='unique_approval_delegation_per_level',
            ),
        ]

    def __str__(self):
        return f"{self.source_job_title} can approve {self.approval_level}"


class JobTitlePermission(models.Model):
    job_title = models.OneToOneField(JobTitle, on_delete=models.CASCADE, related_name='permissions')

    can_manage_system_setup = models.BooleanField(default=False, verbose_name="Superuser / مسؤول تهيئة النظام")
    can_manage_system_settings = models.BooleanField(default=False, verbose_name="إدارة إعدادات النظام")
    can_manage_user_permissions = models.BooleanField(default=False, verbose_name="إدارة صلاحيات المستخدمين الخاصة")

    can_view_employees = models.BooleanField(default=False, verbose_name="عرض الموظفين")
    can_add_employees = models.BooleanField(default=False, verbose_name="إضافة الموظفين")
    can_edit_employees = models.BooleanField(default=False, verbose_name="تعديل الموظفين")
    can_delete_employees = models.BooleanField(default=False, verbose_name="حذف الموظفين")

    can_view_job_titles = models.BooleanField(default=False, verbose_name="عرض المسميات الوظيفية")
    can_add_job_titles = models.BooleanField(default=False, verbose_name="إضافة المسميات الوظيفية")
    can_edit_job_titles_permissions = models.BooleanField(default=False, verbose_name="تعديل صلاحيات المسميات الوظيفية")
    can_delete_job_titles = models.BooleanField(default=False, verbose_name="حذف المسميات الوظيفية")

    can_view_organization_structure = models.BooleanField(default=False, verbose_name="عرض الهيكل التنظيمي")
    can_manage_departments = models.BooleanField(default=False, verbose_name="إدارة الأقسام")
    can_manage_buildings = models.BooleanField(default=False, verbose_name="إدارة المباني")
    can_manage_floors = models.BooleanField(default=False, verbose_name="إدارة الطوابق")
    can_manage_engineer_specialties = models.BooleanField(default=False, verbose_name="إدارة تخصصات المهندسين")

    can_view_audit_logs = models.BooleanField(default=False, verbose_name="عرض سجلات العمليات")
    can_manage_backups = models.BooleanField(default=False, verbose_name="إدارة النسخ الاحتياطية")
    can_view_audit_log = models.BooleanField(default=False, verbose_name="عرض سجل العمليات")

    is_department_manager = models.BooleanField(default=False, verbose_name="رئيس قسم")
    is_engineer = models.BooleanField(default=False, verbose_name="مهندس")

    can_add_maintenance = models.BooleanField(default=False, verbose_name="إضافة طلب صيانة")
    can_approve_maintenance = models.BooleanField(default=False, verbose_name="قبول/رفض وتكليف مهندس")
    can_edit_pending_maintenance = models.BooleanField(default=False, verbose_name="تعديل طلب الصيانة (Pending)")
    can_view_all_departments = models.BooleanField(default=False, verbose_name="عرض ومراقبة جميع الأقسام")
    can_view_dashboard = models.BooleanField(default=False, verbose_name="عرض لوحة التحكم والتحليلات")

    can_add_spare_parts = models.BooleanField(default=False, verbose_name="إضافة طلب قطع غيار")
    can_edit_delete_pending_parts = models.BooleanField(default=False, verbose_name="تعديل/حذف طلب قطع غيار (Pending)")
    can_approve_store_requisition = models.BooleanField(default=False, verbose_name="تعميد طلب صرف مخزني")
    store_approval_order = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)],
        verbose_name="ترتيب تعميد الصرف",
    )
    can_bypass_store_approval = models.BooleanField(default=False, verbose_name="صلاحية تجاوز التعميدات السابقة")
    max_store_bypass_order = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="أقصى رقم ترتيب يمكن تجاوزه")
    can_approve_purchase_order = models.BooleanField(default=False, verbose_name="تعميد طلب الشراء")
    purchase_approval_order = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)],
        verbose_name="ترتيب تعميد الشراء",
    )
    can_bypass_purchase_approval = models.BooleanField(default=False, verbose_name="صلاحية تجاوز تعميدات الشراء السابقة")
    max_purchase_bypass_order = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="أقصى رقم ترتيب شراء يمكن تجاوزه")
    can_issue_from_store = models.BooleanField(default=False, verbose_name="صرف من المخازن")
    can_confirm_issuance = models.BooleanField(default=False, verbose_name="تأكيد عملية الصرف")

    can_add_achievement_report = models.BooleanField(default=False, verbose_name="إضافة تقرير إنجاز")
    can_edit_delete_pending_report = models.BooleanField(default=False, verbose_name="تعديل/حذف تقرير إنجاز (Pending)")
    can_approve_achievement_report = models.BooleanField(default=False, verbose_name="تعميد تقرير الإنجاز")
    achievement_approval_order = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1)],
        verbose_name="ترتيب تعميد التقرير",
    )
    can_bypass_achievement_approval = models.BooleanField(default=False, verbose_name="صلاحية تجاوز تعميدات التقرير السابقة")
    max_achievement_bypass_order = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="أقصى رقم ترتيب تقرير يمكن تجاوزه")

    def clean(self):
        super().clean()
        if self.is_department_manager and self.is_engineer:
            raise ValidationError("لا يمكن للمسمى الوظيفي الواحد أن يكون رئيس قسم ومهندس في نفس الوقت.")

        if self.is_department_manager:
            duplicate_manager = JobTitlePermission.objects.filter(
                is_department_manager=True,
            ).exclude(pk=self.pk).select_related('job_title').first()
            if duplicate_manager:
                raise ValidationError(
                    f"يوجد مسمى وظيفي آخر محدد كرئيس قسم: {duplicate_manager.job_title.title_name}. "
                    "لا يمكن تكرار هذا الدور."
                )

        if self.is_engineer:
            duplicate_engineer = JobTitlePermission.objects.filter(
                is_engineer=True,
            ).exclude(pk=self.pk).select_related('job_title').first()
            if duplicate_engineer:
                raise ValidationError(
                    f"يوجد مسمى وظيفي آخر محدد كمهندس: {duplicate_engineer.job_title.title_name}. "
                    "لا يمكن تكرار هذا الدور."
                )

        if self.is_department_manager:
            self.can_add_maintenance = True
            self.can_edit_pending_maintenance = True
            self.can_approve_store_requisition = True
            self.can_approve_purchase_order = True
            self.can_approve_achievement_report = True

        if self.is_engineer:
            self.can_add_spare_parts = True
            self.can_edit_delete_pending_parts = True
            self.can_confirm_issuance = True
            self.can_add_achievement_report = True
            self.can_edit_delete_pending_report = True

        if self.can_approve_maintenance:
            self.can_view_all_departments = True

        approval_structures = (
            ('can_approve_store_requisition', 'store_approval_order', 'can_bypass_store_approval', 'max_store_bypass_order'),
            ('can_approve_purchase_order', 'purchase_approval_order', 'can_bypass_purchase_approval', 'max_purchase_bypass_order'),
            ('can_approve_achievement_report', 'achievement_approval_order', 'can_bypass_achievement_approval', 'max_achievement_bypass_order'),
        )
        for approve_field, order_field, bypass_field, max_bypass_field in approval_structures:
            if not getattr(self, approve_field):
                setattr(self, order_field, None)
                setattr(self, bypass_field, False)
                setattr(self, max_bypass_field, None)
            elif not getattr(self, bypass_field):
                setattr(self, max_bypass_field, None)
        return

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
    def __str__(self):
        return self.job_title.title_name if self.job_title else 'بدون مسمى وظيفي'


class UserPermissionOverride(models.Model):
    ACTION_GRANT = 'grant'
    ACTION_DENY = 'deny'
    ACTION_CHOICES = (
        (ACTION_GRANT, 'منح'),
        (ACTION_DENY, 'سحب'),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='permission_overrides', verbose_name="المستخدم")
    permission_name = models.CharField(max_length=120, db_index=True, verbose_name="اسم الصلاحية")
    action = models.CharField(max_length=10, choices=ACTION_CHOICES, db_index=True, verbose_name="نوع التعديل")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخر تحديث")

    class Meta:
        verbose_name = "صلاحية خاصة بالمستخدم"
        verbose_name_plural = "صلاحيات خاصة بالمستخدمين"
        ordering = ['user__username', 'permission_name', 'action']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'permission_name', 'action'],
                name='unique_user_permission_override_action',
            ),
        ]
        indexes = [
            models.Index(fields=['user', 'permission_name']),
        ]

    @staticmethod
    def allowed_permission_names():
        return {
            field.name
            for field in JobTitlePermission._meta.fields
            if isinstance(field, models.BooleanField)
        }

    @staticmethod
    def is_approval_override_name(permission_name):
        prefix, separator, value = permission_name.partition(':')
        if separator != ':' or prefix not in ('approval_level', 'approval_delegation'):
            return False
        return value.isdigit() and ApprovalLevel.objects.filter(pk=int(value)).exists()

    def clean(self):
        super().clean()
        if (
            self.permission_name not in self.allowed_permission_names()
            and not self.is_approval_override_name(self.permission_name)
        ):
            raise ValidationError({'permission_name': "اسم الصلاحية غير موجود في صلاحيات المسمى الوظيفي."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} - {self.permission_name} - {self.action}"


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    job_title = models.ForeignKey(JobTitle, on_delete=models.SET_NULL, null=True, related_name='job_titles_profiles', verbose_name="المسمى الوظيفي")
    specialty = models.ForeignKey(Specialty, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="التخصص (للفنيين فقط)")
    managing_department = models.OneToOneField(Department, verbose_name="رئيس القسم", related_name='managed_by', on_delete=models.SET_NULL, null=True, blank=True)
    phone_number = models.CharField(max_length=15, verbose_name="رقم الهاتف")
    employee_id = models.CharField(max_length=20, unique=True, null=True, blank=True, verbose_name="الرقم الوظيفي")

    def clean(self):
        super().clean()
        if not self.job_title:
            raise ValidationError({'job_title': "يجب اختيار مسمى وظيفي."})
        if self.job_title.permissions.is_department_manager:
            if not self.managing_department:
                raise ValidationError({'managing_department': "يجب اختيار قسم لهذا الموظف لأنه رئيس قسم."})
            elif Profile.objects.filter(managing_department=self.managing_department).exclude(pk=self.pk).exists():
                raise ValidationError({'managing_department': "هذا القسم لديه بالفعل رئيس قسم معين."})
        if self.job_title.permissions.is_engineer and not self.specialty:
            raise ValidationError({'specialty': "يجب اختيار تخصص لهذا الموظف لأنه مهندس."})

    def __str__(self):
        job_title = self.job_title.title_name if self.job_title else 'بدون مسمى وظيفي'
        return f"{self.user.get_full_name()} - {job_title}"

    class Meta:
        permissions = [
            ('can_manage_system_setup', 'Can manage system setup'),
            ('can_view_audit_log', 'Can view audit log'),
        ]


class AuditLog(models.Model):
    ACTION_CREATE = 'create'
    ACTION_UPDATE = 'update'
    ACTION_DELETE = 'delete'
    ACTION_APPROVE = 'approve'
    ACTION_REJECT = 'reject'
    ACTION_ASSIGN = 'assign'
    ACTION_LOGIN = 'login'
    ACTION_LOGOUT = 'logout'
    ACTION_PASSWORD_CHANGE = 'password_change'
    ACTION_STATUS_CHANGE = 'status_change'
    ACTION_BACKUP_CREATE = 'backup_create'
    ACTION_BACKUP_DOWNLOAD = 'backup_download'
    ACTION_BACKUP_DELETE = 'backup_delete'

    ACTION_CHOICES = [
        (ACTION_CREATE, 'إنشاء'),
        (ACTION_UPDATE, 'تعديل'),
        (ACTION_DELETE, 'حذف'),
        (ACTION_APPROVE, 'اعتماد'),
        (ACTION_REJECT, 'رفض'),
        (ACTION_ASSIGN, 'تكليف'),
        (ACTION_LOGIN, 'تسجيل دخول'),
        (ACTION_LOGOUT, 'تسجيل خروج'),
        (ACTION_PASSWORD_CHANGE, 'تغيير كلمة مرور'),
        (ACTION_STATUS_CHANGE, 'تغيير حالة'),
        (ACTION_BACKUP_CREATE, 'إنشاء نسخة احتياطية'),
        (ACTION_BACKUP_DOWNLOAD, 'تحميل نسخة احتياطية'),
        (ACTION_BACKUP_DELETE, 'حذف نسخة احتياطية'),
    ]

    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs', verbose_name="المستخدم")
    action_type = models.CharField(max_length=40, choices=ACTION_CHOICES, db_index=True, verbose_name="نوع العملية")
    model_name = models.CharField(max_length=120, db_index=True, verbose_name="اسم النموذج")
    object_id = models.CharField(max_length=120, blank=True, verbose_name="معرف الكائن")
    object_repr = models.CharField(max_length=255, blank=True, verbose_name="وصف الكائن")
    old_data = models.JSONField(null=True, blank=True, verbose_name="البيانات القديمة")
    new_data = models.JSONField(null=True, blank=True, verbose_name="البيانات الجديدة")
    description = models.TextField(blank=True, verbose_name="الوصف")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="عنوان IP")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="تاريخ العملية")

    class Meta:
        verbose_name = "سجل عملية"
        verbose_name_plural = "سجل العمليات"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['user']),
            models.Index(fields=['action_type']),
            models.Index(fields=['model_name']),
        ]
        permissions = [
            ('can_view_audit_log', 'Can view audit log'),
        ]

    def __str__(self):
        return f"{self.get_action_type_display()} - {self.model_name} #{self.object_id}"


class SystemSettings(models.Model):
    system_name = models.CharField(max_length=150, default="طلبات الصيانة", verbose_name="اسم النظام")
    organization_name = models.CharField(max_length=200, blank=True, verbose_name="اسم الجهة")
    logo = models.ImageField(upload_to='system/logos/', null=True, blank=True, verbose_name="شعار النظام")
    phone = models.CharField(max_length=30, blank=True, verbose_name="رقم الهاتف")
    email = models.EmailField(blank=True, verbose_name="البريد الإلكتروني")
    address = models.CharField(max_length=255, blank=True, verbose_name="العنوان")
    footer_text = models.CharField(max_length=255, blank=True, verbose_name="نص الفوتر")
    maintenance_policy_notes = models.TextField(blank=True, verbose_name="ملاحظات سياسة الصيانة")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاريخ آخر تحديث")

    class Meta:
        verbose_name = "إعدادات النظام"
        verbose_name_plural = "إعدادات النظام"

    def clean(self):
        super().clean()
        if not self.pk and SystemSettings.objects.exists():
            raise ValidationError("لا يمكن إنشاء أكثر من سجل واحد لإعدادات النظام.")

    def save(self, *args, **kwargs):
        self.full_clean()
        result = super().save(*args, **kwargs)
        cache.delete('hmms_system_settings')
        return result

    @classmethod
    def get_solo(cls):
        existing = cls.objects.order_by('pk').first()
        if existing:
            return existing
        return cls(pk=1, system_name="نظام إدارة الصيانة الطبية")

    def __str__(self):
        return self.system_name
