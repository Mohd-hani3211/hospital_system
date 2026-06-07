from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from .audit_log_service import create_audit_log, serialize_instance
from .backup_service import create_database_backup, delete_backup_file, get_backup_path, list_backups
from maintenance.models import (
    CompleteReportApproval,
    MaintenanceRequest,
    Notification,
    SparePartApproval,
    SparePartRequest,
    complete_report as CompleteReport,
)
from .forms import (
    JobTitleForm,
    JobTitlePermissionForm,
    ProfileForm,
    SimplePasswordChangeForm,
    SpecialtyForm,
    SystemSettingsForm,
    UserForm,
)
from .models import AuditLog, JobTitle, JobTitlePermission, Specialty, SystemSettings
from .permissions import (
    ensure_at_least_one_system_admin_exists,
    require_system_setup_permission,
    user_can_manage_system_setup,
)


def is_system_admin(user):
    return user_can_manage_system_setup(user)


def user_can_view_audit_log(user):
    if not user.is_authenticated:
        return False
    if user.has_perm('accounts.can_view_audit_log'):
        return True
    try:
        return bool(user.profile.job_title.permissions.can_view_audit_log)
    except (AttributeError, JobTitlePermission.DoesNotExist):
        return False


def require_system_admin(request):
    require_system_setup_permission(request)


def require_audit_log_permission(request):
    if not user_can_view_audit_log(request.user):
        raise PermissionDenied("ليس لديك صلاحية الوصول إلى سجل العمليات.")


def get_job_permissions_map():
    job_permissions = {}
    for job in JobTitle.objects.all():
        try:
            permissions = job.permissions
        except JobTitlePermission.DoesNotExist:
            permissions = None

        job_permissions[str(job.id)] = {
            'is_manager': bool(permissions and permissions.is_department_manager),
            'is_engineer': bool(permissions and permissions.is_engineer),
        }
    return job_permissions


def employee_queryset():
    return User.objects.select_related(
        'profile',
        'profile__job_title',
        'profile__job_title__permissions',
        'profile__specialty',
        'profile__managing_department',
        'profile__managing_department__floor',
        'profile__managing_department__floor__building',
    ).prefetch_related('groups')


def get_employee_stats(employee):
    profile = getattr(employee, 'profile', None)
    assigned_requests = MaintenanceRequest.objects.none()
    spare_requests = SparePartRequest.objects.none()
    reports = CompleteReport.objects.none()

    if profile:
        assigned_requests = MaintenanceRequest.objects.filter(assigned_technician=profile)
        spare_requests = SparePartRequest.objects.filter(engineer=profile)
        reports = CompleteReport.objects.filter(maintenance_request__assigned_technician=profile)

    active_statuses = ('completed', 'rejected')
    return {
        'submitted_requests': MaintenanceRequest.objects.filter(requester=employee).count(),
        'assigned_requests': assigned_requests.count(),
        'completed_requests': assigned_requests.filter(status='completed').count(),
        'active_requests': assigned_requests.exclude(status__in=active_statuses).count(),
        'spare_part_requests': spare_requests.count(),
        'complete_reports': reports.count(),
        'spare_part_approvals': SparePartApproval.objects.filter(approver=employee).count(),
        'complete_report_approvals': CompleteReportApproval.objects.filter(approver=employee).count(),
        'total_approvals': (
            SparePartApproval.objects.filter(approver=employee).count()
            + CompleteReportApproval.objects.filter(approver=employee).count()
        ),
        'unread_notifications': Notification.objects.filter(recipient=employee, is_read=False).count(),
    }


def get_employee_role_data(employee):
    profile = getattr(employee, 'profile', None)
    job_title = profile.job_title if profile else None
    permissions = getattr(job_title, 'permissions', None) if job_title else None
    important_fields = (
        'can_manage_system_setup',
        'can_view_dashboard',
        'can_view_audit_log',
        'can_approve_maintenance',
        'can_add_maintenance',
        'can_add_spare_parts',
        'can_approve_store_requisition',
        'can_approve_purchase_order',
        'can_issue_from_store',
        'can_confirm_issuance',
        'can_add_achievement_report',
        'can_approve_achievement_report',
    )
    important_permissions = []
    if permissions:
        for field_name in important_fields:
            field = permissions._meta.get_field(field_name)
            important_permissions.append({
                'name': field_name,
                'label': field.verbose_name,
                'enabled': bool(getattr(permissions, field_name)),
            })

    role_labels = []
    if permissions and permissions.can_manage_system_setup:
        role_labels.append("مسؤول تهيئة النظام")
    if permissions and permissions.is_department_manager:
        role_labels.append("رئيس قسم")
    if permissions and permissions.is_engineer:
        role_labels.append("مهندس")
    if employee.is_superuser:
        role_labels.append("Superuser")
    if not role_labels:
        role_labels.append(job_title.title_name if job_title else "موظف")

    return {
        'permissions': permissions,
        'important_permissions': important_permissions,
        'role_labels': role_labels,
        'group_names': [group.name for group in employee.groups.all()],
        'can_manage_system_setup': user_can_manage_system_setup(employee),
    }


def get_employee_context(employee):
    notifications = Notification.objects.filter(recipient=employee).select_related(
        'related_maintenance_request',
        'related_spare_part_request',
        'related_complete_report',
    ).order_by('-created_at')[:5]
    return {
        'employee': employee,
        'profile_obj': getattr(employee, 'profile', None),
        'stats': get_employee_stats(employee),
        'role_data': get_employee_role_data(employee),
        'latest_notifications': notifications,
    }


class SystemAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    login_url = 'login'

    def test_func(self):
        return is_system_admin(self.request.user)

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        raise PermissionDenied("عذراً، لا تمتلك صلاحية إدارة هذه البيانات.")


class AuditLogRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    login_url = 'login'

    def test_func(self):
        return user_can_view_audit_log(self.request.user)

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        raise PermissionDenied("ليس لديك صلاحية الوصول إلى سجل العمليات.")


class AuditLogListView(AuditLogRequiredMixin, ListView):
    model = AuditLog
    template_name = 'audit_logs.html'
    context_object_name = 'audit_logs'
    paginate_by = 25

    def get_queryset(self):
        queryset = AuditLog.objects.select_related('user').order_by('-created_at')
        user_query = (self.request.GET.get('user') or '').strip()
        action_type = (self.request.GET.get('action_type') or '').strip()
        model_name = (self.request.GET.get('model_name') or '').strip()
        date_from = (self.request.GET.get('date_from') or '').strip()
        date_to = (self.request.GET.get('date_to') or '').strip()

        if user_query:
            queryset = queryset.filter(
                Q(user__username__icontains=user_query)
                | Q(user__first_name__icontains=user_query)
                | Q(user__last_name__icontains=user_query)
            )
        if action_type:
            queryset = queryset.filter(action_type=action_type)
        if model_name:
            queryset = queryset.filter(model_name__icontains=model_name)
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'سجل العمليات'
        context['filters'] = self.request.GET
        context['action_choices'] = AuditLog.ACTION_CHOICES
        context['model_names'] = AuditLog.objects.order_by('model_name').values_list('model_name', flat=True).distinct()
        return context


class AuditLogDetailView(AuditLogRequiredMixin, DetailView):
    model = AuditLog
    template_name = 'audit_log_detail.html'
    context_object_name = 'audit_log'

    def get_queryset(self):
        return AuditLog.objects.select_related('user')


@login_required(login_url='login')
def backup_list(request):
    require_system_admin(request)
    return render(request, 'backup_list.html', {
        'title': 'النسخ الاحتياطي',
        'backups': list_backups(),
    })


@login_required(login_url='login')
@require_POST
def create_backup(request):
    require_system_admin(request)
    backup_path = create_database_backup()
    create_audit_log(
        request.user,
        AuditLog.ACTION_BACKUP_CREATE,
        None,
        new_data={'filename': backup_path.name, 'size_bytes': backup_path.stat().st_size},
        description=f"تم إنشاء نسخة احتياطية: {backup_path.name}",
        request=request,
    )
    messages.success(request, "تم إنشاء النسخة الاحتياطية بنجاح.")
    return redirect('backup_list')


@login_required(login_url='login')
def download_backup(request, filename):
    require_system_admin(request)
    backup_path = get_backup_path(filename)
    if backup_path is None:
        raise Http404("النسخة الاحتياطية غير موجودة.")
    create_audit_log(
        request.user,
        AuditLog.ACTION_BACKUP_DOWNLOAD,
        None,
        new_data={'filename': backup_path.name, 'size_bytes': backup_path.stat().st_size},
        description=f"تم تحميل نسخة احتياطية: {backup_path.name}",
        request=request,
    )
    return FileResponse(
        backup_path.open('rb'),
        as_attachment=True,
        filename=backup_path.name,
        content_type='application/json',
    )


@login_required(login_url='login')
@require_POST
def delete_backup(request, filename):
    require_system_admin(request)
    backup_path = get_backup_path(filename)
    if backup_path is None:
        messages.error(request, "النسخة الاحتياطية غير موجودة أو غير صالحة.")
        return redirect('backup_list')
    size_bytes = backup_path.stat().st_size
    backup_name = backup_path.name
    if delete_backup_file(backup_name):
        create_audit_log(
            request.user,
            AuditLog.ACTION_BACKUP_DELETE,
            None,
            old_data={'filename': backup_name, 'size_bytes': size_bytes},
            description=f"تم حذف نسخة احتياطية: {backup_name}",
            request=request,
        )
        messages.success(request, "تم حذف النسخة الاحتياطية بنجاح.")
    else:
        messages.error(request, "تعذر حذف النسخة الاحتياطية.")
    return redirect('backup_list')


@login_required(login_url='login')
def system_settings_view(request):
    require_system_admin(request)
    settings_obj = SystemSettings.get_solo()
    old_data = serialize_instance(settings_obj)
    if request.method == 'POST':
        form = SystemSettingsForm(request.POST, request.FILES, instance=settings_obj)
        if form.is_valid():
            settings_obj = form.save()
            create_audit_log(
                request.user,
                AuditLog.ACTION_UPDATE,
                settings_obj,
                old_data=old_data,
                new_data=serialize_instance(settings_obj),
                description="تم تعديل إعدادات النظام العامة.",
                request=request,
            )
            messages.success(request, "تم حفظ إعدادات النظام بنجاح.")
            return redirect('system_settings')
        messages.error(request, "تعذر حفظ إعدادات النظام. يرجى مراجعة الحقول.")
    else:
        form = SystemSettingsForm(instance=settings_obj)
    return render(request, 'system_settings.html', {
        'title': 'إعدادات النظام',
        'form': form,
        'settings_obj': settings_obj,
    })


@login_required(login_url='login')
def home_redirect(request):
    permissions = None
    try:
        permissions = request.user.profile.job_title.permissions
    except AttributeError:
        permissions = None

    if permissions and permissions.can_view_dashboard:
        return redirect('maintenance_dashboard')

    if user_can_manage_system_setup(request.user):
        return redirect('employees')

    from maintenance.views import get_pending_maintenance_actions_count, get_pending_spare_part_actions_count

    if get_pending_maintenance_actions_count(request.user) > 0:
        return redirect('request_list')

    if get_pending_spare_part_actions_count(request.user) > 0:
        return redirect('spare_part_list')

    profile = getattr(request.user, 'profile', None)
    if profile and profile.assigned_tasks.exists():
        return redirect('request_list')

    return redirect('profile')


home = home_redirect


class EmployeesList(SystemAdminRequiredMixin, ListView):
    model = User
    template_name = 'employees.html'
    context_object_name = 'employees'

    def get_queryset(self):
        return employee_queryset().order_by('first_name', 'last_name', 'username')


@login_required(login_url='login')
@transaction.atomic
def AddEmployee(request):
    require_system_admin(request)
    job_permissions = get_job_permissions_map()

    if request.method == 'POST':
        user_form = UserForm(request.POST)
        profile_form = ProfileForm(request.POST)
        if user_form.is_valid() and profile_form.is_valid():
            user = user_form.save(commit=False)
            user.set_password(user_form.cleaned_data['password'])
            user.save()
            profile = profile_form.save(commit=False)
            profile.user = user
            profile.full_clean()
            profile.save()
            create_audit_log(
                request.user,
                AuditLog.ACTION_CREATE,
                user,
                new_data={
                    'user': serialize_instance(user, fields=['username', 'first_name', 'last_name', 'email', 'is_active']),
                    'profile': serialize_instance(profile, fields=['job_title', 'specialty', 'managing_department', 'employee_id', 'phone_number']),
                },
                description=f"تم إنشاء الموظف {user.get_full_name() or user.username}",
                request=request,
            )
            return redirect('employees')
    else:
        user_form = UserForm()
        profile_form = ProfileForm()
    
    return render(request, 'add_employee.html', {
        'user_form': user_form,
        'profile_form': profile_form,
        'job_permissions': job_permissions,
        'title': 'إضافة موظف',
        'submit_label': 'حفظ الموظف',
    })


@login_required(login_url='login')
def EmployeeDetail(request, pk):
    if pk == request.user.pk:
        return redirect('profile')
    require_system_admin(request)
    employee = get_object_or_404(employee_queryset(), pk=pk)
    context = get_employee_context(employee)
    context.update({
        'title': 'Employee Details',
        'can_manage_employee': True,
    })
    return render(request, 'employee_detail.html', {
        **context,
    })


@login_required(login_url='login')
def Profile(request):
    employee = get_object_or_404(employee_queryset(), pk=request.user.id)
    context = get_employee_context(employee)
    context.update({
        'title': 'My Profile',
        'can_manage_employee': False,
    })
    return render(request, 'profile.html', context)


@login_required(login_url='login')
def change_my_password(request):
    if request.method == 'POST':
        form = SimplePasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            request.user.set_password(form.cleaned_data['new_password'])
            request.user.save(update_fields=['password'])
            update_session_auth_hash(request, request.user)
            create_audit_log(
                request.user,
                AuditLog.ACTION_PASSWORD_CHANGE,
                request.user,
                new_data={'changed_at': timezone.now().isoformat()},
                description="قام المستخدم بتغيير كلمة المرور الخاصة به.",
                request=request,
            )
            messages.success(request, "تم تغيير كلمة المرور بنجاح.")
            return redirect('profile')
        messages.error(request, "تعذر تغيير كلمة المرور. تحقق من الحقول المطلوبة.")
    else:
        form = SimplePasswordChangeForm(request.user)
    return render(request, 'change_password.html', {
        'form': form,
        'title': 'تغيير كلمة المرور',
    })


@login_required(login_url='login')
@transaction.atomic
def EditEmployee(request, pk):
    require_system_admin(request)
    employee = get_object_or_404(User, pk=pk)
    old_user_data = serialize_instance(employee, fields=['username', 'first_name', 'last_name', 'email', 'is_active'])
    old_profile_data = serialize_instance(employee.profile, fields=['job_title', 'specialty', 'managing_department', 'employee_id', 'phone_number'])
    old_job_title = employee.profile.job_title
    job_permissions = get_job_permissions_map()
    if request.method == 'POST':
        user_form = UserForm(request.POST, instance=employee, require_password=False)
        profile_form = ProfileForm(request.POST, instance=employee.profile)
        if user_form.is_valid() and profile_form.is_valid():
            user = user_form.save(commit=False)
            profile = profile_form.save(commit=False)
            new_job_title = profile.job_title
            old_can_manage = bool(
                old_job_title
                and hasattr(old_job_title, 'permissions')
                and old_job_title.permissions.can_manage_system_setup
            )
            new_can_manage = bool(
                new_job_title
                and hasattr(new_job_title, 'permissions')
                and new_job_title.permissions.can_manage_system_setup
            )
            if old_can_manage and not new_can_manage:
                try:
                    ensure_at_least_one_system_admin_exists(exclude_user=employee)
                except PermissionDenied as error:
                    messages.error(request, str(error))
                    return render(request, 'add_employee.html', {
                        'user_form': user_form,
                        'profile_form': profile_form,
                        'job_permissions': job_permissions,
                        'title': 'تعديل بيانات الموظف',
                        'submit_label': 'حفظ التعديلات',
                    })
            if user_form.cleaned_data['password']:
                user.set_password(user_form.cleaned_data['password'])
            user.save()
            profile.user = user
            profile.full_clean()
            profile.save()
            create_audit_log(
                request.user,
                AuditLog.ACTION_UPDATE,
                user,
                old_data={'user': old_user_data, 'profile': old_profile_data},
                new_data={
                    'user': serialize_instance(user, fields=['username', 'first_name', 'last_name', 'email', 'is_active']),
                    'profile': serialize_instance(profile, fields=['job_title', 'specialty', 'managing_department', 'employee_id', 'phone_number']),
                },
                description=f"تم تعديل بيانات الموظف {user.get_full_name() or user.username}",
                request=request,
            )
            return redirect('employees')
    else:
        user_form = UserForm(instance=employee, require_password=False)
        profile_form = ProfileForm(instance=employee.profile)
    return render(request, 'add_employee.html', {
        'user_form': user_form,
        'profile_form': profile_form,
        'job_permissions': job_permissions,
        'title': 'تعديل بيانات الموظف',
        'submit_label': 'حفظ التعديلات',
    })


@login_required(login_url='login')
def DeleteEmployee(request, pk):
    require_system_admin(request)
    employee = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        old_data = serialize_instance(employee, fields=['username', 'first_name', 'last_name', 'email', 'is_active'])
        if user_can_manage_system_setup(employee):
            try:
                ensure_at_least_one_system_admin_exists(exclude_user=employee)
            except PermissionDenied as error:
                messages.error(request, str(error))
                return redirect('employee_detail', pk=pk)
        description = f"تم حذف الموظف {employee.get_full_name() or employee.username}"
        create_audit_log(
            request.user,
            AuditLog.ACTION_DELETE,
            employee,
            old_data=old_data,
            description=description,
            request=request,
        )
        employee.delete()
        return redirect('employees')
    return redirect('employee_detail', pk=pk)


@login_required(login_url='login')
@transaction.atomic
def ToggleEmployeeActive(request, pk):
    require_system_admin(request)
    employee = get_object_or_404(employee_queryset(), pk=pk)
    if request.method != 'POST':
        return redirect('employee_detail', pk=pk)
    if employee == request.user:
        messages.error(request, "لا يمكنك تغيير حالة حسابك من هذه الصفحة.")
        return redirect('profile')
    if employee.is_active and user_can_manage_system_setup(employee):
        try:
            ensure_at_least_one_system_admin_exists(exclude_user=employee)
        except PermissionDenied as error:
            messages.error(request, str(error))
            return redirect('employee_detail', pk=pk)
    old_data = {'is_active': employee.is_active}
    employee.is_active = not employee.is_active
    employee.save(update_fields=['is_active'])
    create_audit_log(
        request.user,
        AuditLog.ACTION_STATUS_CHANGE,
        employee,
        old_data=old_data,
        new_data={'is_active': employee.is_active},
        description=f"تم {'تفعيل' if employee.is_active else 'تعطيل'} حساب {employee.get_full_name() or employee.username}",
        request=request,
    )
    messages.success(request, "تم تحديث حالة الحساب بنجاح.")
    return redirect('employee_detail', pk=pk)


class JobTitlesList(SystemAdminRequiredMixin, ListView):
    model = JobTitle
    template_name = 'roles.html'
    context_object_name = 'job_titles'


class AddJobTitle(SystemAdminRequiredMixin, CreateView):
    model = JobTitle
    form_class = JobTitleForm
    template_name = 'add.html'
    success_url = '/accounts/roles/'

    def form_valid(self, form):
        response = super().form_valid(form)
        create_audit_log(
            self.request.user,
            AuditLog.ACTION_CREATE,
            self.object,
            new_data=serialize_instance(self.object),
            description=f"تم إنشاء المسمى الوظيفي {self.object.title_name}",
            request=self.request,
        )
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'إضافة مسمى وظيفي'
        return context


class EditJobTitle(SystemAdminRequiredMixin, UpdateView):
    model = JobTitle
    form_class = JobTitleForm
    template_name = 'edit.html'
    success_url = '/accounts/roles/'

    def dispatch(self, request, *args, **kwargs):
        self._old_data = None
        if kwargs.get('pk'):
            obj = get_object_or_404(JobTitle, pk=kwargs['pk'])
            self._old_data = serialize_instance(obj)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        create_audit_log(
            self.request.user,
            AuditLog.ACTION_UPDATE,
            self.object,
            old_data=self._old_data,
            new_data=serialize_instance(self.object),
            description=f"تم تعديل المسمى الوظيفي {self.object.title_name}",
            request=self.request,
        )
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'تعديل المسمى الوظيفي ({self.get_object().title_name})'
        return context


class DeleteJobTitle(SystemAdminRequiredMixin, DeleteView):
    model = JobTitle
    success_url = '/accounts/roles/'

    def post(self, request, *args, **kwargs):
        job_title = self.get_object()
        if job_title.job_titles_profiles.exists():
            messages.error(request, "لا يمكن حذف المسمى الوظيفي لأنه مرتبط بملفات تعريف المستخدمين.")
            return redirect('roles')
        if hasattr(job_title, 'permissions') and job_title.permissions.can_manage_system_setup:
            try:
                ensure_at_least_one_system_admin_exists(exclude_job_title=job_title)
            except PermissionDenied as error:
                messages.error(request, str(error))
                return redirect('roles')
        old_data = serialize_instance(job_title)
        create_audit_log(
            request.user,
            AuditLog.ACTION_DELETE,
            job_title,
            old_data=old_data,
            description=f"تم حذف المسمى الوظيفي {job_title.title_name}",
            request=request,
        )
        return super().post(request, *args, **kwargs)


@login_required
def EditJobTitlePermissions(request, pk):
    require_system_admin(request)
    job_title = get_object_or_404(JobTitle, pk=pk)
    permissions, _ = JobTitlePermission.objects.get_or_create(job_title=job_title)
    old_data = serialize_instance(permissions)

    if request.method == 'POST':
        form = JobTitlePermissionForm(request.POST, instance=permissions)
        if form.is_valid():
            try:
                permission_instance = form.save(commit=False)
                permission_instance.job_title = job_title
                if permissions.can_manage_system_setup and not permission_instance.can_manage_system_setup:
                    ensure_at_least_one_system_admin_exists(exclude_job_title=job_title)

                permission_instance.full_clean()
                
                permission_instance.save()
                create_audit_log(
                    request.user,
                    AuditLog.ACTION_UPDATE,
                    permission_instance,
                    old_data=old_data,
                    new_data=serialize_instance(permission_instance),
                    description=f"تم تعديل صلاحيات المسمى الوظيفي {job_title.title_name}",
                    request=request,
                )
                messages.success(request, "تم حفظ صلاحيات المسمى الوظيفي بنجاح.")
                return redirect('roles')
            except (ValidationError, PermissionDenied) as error:
                form.add_error(None, error)
    else:
        form = JobTitlePermissionForm(instance=permissions)

    return render(request, 'job_title_permissions.html', {
        'form': form,
        'job_title': job_title,
    })


class SpecialtiesList(SystemAdminRequiredMixin, ListView):
    model = Specialty
    template_name = 'specialties.html'
    context_object_name = 'specialties'


class AddSpecialty(SystemAdminRequiredMixin, CreateView):
    model = Specialty
    form_class = SpecialtyForm
    template_name = 'add.html'
    success_url = '/accounts/specialties/'

    def form_valid(self, form):
        response = super().form_valid(form)
        create_audit_log(
            self.request.user,
            AuditLog.ACTION_CREATE,
            self.object,
            new_data=serialize_instance(self.object),
            description=f"تم إنشاء التخصص {self.object.name}",
            request=self.request,
        )
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'إضافة تخصص هندسي'
        return context


class EditSpecialty(SystemAdminRequiredMixin, UpdateView):
    model = Specialty
    form_class = SpecialtyForm
    template_name = 'edit.html'
    success_url = '/accounts/specialties/'

    def dispatch(self, request, *args, **kwargs):
        self._old_data = None
        if kwargs.get('pk'):
            obj = get_object_or_404(Specialty, pk=kwargs['pk'])
            self._old_data = serialize_instance(obj)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        create_audit_log(
            self.request.user,
            AuditLog.ACTION_UPDATE,
            self.object,
            old_data=self._old_data,
            new_data=serialize_instance(self.object),
            description=f"تم تعديل التخصص {self.object.name}",
            request=self.request,
        )
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = f'تعديل التخصص ({self.get_object().name})'
        return context


class DeleteSpecialty(SystemAdminRequiredMixin, DeleteView):
    model = Specialty
    success_url = '/accounts/specialties/'

    def post(self, request, *args, **kwargs):
        specialty = self.get_object()
        if specialty.profile_set.exists():
            messages.error(request, "لا يمكن حذف هذا التخصص لأنه مرتبط بملفات تعريف المستخدمين.")
            return redirect('specialties')
        create_audit_log(
            request.user,
            AuditLog.ACTION_DELETE,
            specialty,
            old_data=serialize_instance(specialty),
            description=f"تم حذف التخصص {specialty.name}",
            request=request,
        )
        return super().post(request, *args, **kwargs)


@login_required(login_url='login')
def dashboard(request):
    try:
        can_view_dashboard = request.user.profile.job_title.permissions.can_view_dashboard
    except AttributeError:
        can_view_dashboard = False
    if request.user.has_perm('maintenance.can_view_dashboard') or can_view_dashboard:
        return redirect('maintenance_dashboard')
    messages.warning(request, "لا تمتلك صلاحية عرض لوحة التحكم. تم توجيهك للصفحة المناسبة.")
    return redirect('home_redirect')


def LoginView(request):
    if request.user.is_authenticated:
        return redirect('home_redirect')

    if request.method == 'POST':
        user = authenticate(request, username=request.POST.get('username'), password=request.POST.get('password'))
        if user is not None:
            login(request, user)
            create_audit_log(
                user,
                AuditLog.ACTION_LOGIN,
                user,
                new_data={'username': user.username, 'login_at': timezone.now().isoformat()},
                description="تم تسجيل الدخول إلى النظام.",
                request=request,
            )
            next_url = request.POST.get('next') or request.GET.get('next')
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect('home_redirect')
        messages.error(request, "اسم المستخدم أو كلمة المرور غير صحيحة.")
    return render(request, 'login.html', {
        'username': request.POST.get('username', ''),
        'next': request.POST.get('next') or request.GET.get('next', ''),
    })


@login_required(login_url='login')
@require_POST
def logout_view(request):
    create_audit_log(
        request.user,
        AuditLog.ACTION_LOGOUT,
        request.user,
        new_data={'username': request.user.username, 'logout_at': timezone.now().isoformat()},
        description="تم تسجيل الخروج من النظام.",
        request=request,
    )
    logout(request)
    return redirect('login')
