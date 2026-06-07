from django.db import models
from django.contrib.auth.models import User
from hospital_units.models import Department
from accounts.models import Profile, Specialty
from django.core.validators import MaxValueValidator, MinValueValidator


# Create your models here.

class MaintenanceRequest(models.Model):
    # خيارات حالة الطلب
    STATUS_CHOICES = [
        ('pending', 'قيد الانتظار'),
        ('assigned', 'تم التعيين (بانتظار الفني)'),

        ('accept_assignment_start_progress', 'بدا التنفيذ'),
        ('reject_assignment', 'رفض التكليف'),

        ('awaiting_parts', 'قيد التنفيذ (بانتظار قطع غيار)'),
        ('spare_parts_rejected', 'تم رفض قطع الغيار'),

        ('obtained_parts', 'قيد التنفيذ (تم الحصول على قطع غيار)'),

        ('awaiting_completed_report_acceptenace', 'بانتظار تعميد تقرير الانجاز'),
        ('completed_report_rejected', 'تم رفض تقرير الانجاز'),

        ('completed', 'مكتمل'),
        ('rejected', 'مرفوض/ملغى'),
    ]

    # خيارات الأولوية
    PRIORITY_CHOICES = [
        ('critical', 'حرجة (طوارئ فورية)'),
        ('high', 'عالية (مستعجلة)'),
        ('medium', 'متوسطة'),
        ('low', 'منخفضة (روتينية)'),
    ]

    # 1. تفاصيل المشكلة
    description = models.TextField(verbose_name="وصف العطل بالتفصيل")
    
    # 2. معلومات المصدر (من طلب الصيانة وأين؟)
    requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name='submitted_requests', blank=True, verbose_name="مقدم الطلب")
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='maintenance_requests' ,null=False , blank=False, verbose_name="القسم المعطل")
    
    # 3. التوجيه الفني (لاختيار التخصص والفني المناسب)
    required_specialty = models.ForeignKey(Specialty, on_delete=models.CASCADE, null=False, verbose_name="التخصص المطلوب للصيانة")
    assigned_technician = models.ForeignKey(
        Profile, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        limit_choices_to={'specialty__isnull': False},
        related_name='assigned_tasks', 
        verbose_name="الفني المكلف"
    )


    # 4. حالة الطلب ووقته
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='pending', verbose_name="حالة الطلب")
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium', verbose_name="الأولوية")
    
    # rejected fields
    is_rejected=models.BooleanField("رفض القطعه", default=False)
    cause=models.CharField("سبب الرفض", null= True , max_length=1000 ,blank=True)
    rejected_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='rejected_maintenance_requests', verbose_name="رفض بواسطة")
    rejected_reason = models.CharField("سبب الرفض", null=True, max_length=1000, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ الرفض")


    # التواريخ (تلقائية)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ تقديم الطلب")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاريخ آخر تحديث")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ الانتهاء")

    class Meta:
        verbose_name = "طلب صيانة"
        verbose_name_plural = "طلبات الصيانة"
        ordering = ['-created_at'] # الترتيب من الأحدث للأقدم افتراضياً
        permissions = [
            ('can_view_dashboard', 'Can view maintenance dashboard'),
        ]

    def __str__(self):
        return f"طلب رقم {self.id} - {self.description[:50]} ({self.department.name})"



class complete_report(models.Model):
    report_statuses = [
        ('awaiting_acceptenace', ' في انتظار التعميد'),
        ('accepted', 'تم القبول'),
        ('rejected', 'تم الرفض'),
    ]

    maintenance_request = models.ForeignKey(MaintenanceRequest, verbose_name="طلب الصيانه", related_name='completed_report',on_delete=models.CASCADE)

    # report fields
    complete_report = models.CharField("تقرير النجاح",null=True,blank=True , max_length=1000)
    status = models.CharField("الحالة", max_length=50, choices=report_statuses, default='awaiting_acceptenace')

    # rejected fields
    is_rejected=models.BooleanField("رفض تقرير الانجاز", default=False)
    cause=models.CharField("سبب الرفض", null= True , max_length=1000 ,blank=True)
    rejected_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='rejected_complete_reports', verbose_name="رفض بواسطة")
    rejected_reason = models.CharField("سبب الرفض", null=True, max_length=1000, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ الرفض")

    # rang between 0 and 5
    acceptance = models.IntegerField( validators=[MinValueValidator(0), MaxValueValidator(5)], default=0,verbose_name=" مستوائ التعميد")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ إنشاء التقرير")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاريخ آخر تحديث")



class SparePartRequest(models.Model):
        # خيارات حالة الطلب
    STATUS_CHOICES = [
        ('awaiting_for_accepenace', ' في انتظار التعميد'),

        ('waiting_purchase_accepted_parts', 'في انتضار شراء القطغ المقبوله'),

        ('bought_available_parts', 'تم شراء القطع المقبوله (في انتظار صرف القطع المقبوله)'),
        ('issue_available_parts_issued_waiting_confirmation', 'بانتظار صرف القطع المقبوله من المخزن'),

        ('avaliable_parts_issued_waiting_confirmation', 'تم صرف القطع المقبوله (في انتظار تأكيد استلام القطع)'),

        ('avaliable_parts_issued', 'تم صرف القطع المقبوله والتأكيد'),

        ('rejected', 'مرفوض/ملغى'),
    ]
    maintenance_request = models.ForeignKey(MaintenanceRequest, on_delete=models.CASCADE, related_name='spare_parts', verbose_name="طلب الصيانة")
    engineer = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, limit_choices_to={'specialty__isnull': False}, verbose_name="المهندس الذي طلب القطعة")

    order_kinds_choices=[
        ('store_requisition','طلب صرف مخزني'),
        ('purchase_requisition','طلب شراء'),
    ]

    order_kind=models.CharField(max_length=100, choices=order_kinds_choices , verbose_name="نوع الطلب")
    description = models.CharField(max_length=500,verbose_name=" الوصف")
    status = models.CharField(max_length=60, choices=STATUS_CHOICES, default='awaiting_for_accepenace', verbose_name="حالة طلب القطعة")


    cause=models.CharField("سبب الرفض", max_length=1000 , null=True , blank=True)
    rejected_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='rejected_spare_part_requests', verbose_name="رفض بواسطة")
    rejected_reason = models.CharField("سبب الرفض", max_length=1000, null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ الرفض")



    acceptance = models.IntegerField( validators=[MinValueValidator(0), MaxValueValidator(8)], default=0,verbose_name=" مستوائ التعميد")




    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ تقديم الطلب")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاريخ آخر تحديث")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="تاريخ الانتهاء")

    class Meta:
        verbose_name = "قطعة غيار مطلوبة"
        verbose_name_plural = "قطع الغيار المطلوبة"

    def __str__(self):
        return f"طلب قطع غيار رقم {self.id} - طلب صيانة رقم {self.maintenance_request.id}"
    
    
    def time_for_acceptance(self):
        return self.acceptance > 0 or self.status == 'awaiting_for_accepenace'
        

    # it return 1 if all parts are accepted, -1 if all parts are rejected, and 0 if some are accepted and some are rejected
    def is_accepted_all_part_or_some_none(self):
        parts = self.spare_parts.all()

        if parts.exists() is False:
            return None

        total_count = parts.count()
        accepted_count = parts.filter(acceptance__gt=0).exclude(is_rejected=True).count()
        rejected_count = parts.filter(is_rejected=True).count()

        # accepted all
        if accepted_count == total_count:
            return True
        
        elif rejected_count == total_count:
            return -1
        elif accepted_count > 0 and rejected_count > 0 :
            return 0
        else:
            return None

class SpareParts(models.Model):

    order = models.ForeignKey(SparePartRequest, on_delete=models.CASCADE, related_name='spare_parts', verbose_name="طلب القطعة")

    part_name = models.CharField(max_length=100, verbose_name="اسم قطعة الغيار")
    quantity = models.PositiveIntegerField(verbose_name="الكمية المطلوبة")

    acceptance = models.IntegerField( validators=[MinValueValidator(0), MaxValueValidator(8)], default=0,verbose_name=" مستوائ التعميد")

    # value 1 means confirmed, -1 means rejected, 0 means not confirmed
    Issued_confirmation= models.IntegerField( validators=[MinValueValidator(-1), MaxValueValidator(1)], default=0,verbose_name=" مستوائ التعميد")

    is_rejected=models.BooleanField("رفض القطعه", default=False)


class SparePartApproval(models.Model):
    DECISION_CHOICES = [
        ('accepted', 'تم القبول'),
        ('rejected', 'تم الرفض'),
    ]

    request = models.ForeignKey(SparePartRequest, on_delete=models.CASCADE, related_name='approvals', verbose_name="طلب قطع الغيار")
    approver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='spare_part_approvals', verbose_name="المعمد")
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES, verbose_name="القرار")
    reason = models.CharField(max_length=1000, null=True, blank=True, verbose_name="سبب الرفض")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ القرار")

    class Meta:
        verbose_name = "تعميد طلب قطع غيار"
        verbose_name_plural = "تعميدات طلبات قطع الغيار"
        unique_together = ('request', 'approver')


class CompleteReportApproval(models.Model):
    DECISION_CHOICES = [
        ('accepted', 'تم القبول'),
        ('rejected', 'تم الرفض'),
    ]

    report = models.ForeignKey(complete_report, on_delete=models.CASCADE, related_name='approvals', verbose_name="تقرير الإنجاز")
    approver = models.ForeignKey(User, on_delete=models.CASCADE, related_name='complete_report_approvals', verbose_name="المعمد")
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES, verbose_name="القرار")
    reason = models.CharField(max_length=1000, null=True, blank=True, verbose_name="سبب الرفض")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ القرار")

    class Meta:
        verbose_name = "تعميد تقرير إنجاز"
        verbose_name_plural = "تعميدات تقارير الإنجاز"
        unique_together = ('report', 'approver')


class Notification(models.Model):
    TYPE_CHOICES = [
        ('maintenance_needs_acceptance', 'طلب صيانة جديد يحتاج معالجة'),
        ('maintenance_assigned', 'تم تعيين طلب صيانة'),
        ('maintenance_rejected', 'تم رفض طلب صيانة'),
        ('assignment_rejected', 'رفض تكليف مهندس'),
        ('spare_part_needs_approval', 'طلب قطع غيار يحتاج تعميد'),
        ('spare_part_rejected', 'تم رفض طلب قطع غيار'),
        ('spare_part_purchase_ready', 'قطع غيار بانتظار التوفير'),
        ('spare_part_ready_to_issue', 'قطع غيار جاهزة للصرف'),
        ('spare_part_waiting_confirmation', 'قطع غيار بانتظار التأكيد'),
        ('spare_part_issued', 'تم صرف قطع الغيار'),
        ('complete_report_needs_approval', 'تقرير إنجاز يحتاج تعميد'),
        ('complete_report_rejected', 'تم رفض تقرير إنجاز'),
        ('maintenance_completed', 'اكتمل طلب الصيانة'),
        ('general', 'عام'),
    ]

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications', verbose_name="المستلم")
    title = models.CharField(max_length=255, verbose_name="عنوان الإشعار")
    message = models.TextField(verbose_name="نص الإشعار")
    notification_type = models.CharField(max_length=60, choices=TYPE_CHOICES, default='general', verbose_name="نوع الإشعار")
    related_maintenance_request = models.ForeignKey(
        MaintenanceRequest,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name="طلب الصيانة المرتبط",
    )
    related_spare_part_request = models.ForeignKey(
        SparePartRequest,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name="طلب قطع الغيار المرتبط",
    )
    related_complete_report = models.ForeignKey(
        complete_report,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name="تقرير الإنجاز المرتبط",
    )
    url = models.CharField(max_length=500, blank=True, verbose_name="رابط الإشعار")
    is_read = models.BooleanField(default=False, db_index=True, verbose_name="مقروء")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="تاريخ الإنشاء")

    class Meta:
        verbose_name = "إشعار"
        verbose_name_plural = "الإشعارات"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient']),
            models.Index(fields=['recipient', 'is_read']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.title} - {self.recipient}"
