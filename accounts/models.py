from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.core.validators import MaxValueValidator, MinValueValidator
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


class JobTitlePermission(models.Model):
    job_title = models.OneToOneField(JobTitle, on_delete=models.CASCADE, related_name='permissions')

    can_manage_system_setup = models.BooleanField(default=False, verbose_name="Superuser / مسؤول تهيئة النظام")
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
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(8)],
        verbose_name="ترتيب تعميد الصرف (1-8)",
    )
    can_bypass_store_approval = models.BooleanField(default=False, verbose_name="صلاحية تجاوز التعميدات السابقة")
    max_store_bypass_order = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="أقصى رقم ترتيب يمكن تجاوزه")
    can_approve_purchase_order = models.BooleanField(default=False, verbose_name="تعميد طلب الشراء")
    purchase_approval_order = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(8)],
        verbose_name="ترتيب تعميد الشراء (1-8)",
    )
    can_bypass_purchase_approval = models.BooleanField(default=False, verbose_name="صلاحية تجاوز تعميدات الشراء السابقة")
    max_purchase_bypass_order = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name="أقصى رقم ترتيب شراء يمكن تجاوزه")
    can_issue_from_store = models.BooleanField(default=False, verbose_name="صرف من المخازن")
    can_confirm_issuance = models.BooleanField(default=False, verbose_name="تأكيد عملية الصرف")

    can_add_achievement_report = models.BooleanField(default=False, verbose_name="إضافة تقرير إنجاز")
    can_edit_delete_pending_report = models.BooleanField(default=False, verbose_name="تعديل/حذف تقرير إنجاز (Pending)")
    can_approve_achievement_report = models.BooleanField(default=False, verbose_name="تعميد تقرير الإنجاز")
    achievement_approval_order = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(5)],
        verbose_name="ترتيب تعميد التقرير (1-5)",
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

        structures = (
            ('can_approve_store_requisition', 'store_approval_order', 'can_bypass_store_approval', 'max_store_bypass_order', 'الصرف المخزني'),
            ('can_approve_purchase_order', 'purchase_approval_order', 'can_bypass_purchase_approval', 'max_purchase_bypass_order', 'طلب الشراء'),
            ('can_approve_achievement_report', 'achievement_approval_order', 'can_bypass_achievement_approval', 'max_achievement_bypass_order', 'تقرير الإنجاز'),
        )
        for approve_field, order_field, bypass_field, max_bypass_field, label in structures:
            if not getattr(self, approve_field):
                setattr(self, order_field, None)
                setattr(self, bypass_field, False)
                setattr(self, max_bypass_field, None)
                continue

            order = getattr(self, order_field)
            bypass = getattr(self, bypass_field)
            max_bypass = getattr(self, max_bypass_field)
            if not order:
                raise ValidationError(f"يجب تحديد رقم ترتيب التعميد لـ {label}.")
            if bypass:
                if order < 2:
                    raise ValidationError(f"لا يمكن تفعيل التجاوز لـ {label} عند الترتيب الأول.")
                if not max_bypass:
                    raise ValidationError(f"يجب تحديد أقصى ترتيب يمكن تجاوزه لـ {label}.")
                if max_bypass >= order:
                    raise ValidationError(f"أقصى ترتيب يمكن تجاوزه لـ {label} يجب أن يكون أقل من ترتيبك الحالي.")
            else:
                setattr(self, max_bypass_field, None)

        unique_orders = (
            ('can_approve_store_requisition', 'store_approval_order', 'الصرف المخزني'),
            ('can_approve_purchase_order', 'purchase_approval_order', 'طلب الشراء'),
            ('can_approve_achievement_report', 'achievement_approval_order', 'تقرير الإنجاز'),
        )
        for approve_field, order_field, label in unique_orders:
            order = getattr(self, order_field)
            if getattr(self, approve_field) and order:
                duplicate = JobTitlePermission.objects.filter(**{order_field: order}).exclude(pk=self.pk).select_related('job_title').first()
                print(f"the duplicate: {duplicate} ")
                if duplicate is not None:
                    raise ValidationError(f"رقم ترتيب تعميد {label} ({order}) محجوز مسبقاً للمسمى الوظيفي '{duplicate}'.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
    def __str__(self):
        return self.job_title.title_name if self.job_title else 'بدون مسمى وظيفي'


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    job_title = models.ForeignKey(JobTitle, on_delete=models.SET_NULL, null=True, related_name='job_titles_profiles', verbose_name="المسمى الوظيفي")
    specialty = models.ForeignKey(Specialty, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="التخصص (للفنيين فقط)")
    managing_department = models.OneToOneField(Department, verbose_name="رئيس القسم", related_name='managed_by', on_delete=models.SET_NULL, null=True, blank=True)
    phone_number = models.CharField(max_length=15, verbose_name="رقم الهاتف")
    employee_id = models.CharField(max_length=20, unique=True, verbose_name="الرقم الوظيفي")

    def clean(self):
        super().clean()
        if not self.job_title:
            raise ValidationError({'job_title': "يجب اختيار مسمى وظيفي."})

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
