from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, JsonResponse
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from .audit_log_service import create_audit_log, serialize_instance
from .backup_service import create_database_backup, delete_backup_file, get_backup_path, list_backups
from hospital_units.models import Building, Department, Floor
from maintenance.access import (
    get_visible_complete_reports,
    get_visible_maintenance_requests,
    get_visible_spare_part_requests,
)
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
    UserPermissionOverrideForm,
    UserForm,
)
from .models import (
    ApprovalDelegation,
    ApprovalLevel,
    ApprovalWorkflow,
    AuditLog,
    JobTitle,
    JobTitlePermission,
    Specialty,
    SystemSettings,
    UserPermissionOverride,
)
from .permissions import (
    AUTH_PERMISSION_FIELD_MAP,
    SYSTEM_SETUP_PERMISSION,
    ensure_at_least_one_system_admin_exists,
    get_effective_permissions,
    get_permission_field_metadata,
    get_user_role_permissions,
    require_role_permission,
    require_system_setup_permission,
    user_has_any_role_permission,
    user_has_role_permission,
    user_can_manage_system_setup,
)


APPROVAL_HIERARCHY_WORKFLOWS = (
    {
        'workflow_type': ApprovalWorkflow.PURCHASE,
        'title': 'تعميد طلب الشراء',
        'approve_field': 'can_approve_purchase_order',
        'order_field': 'purchase_approval_order',
        'spare_order_kind': 'purchase_requisition',
    },
    {
        'workflow_type': ApprovalWorkflow.STORE_ISSUE,
        'title': 'تعميد طلب الصرف من المخازن',
        'approve_field': 'can_approve_store_requisition',
        'order_field': 'store_approval_order',
        'spare_order_kind': 'store_requisition',
    },
    {
        'workflow_type': ApprovalWorkflow.ACHIEVEMENT_REPORT,
        'title': 'تعميد تقرير الإنجاز',
        'approve_field': 'can_approve_achievement_report',
        'order_field': 'achievement_approval_order',
    },
)


def is_system_admin(user):
    return user_can_manage_system_setup(user)


def user_can_view_audit_log(user):
    return (
        user_has_role_permission(user, 'can_view_audit_logs', auth_permission='accounts.can_view_audit_log')
        or user_has_role_permission(user, 'can_view_audit_log', auth_permission='accounts.can_view_audit_log')
    )


def account_navigation_context(
    current_label,
    section_label=None,
    section_url_name=None,
    show_back_button=False,
    back_url_name=None,
    back_url_kwargs=None,
):
    breadcrumbs = [{'label': 'لوحة التحكم', 'url': reverse('home_redirect')}]
    if section_label:
        breadcrumbs.append({
            'label': section_label,
            'url': reverse(section_url_name) if section_url_name else None,
        })
    breadcrumbs.append({'label': current_label})
    context = {'breadcrumbs': breadcrumbs}
    if show_back_button:
        context['show_back_button'] = True
        if back_url_name:
            context['back_url'] = reverse(back_url_name, kwargs=back_url_kwargs or {})
    return context


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


GLOBAL_SEARCH_MIN_LENGTH = 2
GLOBAL_SEARCH_TOTAL_LIMIT = 10
GLOBAL_SEARCH_TYPE_LIMIT = 5


def _short_text(value, max_length=90):
    value = (value or '').strip()
    if len(value) <= max_length:
        return value
    return f"{value[:max_length].rstrip()}..."


def _display_name(user):
    return user.get_full_name() or user.username


def _department_location(department):
    if not department:
        return ''
    parts = [department.name]
    floor = getattr(department, 'floor', None)
    building = getattr(floor, 'building', None) if floor else None
    if floor:
        parts.append(floor.name)
    if building:
        parts.append(building.name)
    return ' | '.join(part for part in parts if part)


def _matching_choice_values(choices, query):
    lowered_query = query.casefold()
    return [
        value
        for value, label in choices
        if lowered_query in str(value).casefold() or lowered_query in str(label).casefold()
    ]


def _append_global_result(results, result_type, title, description, url):
    if len(results) >= GLOBAL_SEARCH_TOTAL_LIMIT:
        return False
    results.append({
        'type': result_type,
        'title': title,
        'description': description or '',
        'url': url,
    })
    return True


def _global_search_maintenance_queryset(user):
    queryset = MaintenanceRequest.objects.select_related(
        'department',
        'department__floor',
        'department__floor__building',
        'required_specialty',
        'assigned_technician',
        'assigned_technician__user',
    )
    if user_can_manage_system_setup(user) or user_has_role_permission(user, 'can_view_all_departments'):
        return queryset
    return get_visible_maintenance_requests(user, queryset)


def _global_search_spare_part_queryset(user):
    queryset = SparePartRequest.objects.select_related(
        'maintenance_request',
        'maintenance_request__department',
        'maintenance_request__department__floor',
        'maintenance_request__department__floor__building',
        'engineer',
        'engineer__user',
    ).prefetch_related('spare_parts')
    if user_can_manage_system_setup(user) or user_has_role_permission(user, 'can_view_all_departments'):
        return queryset
    return get_visible_spare_part_requests(user, queryset)


def _global_search_complete_report_queryset(user):
    queryset = CompleteReport.objects.select_related(
        'maintenance_request',
        'maintenance_request__department',
        'maintenance_request__department__floor',
        'maintenance_request__department__floor__building',
    )
    if user_can_manage_system_setup(user) or user_has_role_permission(user, 'can_view_all_departments'):
        return queryset
    return get_visible_complete_reports(user, queryset)


def _search_maintenance_requests(user, query, results):
    search_query = (
        Q(description__icontains=query)
        | Q(department__name__icontains=query)
        | Q(department__floor__name__icontains=query)
        | Q(department__floor__building__name__icontains=query)
        | Q(required_specialty__name__icontains=query)
        | Q(assigned_technician__user__first_name__icontains=query)
        | Q(assigned_technician__user__last_name__icontains=query)
        | Q(assigned_technician__user__username__icontains=query)
        | Q(status__in=_matching_choice_values(MaintenanceRequest.STATUS_CHOICES, query))
        | Q(priority__in=_matching_choice_values(MaintenanceRequest.PRIORITY_CHOICES, query))
    )
    if query.isdigit():
        search_query |= Q(pk=int(query))

    for maintenance_request in _global_search_maintenance_queryset(user).filter(search_query).distinct().order_by('-updated_at')[:GLOBAL_SEARCH_TYPE_LIMIT]:
        specialty = maintenance_request.required_specialty.name if maintenance_request.required_specialty_id else ''
        title = f"طلب صيانة #{maintenance_request.pk}"
        if specialty:
            title = f"{title} - {specialty}"
        description = ' - '.join(filter(None, [
            _department_location(maintenance_request.department),
            maintenance_request.get_status_display(),
        ]))
        if not _append_global_result(
            results,
            'طلب صيانة',
            title,
            description,
            reverse('request_maintenance_details', kwargs={'pk': maintenance_request.pk}),
        ):
            break


def _search_spare_part_requests(user, query, results):
    search_query = (
        Q(description__icontains=query)
        | Q(maintenance_request__department__name__icontains=query)
        | Q(maintenance_request__department__floor__name__icontains=query)
        | Q(maintenance_request__department__floor__building__name__icontains=query)
        | Q(engineer__user__first_name__icontains=query)
        | Q(engineer__user__last_name__icontains=query)
        | Q(engineer__user__username__icontains=query)
        | Q(spare_parts__part_name__icontains=query)
        | Q(status__in=_matching_choice_values(SparePartRequest.STATUS_CHOICES, query))
        | Q(order_kind__in=_matching_choice_values(SparePartRequest.order_kinds_choices, query))
    )
    if query.isdigit():
        search_query |= Q(pk=int(query)) | Q(maintenance_request_id=int(query))

    for spare_request in _global_search_spare_part_queryset(user).filter(search_query).distinct().order_by('-updated_at')[:GLOBAL_SEARCH_TYPE_LIMIT]:
        part_names = [part.part_name for part in list(spare_request.spare_parts.all())[:2]]
        description = ' - '.join(filter(None, [
            f"طلب صيانة #{spare_request.maintenance_request_id}",
            spare_request.get_status_display(),
            '، '.join(part_names),
        ]))
        _append_global_result(
            results,
            'طلب قطع غيار',
            f"طلب قطع غيار #{spare_request.pk} - {spare_request.get_order_kind_display()}",
            description,
            reverse('spare_part_details', kwargs={'pk': spare_request.pk}),
        )
        if len(results) >= GLOBAL_SEARCH_TOTAL_LIMIT:
            break


def _search_complete_reports(user, query, results):
    search_query = (
        Q(complete_report__icontains=query)
        | Q(maintenance_request__description__icontains=query)
        | Q(maintenance_request__department__name__icontains=query)
        | Q(maintenance_request__department__floor__name__icontains=query)
        | Q(maintenance_request__department__floor__building__name__icontains=query)
        | Q(status__in=_matching_choice_values(CompleteReport.report_statuses, query))
    )
    if query.isdigit():
        search_query |= Q(pk=int(query)) | Q(maintenance_request_id=int(query))

    for report in _global_search_complete_report_queryset(user).filter(search_query).distinct().order_by('-updated_at')[:GLOBAL_SEARCH_TYPE_LIMIT]:
        description = ' - '.join(filter(None, [
            report.get_status_display(),
            _short_text(report.complete_report),
        ]))
        _append_global_result(
            results,
            'تقرير إنجاز',
            f"تقرير إنجاز #{report.pk} - طلب صيانة #{report.maintenance_request_id}",
            description,
            reverse('request_maintenance_details', kwargs={'pk': report.maintenance_request_id}),
        )
        if len(results) >= GLOBAL_SEARCH_TOTAL_LIMIT:
            break


def _search_employees(user, query, results):
    if not user_has_role_permission(user, 'can_view_employees'):
        return

    search_query = (
        Q(first_name__icontains=query)
        | Q(last_name__icontains=query)
        | Q(username__icontains=query)
        | Q(email__icontains=query)
        | Q(profile__employee_id__icontains=query)
        | Q(profile__phone_number__icontains=query)
        | Q(profile__job_title__title_name__icontains=query)
        | Q(profile__managing_department__name__icontains=query)
    )
    if query.isdigit():
        search_query |= Q(pk=int(query))

    for employee in employee_queryset().filter(search_query).distinct().order_by('first_name', 'last_name', 'username')[:GLOBAL_SEARCH_TYPE_LIMIT]:
        profile = getattr(employee, 'profile', None)
        job_title = profile.job_title.title_name if profile and profile.job_title else ''
        department = _department_location(profile.managing_department) if profile and profile.managing_department else ''
        status = 'نشط' if employee.is_active else 'غير نشط'
        description = ' - '.join(filter(None, [job_title, department, status]))
        _append_global_result(
            results,
            'موظف',
            f"الموظف: {_display_name(employee)}",
            description,
            reverse('employee_detail', kwargs={'pk': employee.pk}),
        )
        if len(results) >= GLOBAL_SEARCH_TOTAL_LIMIT:
            break


def _search_job_titles(user, query, results):
    if not user_has_role_permission(user, 'can_view_job_titles'):
        return

    for job_title in JobTitle.objects.filter(title_name__icontains=query).order_by('title_name')[:GLOBAL_SEARCH_TYPE_LIMIT]:
        _append_global_result(
            results,
            'مسمى وظيفي',
            f"المسمى الوظيفي: {job_title.title_name}",
            '',
            reverse('job_title_detail', kwargs={'pk': job_title.pk}),
        )
        if len(results) >= GLOBAL_SEARCH_TOTAL_LIMIT:
            break


def _search_organization(user, query, results):
    if not user_has_role_permission(user, 'can_view_organization_structure'):
        return

    department_query = (
        Q(name__icontains=query)
        | Q(floor__name__icontains=query)
        | Q(floor__building__name__icontains=query)
    )
    for department in Department.objects.select_related('floor', 'floor__building').filter(department_query).distinct().order_by('name')[:GLOBAL_SEARCH_TYPE_LIMIT]:
        _append_global_result(
            results,
            'قسم',
            f"القسم: {department.name}",
            _department_location(department),
            reverse('departments'),
        )
        if len(results) >= GLOBAL_SEARCH_TOTAL_LIMIT:
            return

    for building in Building.objects.filter(Q(name__icontains=query) | Q(description__icontains=query)).order_by('name')[:GLOBAL_SEARCH_TYPE_LIMIT]:
        _append_global_result(
            results,
            'مبنى',
            f"المبنى: {building.name}",
            _short_text(building.description),
            reverse('buildings'),
        )
        if len(results) >= GLOBAL_SEARCH_TOTAL_LIMIT:
            return

    floor_query = Q(name__icontains=query) | Q(description__icontains=query) | Q(building__name__icontains=query)
    for floor in Floor.objects.select_related('building').filter(floor_query).distinct().order_by('building__name', 'name')[:GLOBAL_SEARCH_TYPE_LIMIT]:
        _append_global_result(
            results,
            'طابق',
            f"الطابق: {floor.name}",
            floor.building.name if floor.building_id else '',
            reverse('floors'),
        )
        if len(results) >= GLOBAL_SEARCH_TOTAL_LIMIT:
            return


@login_required(login_url='login')
def global_search(request):
    query = (request.GET.get('q') or '').strip()
    if len(query) < GLOBAL_SEARCH_MIN_LENGTH:
        return JsonResponse({'results': []})

    results = []
    _search_maintenance_requests(request.user, query, results)
    if len(results) < GLOBAL_SEARCH_TOTAL_LIMIT:
        _search_spare_part_requests(request.user, query, results)
    if len(results) < GLOBAL_SEARCH_TOTAL_LIMIT:
        _search_complete_reports(request.user, query, results)
    if len(results) < GLOBAL_SEARCH_TOTAL_LIMIT:
        _search_employees(request.user, query, results)
    if len(results) < GLOBAL_SEARCH_TOTAL_LIMIT:
        _search_job_titles(request.user, query, results)
    if len(results) < GLOBAL_SEARCH_TOTAL_LIMIT:
        _search_organization(request.user, query, results)

    return JsonResponse({'results': results})


def _get_filter_value(params, key):
    return (params.get(key) or '').strip()


def _get_boolean_filter_value(params, key):
    value = _get_filter_value(params, key)
    if value == '1':
        return True
    if value == '0':
        return False
    return None


def _has_admin_filters(params):
    return any(value for key, value in params.items() if key != 'page' and value)


def _query_string_without_page(params):
    query = params.copy()
    query.pop('page', None)
    return query.urlencode()


def _remove_filter_url(request, *keys):
    query = request.GET.copy()
    query.pop('page', None)
    for key in keys:
        query.pop(key, None)
    query_string = query.urlencode()
    return f"{request.path}?{query_string}" if query_string else request.path


def _append_applied_filter(filters, request, key, label, value_label):
    if _get_filter_value(request.GET, key):
        filters.append({
            'label': label,
            'value': value_label,
            'remove_url': _remove_filter_url(request, key),
        })


def _label_from_queryset(queryset, value, fallback='', attr_name=None):
    if not value:
        return fallback
    for obj in queryset:
        if str(obj.pk) == str(value):
            return str(getattr(obj, attr_name, obj)) if attr_name else str(obj)
    return fallback or value


YES_NO_FILTER_LABELS = {
    '1': 'نعم',
    '0': 'لا',
}

EMPLOYEE_STATUS_FILTER_LABELS = {
    'active': 'نشط',
    'inactive': 'غير نشط',
}

EMPLOYEE_ORDERING_FILTER_LABELS = {
    'name': 'الاسم تصاعدياً',
    '-name': 'الاسم تنازلياً',
    'newest': 'الأحدث أولاً',
    'oldest': 'الأقدم أولاً',
    'status': 'حسب الحالة',
    'department': 'حسب القسم',
    'job_title': 'حسب المسمى الوظيفي',
}

NAME_ORDERING_FILTER_LABELS = {
    'name': 'الاسم تصاعدياً',
    '-name': 'الاسم تنازلياً',
    'newest': 'الأحدث أولاً',
    'oldest': 'الأقدم أولاً',
}


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
    role_permissions = get_user_role_permissions(employee)
    permissions = get_effective_permissions(employee)
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
                'role_enabled': bool(role_permissions and getattr(role_permissions, field_name, False)),
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
        'role_permissions': role_permissions,
        'permissions': permissions,
        'important_permissions': important_permissions,
        'role_labels': role_labels,
        'group_names': [group.name for group in employee.groups.all()],
        'can_manage_system_setup': user_can_manage_system_setup(employee),
    }


def get_employee_permission_override_data(employee):
    profile = getattr(employee, 'profile', None)
    job_title = profile.job_title if profile else None
    role_permissions = get_user_role_permissions(employee)
    effective_permissions = get_effective_permissions(employee)
    overrides = list(UserPermissionOverride.objects.filter(user=employee))
    granted_names = {
        override.permission_name
        for override in overrides
        if override.action == UserPermissionOverride.ACTION_GRANT
    }
    denied_names = {
        override.permission_name
        for override in overrides
        if override.action == UserPermissionOverride.ACTION_DENY
    }

    auth_permission_names = {
        field_name
        for field_name, auth_permission in AUTH_PERMISSION_FIELD_MAP.items()
        if employee.has_perm(auth_permission)
    }

    granted_permissions = []
    denied_permissions = []
    role_permission_rows = []
    permission_matrix = []
    permission_rows_by_name = {}
    system_setup_enabled = bool(getattr(effective_permissions, 'can_manage_system_setup', False))
    grouped_permission_names = {
        field_name
        for _, field_names in JOB_TITLE_DETAIL_PERMISSION_GROUPS
        for field_name in field_names
    }
    for permission in get_permission_field_metadata():
        name = permission['name']
        label = permission['label']
        role_enabled = bool(role_permissions and getattr(role_permissions, name, False))
        direct_effective_enabled = bool(getattr(effective_permissions, name, False))
        via_system_setup = bool(
            system_setup_enabled
            and name in grouped_permission_names
            and name != 'can_manage_system_setup'
            and not direct_effective_enabled
        )
        effective_enabled = bool(direct_effective_enabled or via_system_setup)
        auth_enabled = name in auth_permission_names
        row = {
            'name': name,
            'label': label,
            'role_enabled': role_enabled,
            'auth_enabled': auth_enabled,
            'via_system_setup': via_system_setup,
            'effective_enabled': effective_enabled,
            'is_granted': name in granted_names,
            'is_denied': name in denied_names,
            'is_visible': bool(effective_enabled or role_enabled or name in granted_names or name in denied_names),
        }
        permission_matrix.append(row)
        permission_rows_by_name[name] = row
        if row['is_granted']:
            granted_permissions.append(row)
        if row['is_denied']:
            denied_permissions.append(row)
        if role_enabled:
            role_permission_rows.append(row)

    grouped_permissions = []
    grouped_names = set()
    for group_label, field_names in JOB_TITLE_DETAIL_PERMISSION_GROUPS:
        group_permissions = []
        for field_name in field_names:
            row = permission_rows_by_name.get(field_name)
            if not row or not row['is_visible']:
                continue
            group_permissions.append(row)
            grouped_names.add(field_name)
        if group_permissions:
            grouped_permissions.append({
                'label': group_label,
                'permissions': group_permissions,
            })

    ungrouped_permissions = [
        row for row in permission_matrix
        if row['name'] not in grouped_names and row['is_visible']
    ]
    if ungrouped_permissions:
        grouped_permissions.append({
            'label': 'صلاحيات أخرى',
            'permissions': ungrouped_permissions,
        })

    inherited_delegation_ids = set()
    if job_title:
        inherited_delegation_ids = set(
            ApprovalDelegation.objects.filter(source_job_title=job_title)
            .values_list('approval_level_id', flat=True)
        )

    approval_sections = []
    approval_effective_count = 0
    approval_denied_count = 0
    approval_granted_count = 0
    for workflow_type, workflow_label in USER_APPROVAL_WORKFLOWS:
        levels = (
            ApprovalLevel.objects.filter(workflow_type=workflow_type)
            .select_related('job_title')
            .order_by('order_number')
        )
        approval_rows = []
        for level in levels:
            normal_permission = _approval_level_permission_name(level)
            delegation_permission = _approval_delegation_permission_name(level)

            normal_inherited = bool(job_title and level.job_title_id == job_title.pk)
            normal_denied = normal_permission in denied_names
            normal_effective = normal_inherited and not normal_denied
            if normal_inherited or normal_denied:
                approval_effective_count += 1 if normal_effective else 0
                approval_denied_count += 1 if normal_denied else 0
                approval_rows.append({
                    'level': level,
                    'mode': 'normal',
                    'mode_label': 'تعميد مباشر',
                    'role_enabled': normal_inherited,
                    'effective_enabled': normal_effective,
                    'is_granted': False,
                    'is_denied': normal_denied,
                })

            delegation_inherited = level.pk in inherited_delegation_ids
            delegation_granted = delegation_permission in granted_names
            delegation_denied = delegation_permission in denied_names
            delegation_effective = (delegation_inherited or delegation_granted) and not delegation_denied
            if delegation_inherited or delegation_granted or delegation_denied:
                approval_effective_count += 1 if delegation_effective else 0
                approval_denied_count += 1 if delegation_denied else 0
                approval_granted_count += 1 if delegation_granted else 0
                approval_rows.append({
                    'level': level,
                    'mode': 'delegation',
                    'mode_label': 'تعميد نيابة عن',
                    'role_enabled': delegation_inherited,
                    'effective_enabled': delegation_effective,
                    'is_granted': delegation_granted,
                    'is_denied': delegation_denied,
                })

        if approval_rows:
            approval_sections.append({
                'workflow_type': workflow_type,
                'label': workflow_label,
                'permissions': approval_rows,
            })

    return {
        'grant_permissions': granted_permissions,
        'deny_permissions': denied_permissions,
        'role_permissions': role_permission_rows,
        'permission_matrix': permission_matrix,
        'grouped_permissions': grouped_permissions,
        'approval_sections': approval_sections,
        'summary': {
            'effective_count': sum(1 for row in permission_matrix if row['effective_enabled']),
            'role_count': len(role_permission_rows),
            'grant_count': len(granted_permissions) + approval_granted_count,
            'deny_count': len(denied_permissions) + approval_denied_count,
            'approval_effective_count': approval_effective_count,
            'approval_grant_count': approval_granted_count,
            'approval_deny_count': approval_denied_count,
        },
    }


USER_APPROVAL_WORKFLOWS = (
    (ApprovalWorkflow.PURCHASE, 'مستويات تعميد الشراء'),
    (ApprovalWorkflow.STORE_ISSUE, 'مستويات تعميد الصرف من المخازن'),
    (ApprovalWorkflow.ACHIEVEMENT_REPORT, 'مستويات تعميد تقرير الإنجاز'),
)


def user_can_manage_permission_overrides(user):
    return user_has_any_role_permission(
        user,
        'can_manage_user_permissions',
        'can_edit_employees',
    )


def _employee_override_sets(employee):
    overrides = UserPermissionOverride.objects.filter(user=employee).values_list('permission_name', 'action')
    grants = {
        permission_name
        for permission_name, action in overrides
        if action == UserPermissionOverride.ACTION_GRANT
    }
    denies = {
        permission_name
        for permission_name, action in overrides
        if action == UserPermissionOverride.ACTION_DENY
    }
    return grants, denies


def _approval_level_permission_name(level):
    return f"approval_level:{level.pk}"


def _approval_delegation_permission_name(level):
    return f"approval_delegation:{level.pk}"


def build_user_permission_management_data(employee):
    profile = getattr(employee, 'profile', None)
    job_title = profile.job_title if profile else None
    role_permissions = get_user_role_permissions(employee)
    effective_permissions = get_effective_permissions(employee)
    grant_overrides, deny_overrides = _employee_override_sets(employee)

    grant_choices = []
    deny_choices = []
    manageable_permission_names = set()
    permission_groups = []

    for group_label, field_names in JOB_TITLE_DETAIL_PERMISSION_GROUPS:
        rows = []
        for field_name in field_names:
            field = JobTitlePermission._meta.get_field(field_name)
            label = field.verbose_name
            role_enabled = bool(role_permissions and getattr(role_permissions, field_name, False))
            effective_enabled = bool(getattr(effective_permissions, field_name, False))
            is_granted = field_name in grant_overrides
            is_denied = field_name in deny_overrides
            grant_selectable = not role_enabled or is_granted
            deny_visible = effective_enabled or is_denied

            manageable_permission_names.add(field_name)
            if grant_selectable:
                grant_choices.append((field_name, str(label)))
            if deny_visible:
                deny_choices.append((field_name, str(label)))

            rows.append({
                'name': field_name,
                'label': label,
                'role_enabled': role_enabled,
                'effective_enabled': effective_enabled,
                'is_granted': is_granted,
                'is_denied': is_denied,
                'grant_selectable': grant_selectable,
                'deny_visible': deny_visible,
            })
        permission_groups.append({
            'label': group_label,
            'rows': rows,
        })

    approval_sections = []
    inherited_delegation_ids = set()
    if job_title:
        inherited_delegation_ids = set(
            ApprovalDelegation.objects.filter(source_job_title=job_title)
            .values_list('approval_level_id', flat=True)
        )

    for workflow_type, workflow_label in USER_APPROVAL_WORKFLOWS:
        levels = list(
            ApprovalLevel.objects.filter(workflow_type=workflow_type)
            .select_related('job_title')
            .order_by('order_number')
        )
        level_rows = []
        for level in levels:
            level_permission = _approval_level_permission_name(level)
            delegation_permission = _approval_delegation_permission_name(level)
            normal_inherited = bool(job_title and level.job_title_id == job_title.pk)
            delegation_inherited = level.pk in inherited_delegation_ids
            normal_denied = level_permission in deny_overrides
            delegation_granted = delegation_permission in grant_overrides
            delegation_denied = delegation_permission in deny_overrides
            normal_effective = normal_inherited and not normal_denied
            delegation_effective = (delegation_inherited or delegation_granted) and not delegation_denied
            can_grant_delegation = not delegation_inherited or delegation_granted
            normal_deny_visible = normal_effective or normal_denied
            delegation_deny_visible = delegation_effective or delegation_denied
            delegation_label = f"{workflow_label} - المستوى {level.order_number} - {level.job_title.title_name}"

            manageable_permission_names.update({level_permission, delegation_permission})
            if can_grant_delegation:
                grant_choices.append((delegation_permission, f"تعميد نيابة عن: {delegation_label}"))
            if normal_deny_visible:
                deny_choices.append((level_permission, f"سحب تعميد المستوى: {delegation_label}"))
            if delegation_deny_visible:
                deny_choices.append((delegation_permission, f"سحب التعميد نيابة عن: {delegation_label}"))

            level_rows.append({
                'level': level,
                'normal_permission': level_permission,
                'delegation_permission': delegation_permission,
                'normal_inherited': normal_inherited,
                'delegation_inherited': delegation_inherited,
                'normal_denied': normal_denied,
                'delegation_granted': delegation_granted,
                'delegation_denied': delegation_denied,
                'normal_effective': normal_effective,
                'delegation_effective': delegation_effective,
                'can_grant_delegation': can_grant_delegation,
                'normal_deny_visible': normal_deny_visible,
                'delegation_deny_visible': delegation_deny_visible,
            })

        approval_sections.append({
            'workflow_type': workflow_type,
            'label': workflow_label,
            'levels': level_rows,
        })

    return {
        'permission_groups': permission_groups,
        'approval_sections': approval_sections,
        'grant_choices': grant_choices,
        'deny_choices': deny_choices,
        'manageable_permission_names': manageable_permission_names,
        'grant_overrides': grant_overrides,
        'deny_overrides': deny_overrides,
        'role_permissions': role_permissions,
        'effective_permissions': effective_permissions,
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
        'user_permission_override_data': get_employee_permission_override_data(employee),
        'latest_notifications': notifications,
    }


class SystemAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    login_url = 'login'
    required_role_permission = None
    required_role_permissions = ()

    def test_func(self):
        if self.required_role_permission:
            return user_has_role_permission(self.request.user, self.required_role_permission)
        if self.required_role_permissions:
            return user_has_any_role_permission(self.request.user, *self.required_role_permissions)
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
        context.update(account_navigation_context(
            'سجل العمليات',
            section_label='الإدارة',
        ))
        return context


class AuditLogDetailView(AuditLogRequiredMixin, DetailView):
    model = AuditLog
    template_name = 'audit_log_detail.html'
    context_object_name = 'audit_log'

    def get_queryset(self):
        return AuditLog.objects.select_related('user')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(account_navigation_context(
            f'تفاصيل عملية #{self.object.pk}',
            section_label='سجل العمليات',
            section_url_name='audit_logs',
            show_back_button=True,
            back_url_name='audit_logs',
        ))
        return context


@login_required(login_url='login')
def backup_list(request):
    require_role_permission(request, 'can_manage_backups')
    return render(request, 'backup_list.html', {
        'title': 'النسخ الاحتياطي',
        'backups': list_backups(),
        **account_navigation_context(
            'النسخ الاحتياطي',
            section_label='الإدارة',
            section_url_name='system_settings',
            show_back_button=True,
            back_url_name='system_settings',
        ),
    })


@login_required(login_url='login')
@require_POST
def create_backup(request):
    require_role_permission(request, 'can_manage_backups')
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
    require_role_permission(request, 'can_manage_backups')
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
    require_role_permission(request, 'can_manage_backups')
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


def get_approval_workflow_config(workflow_type):
    for config in APPROVAL_HIERARCHY_WORKFLOWS:
        if config['workflow_type'] == workflow_type:
            return config
    raise ValidationError("نوع مسار التعميد غير صحيح.")


def approval_hierarchy_sections():
    sections = []
    for config in APPROVAL_HIERARCHY_WORKFLOWS:
        levels = list(
            ApprovalLevel.objects.filter(workflow_type=config['workflow_type'])
            .select_related('job_title')
            .order_by('order_number')
        )
        sections.append({
            **config,
            'levels': levels,
            'next_order_number': levels[-1].order_number + 1 if levels else 1,
        })
    return sections


def validation_error_text(error):
    if hasattr(error, 'messages') and error.messages:
        return error.messages[0]
    return str(error)


def ensure_sequential_approval_levels(workflow_type):
    levels = list(ApprovalLevel.objects.filter(workflow_type=workflow_type).order_by('order_number'))
    expected = list(range(1, len(levels) + 1))
    actual = [level.order_number for level in levels]
    if actual != expected:
        raise ValidationError("يجب أن تكون أرقام مستويات التعميد متسلسلة وتبدأ من 1.")


def sync_legacy_approval_fields(workflow_type):
    config = get_approval_workflow_config(workflow_type)
    levels = ApprovalLevel.objects.filter(workflow_type=workflow_type).select_related('job_title')
    assigned_orders = {
        level.job_title_id: level.order_number
        for level in levels
    }

    JobTitlePermission.objects.filter(
        **{f"{config['order_field']}__isnull": False}
    ).exclude(
        job_title_id__in=assigned_orders.keys()
    ).update(**{config['order_field']: None})

    for job_title_id, order_number in assigned_orders.items():
        permissions, _ = JobTitlePermission.objects.get_or_create(job_title_id=job_title_id)
        JobTitlePermission.objects.filter(pk=permissions.pk).update(
            **{
                config['approve_field']: True,
                config['order_field']: order_number,
            }
        )


def save_approval_hierarchy_assignments(post_data):
    with transaction.atomic():
        for config in APPROVAL_HIERARCHY_WORKFLOWS:
            workflow_type = config['workflow_type']
            ensure_sequential_approval_levels(workflow_type)
            levels = list(
                ApprovalLevel.objects.select_for_update()
                .filter(workflow_type=workflow_type)
                .order_by('order_number')
            )
            selected_job_title_ids = []
            for level in levels:
                job_title_id = post_data.get(f"level_{level.pk}_job_title")
                if not job_title_id:
                    raise ValidationError(f"يجب تحديد المسمى الوظيفي للمستوى {level.order_number} في {config['title']}.")
                if not JobTitle.objects.filter(pk=job_title_id).exists():
                    raise ValidationError("المسمى الوظيفي المحدد غير موجود.")
                selected_job_title_ids.append(int(job_title_id))

            if len(selected_job_title_ids) != len(set(selected_job_title_ids)):
                raise ValidationError(f"لا يمكن تكرار نفس المسمى الوظيفي في أكثر من مستوى داخل {config['title']}.")

            for level, job_title_id in zip(levels, selected_job_title_ids):
                if level.job_title_id != job_title_id:
                    level.job_title_id = job_title_id
                    level.full_clean()
                    level.save(update_fields=['job_title', 'updated_at'])
            sync_legacy_approval_fields(workflow_type)


def add_approval_level(workflow_type, job_title_id):
    config = get_approval_workflow_config(workflow_type)
    if not job_title_id:
        raise ValidationError("يجب اختيار المسمى الوظيفي للمستوى الجديد.")
    job_title = get_object_or_404(JobTitle, pk=job_title_id)
    with transaction.atomic():
        ensure_sequential_approval_levels(workflow_type)
        if ApprovalLevel.objects.filter(workflow_type=workflow_type, job_title=job_title).exists():
            raise ValidationError(f"المسمى الوظيفي {job_title.title_name} مستخدم مسبقاً في {config['title']}.")
        last_level = (
            ApprovalLevel.objects.select_for_update()
            .filter(workflow_type=workflow_type)
            .order_by('-order_number')
            .first()
        )
        new_order = last_level.order_number + 1 if last_level else 1
        ApprovalLevel.objects.create(
            workflow_type=workflow_type,
            order_number=new_order,
            job_title=job_title,
        )
        sync_legacy_approval_fields(workflow_type)
        return new_order


def approval_level_removal_error(level, config):
    if config['workflow_type'] == ApprovalWorkflow.ACHIEVEMENT_REPORT:
        if CompleteReportApproval.objects.filter(approval_level=level).exists():
            return "لا يمكن حذف هذا المستوى لأنه مستخدم في سجلات تعميد تقارير إنجاز سابقة."
        if CompleteReport.objects.filter(status='awaiting_acceptenace', acceptance__lt=level.order_number).exists():
            return "لا يمكن حذف هذا المستوى لوجود تقارير إنجاز قيد التعميد تعتمد عليه."
        return None

    if SparePartApproval.objects.filter(approval_level=level).exists():
        return "لا يمكن حذف هذا المستوى لأنه مستخدم في سجلات تعميد طلبات قطع غيار سابقة."
    if SparePartRequest.objects.filter(
        order_kind=config['spare_order_kind'],
        acceptance__lt=level.order_number,
    ).exclude(status__in=('avaliable_parts_issued', 'rejected')).exists():
        return "لا يمكن حذف هذا المستوى لوجود طلبات قطع غيار قيد المعالجة تعتمد عليه."
    return None


def remove_last_approval_level(workflow_type):
    config = get_approval_workflow_config(workflow_type)
    with transaction.atomic():
        last_level = (
            ApprovalLevel.objects.select_for_update()
            .filter(workflow_type=workflow_type)
            .order_by('-order_number')
            .first()
        )
        if last_level is None:
            raise ValidationError("لا توجد مستويات تعميد يمكن حذفها.")
        error_message = approval_level_removal_error(last_level, config)
        if error_message:
            raise ValidationError(error_message)
        ApprovalDelegation.objects.filter(approval_level=last_level).delete()
        last_level.delete()
        sync_legacy_approval_fields(workflow_type)
        return last_level.order_number


@login_required(login_url='login')
def system_settings_view(request):
    require_role_permission(request, 'can_manage_system_settings')
    settings_obj = SystemSettings.get_solo()
    return render(request, 'system_settings.html', {
        'title': 'إعدادات النظام',
        'settings_obj': settings_obj,
        **account_navigation_context(
            'إعدادات النظام',
            section_label='الإدارة',
            show_back_button=True,
            back_url_name='home_redirect',
        ),
    })


@login_required(login_url='login')
def system_settings_customize_view(request):
    require_role_permission(request, 'can_manage_system_settings')
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
                description="تم تخصيص إعدادات النظام العامة.",
                request=request,
            )
            messages.success(request, "تم حفظ تخصيص إعدادات النظام بنجاح.")
            return redirect('system_settings')
        messages.error(request, "تعذر حفظ إعدادات النظام. يرجى مراجعة الحقول.")
    else:
        form = SystemSettingsForm(instance=settings_obj)

    return render(request, 'system_settings_customize.html', {
        'title': 'تخصيص إعدادات النظام',
        'form': form,
        'settings_obj': settings_obj,
        **account_navigation_context(
            'تخصيص إعدادات النظام',
            section_label='إعدادات النظام',
            section_url_name='system_settings',
            show_back_button=True,
            back_url_name='system_settings',
        ),
    })


@login_required(login_url='login')
def approval_hierarchy_settings_view(request):
    require_role_permission(request, 'can_manage_system_settings')
    if request.method == 'POST':
        workflow_action = request.POST.get('workflow_action')
        if workflow_action and '|' in workflow_action:
            action, workflow_type = workflow_action.split('|', 1)
        else:
            action = request.POST.get('action')
            workflow_type = request.POST.get('workflow_type')
        try:
            if action == 'save':
                save_approval_hierarchy_assignments(request.POST)
                messages.success(request, "تم حفظ إعدادات التعميدات بنجاح.")
            elif action == 'add_level':
                config = get_approval_workflow_config(workflow_type)
                job_title_id = request.POST.get(f"new_job_title_{workflow_type}")
                new_order = add_approval_level(workflow_type, job_title_id)
                messages.success(request, f"تمت إضافة المستوى {new_order} إلى {config['title']}.")
            elif action == 'remove_last_level':
                config = get_approval_workflow_config(workflow_type)
                removed_order = remove_last_approval_level(workflow_type)
                messages.success(request, f"تم حذف المستوى {removed_order} من {config['title']}.")
            else:
                messages.error(request, "الإجراء المطلوب غير صحيح.")
        except (ValidationError, Http404) as error:
            messages.error(request, validation_error_text(error))
        return redirect('approval_hierarchy_settings')

    return render(request, 'approval_hierarchy_settings.html', {
        'title': 'إعدادات التعميدات',
        'sections': approval_hierarchy_sections(),
        'job_titles': JobTitle.objects.order_by('title_name'),
        **account_navigation_context(
            'إعدادات التعميدات',
            section_label='إعدادات النظام',
            section_url_name='system_settings',
            show_back_button=True,
            back_url_name='system_settings',
        ),
    })


@login_required(login_url='login')
def home_redirect(request):
    permissions = get_effective_permissions(request.user)

    if permissions and permissions.can_view_dashboard:
        return redirect('maintenance_dashboard')

    if user_has_role_permission(request.user, 'can_view_employees'):
        return redirect('employees')
    if user_has_role_permission(request.user, 'can_view_job_titles'):
        return redirect('roles')
    if user_has_role_permission(request.user, 'can_manage_engineer_specialties'):
        return redirect('specialties')
    if user_has_any_role_permission(
        request.user,
        'can_view_organization_structure',
        'can_manage_departments',
        'can_manage_buildings',
        'can_manage_floors',
    ):
        return redirect('departments')
    if user_has_role_permission(request.user, 'can_manage_system_settings'):
        return redirect('system_settings')
    if user_has_role_permission(request.user, 'can_manage_backups'):
        return redirect('backup_list')
    if user_can_view_audit_log(request.user):
        return redirect('audit_logs')

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
    required_role_permission = 'can_view_employees'
    model = User
    template_name = 'employees.html'
    context_object_name = 'employees'

    def get_queryset(self):
        queryset = employee_queryset()
        params = self.request.GET

        query = _get_filter_value(params, 'q')
        if query:
            queryset = queryset.filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(username__icontains=query)
                | Q(email__icontains=query)
                | Q(profile__phone_number__icontains=query)
                | Q(profile__employee_id__icontains=query)
            )

        job_title = _get_filter_value(params, 'job_title')
        if job_title.isdigit():
            queryset = queryset.filter(profile__job_title_id=int(job_title))

        department = _get_filter_value(params, 'department')
        if department.isdigit():
            queryset = queryset.filter(profile__managing_department_id=int(department))

        status = _get_filter_value(params, 'status')
        if status == 'active':
            queryset = queryset.filter(is_active=True)
        elif status == 'inactive':
            queryset = queryset.filter(is_active=False)

        is_engineer = _get_boolean_filter_value(params, 'is_engineer')
        if is_engineer is not None:
            queryset = queryset.filter(profile__job_title__permissions__is_engineer=is_engineer)

        is_department_manager = _get_boolean_filter_value(params, 'is_department_manager')
        if is_department_manager is not None:
            queryset = queryset.filter(profile__job_title__permissions__is_department_manager=is_department_manager)

        ordering = _get_filter_value(params, 'ordering')
        ordering_map = {
            'newest': '-date_joined',
            'oldest': 'date_joined',
            'name': 'first_name',
            '-name': '-first_name',
            'username': 'username',
            'status': '-is_active',
            'department': 'profile__managing_department__name',
            'job_title': 'profile__job_title__title_name',
        }
        return queryset.distinct().order_by(ordering_map.get(ordering, 'first_name'), 'last_name', 'username')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        job_title_options = list(JobTitle.objects.order_by('title_name'))
        department_options = list(Department.objects.select_related(
            'floor',
            'floor__building',
        ).order_by('name'))
        context['filters'] = {
            'q': _get_filter_value(self.request.GET, 'q'),
            'job_title': _get_filter_value(self.request.GET, 'job_title'),
            'department': _get_filter_value(self.request.GET, 'department'),
            'status': _get_filter_value(self.request.GET, 'status'),
            'is_engineer': _get_filter_value(self.request.GET, 'is_engineer'),
            'is_department_manager': _get_filter_value(self.request.GET, 'is_department_manager'),
            'ordering': _get_filter_value(self.request.GET, 'ordering'),
        }
        applied_filters = []
        _append_applied_filter(applied_filters, self.request, 'q', 'بحث', context['filters']['q'])
        _append_applied_filter(
            applied_filters,
            self.request,
            'job_title',
            'المسمى الوظيفي',
            _label_from_queryset(job_title_options, context['filters']['job_title'], attr_name='title_name'),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'department',
            'القسم',
            _label_from_queryset(department_options, context['filters']['department'], attr_name='name'),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'status',
            'الحالة',
            EMPLOYEE_STATUS_FILTER_LABELS.get(context['filters']['status'], context['filters']['status']),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'is_engineer',
            'مهندس',
            YES_NO_FILTER_LABELS.get(context['filters']['is_engineer'], context['filters']['is_engineer']),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'is_department_manager',
            'رئيس قسم',
            YES_NO_FILTER_LABELS.get(context['filters']['is_department_manager'], context['filters']['is_department_manager']),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'ordering',
            'الترتيب',
            EMPLOYEE_ORDERING_FILTER_LABELS.get(context['filters']['ordering'], context['filters']['ordering']),
        )
        context['can_add_employees'] = user_has_role_permission(self.request.user, 'can_add_employees')
        context['filters'] = {
            'q': _get_filter_value(self.request.GET, 'q'),
            'job_title': _get_filter_value(self.request.GET, 'job_title'),
            'department': _get_filter_value(self.request.GET, 'department'),
            'status': _get_filter_value(self.request.GET, 'status'),
            'is_engineer': _get_filter_value(self.request.GET, 'is_engineer'),
            'is_department_manager': _get_filter_value(self.request.GET, 'is_department_manager'),
            'ordering': _get_filter_value(self.request.GET, 'ordering'),
        }
        context['has_active_filters'] = _has_admin_filters(self.request.GET)
        context['filter_query_string'] = _query_string_without_page(self.request.GET)
        context['applied_filters'] = applied_filters
        context['applied_filters_count'] = len(applied_filters)
        context['job_title_options'] = job_title_options
        context['department_options'] = department_options
        context.update(account_navigation_context(
            'الموظفون',
            section_label='إدارة الموظفين',
        ))
        return context


@login_required(login_url='login')
@transaction.atomic
def AddEmployee(request):
    require_role_permission(request, 'can_add_employees')
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
        **account_navigation_context(
            'إضافة موظف',
            section_label='الموظفون',
            section_url_name='employees',
            show_back_button=True,
            back_url_name='employees',
        ),
    })


@login_required(login_url='login')
def EmployeeDetail(request, pk):
    if  pk == request.user.pk :
        return redirect('profile')
    require_role_permission(request, 'can_view_employees')
    employee = get_object_or_404(employee_queryset(), pk=pk)
    can_edit_employee = user_has_role_permission(request.user, 'can_edit_employees')
    can_delete_employee = user_has_role_permission(request.user, 'can_delete_employees')
    can_edit_job_title_permissions = user_has_role_permission(request.user, 'can_edit_job_titles_permissions')
    can_manage_user_permissions = user_can_manage_permission_overrides(request.user)

    context = get_employee_context(employee)
    context.update({
        'title': 'Employee Details',
        'can_manage_employee': can_edit_employee or can_delete_employee or can_edit_job_title_permissions or can_manage_user_permissions,
        'can_edit_employee': can_edit_employee,
        'can_delete_employee': can_delete_employee,
        'can_edit_job_title_permissions': can_edit_job_title_permissions,
        'can_manage_user_permissions': can_manage_user_permissions,
        **account_navigation_context(
            f'تفاصيل الموظف #{employee.pk}',
            section_label='الموظفون',
            section_url_name='employees',
            show_back_button=True,
            back_url_name='employees',
        ),
    })
    return render(request, 'employee_detail.html', {
        **context,
    })


@login_required(login_url='login')
def EmployeePermissions(request, pk):
    if not user_can_manage_permission_overrides(request.user):
        raise PermissionDenied("ليس لديك صلاحية إدارة صلاحيات المستخدمين.")
    employee = get_object_or_404(employee_queryset(), pk=pk)
    if employee == request.user:
        messages.error(request, "لا يمكنك تعديل صلاحيات حسابك من هذه الصفحة.")
        return redirect('profile')

    permission_data = build_user_permission_management_data(employee)
    form_kwargs = {
        'user': employee,
        'grant_choices': permission_data['grant_choices'],
        'deny_choices': permission_data['deny_choices'],
        'manageable_permission_names': permission_data['manageable_permission_names'],
    }
    form = UserPermissionOverrideForm(**form_kwargs)

    if request.method == 'POST':
        form = UserPermissionOverrideForm(request.POST, **form_kwargs)
        if form.is_valid():
            grant_permissions = set(form.cleaned_data.get('grant_permissions') or [])
            deny_permissions = set(form.cleaned_data.get('deny_permissions') or [])
            role_permissions = get_user_role_permissions(employee)
            role_has_system_setup = bool(role_permissions and role_permissions.can_manage_system_setup)
            auth_has_system_setup = employee.has_perm(SYSTEM_SETUP_PERMISSION)
            will_have_system_setup = bool(
                (role_has_system_setup or auth_has_system_setup or 'can_manage_system_setup' in grant_permissions)
                and 'can_manage_system_setup' not in deny_permissions
            )
            if user_can_manage_system_setup(employee) and not will_have_system_setup:
                try:
                    ensure_at_least_one_system_admin_exists(exclude_user=employee)
                except PermissionDenied as error:
                    form.add_error(None, str(error))
                else:
                    old_data = list(UserPermissionOverride.objects.filter(user=employee).values('permission_name', 'action'))
                    with transaction.atomic():
                        form.save()
                    new_data = list(UserPermissionOverride.objects.filter(user=employee).values('permission_name', 'action'))
                    create_audit_log(
                        request.user,
                        AuditLog.ACTION_UPDATE,
                        employee,
                        old_data={'permission_overrides': old_data},
                        new_data={'permission_overrides': new_data},
                        description=f"تم تعديل الصلاحيات الخاصة بالمستخدم {employee.get_full_name() or employee.username}",
                        request=request,
                    )
                    messages.success(request, "تم حفظ صلاحيات الموظف الخاصة بنجاح.")
                    return redirect('employee_permissions', pk=employee.pk)
            else:
                old_data = list(UserPermissionOverride.objects.filter(user=employee).values('permission_name', 'action'))
                with transaction.atomic():
                    form.save()
                new_data = list(UserPermissionOverride.objects.filter(user=employee).values('permission_name', 'action'))
                create_audit_log(
                    request.user,
                    AuditLog.ACTION_UPDATE,
                    employee,
                    old_data={'permission_overrides': old_data},
                    new_data={'permission_overrides': new_data},
                    description=f"تم تعديل الصلاحيات الخاصة بالمستخدم {employee.get_full_name() or employee.username}",
                    request=request,
                )
                messages.success(request, "تم حفظ صلاحيات الموظف الخاصة بنجاح.")
                return redirect('employee_permissions', pk=employee.pk)

    context = get_employee_context(employee)
    context.update({
        'title': f"إدارة صلاحيات الموظف: {employee.get_full_name() or employee.username}",
        'form': form,
        'permission_data': permission_data,
        'selected_grants': set(form['grant_permissions'].value() or []),
        'selected_denies': set(form['deny_permissions'].value() or []),
        'can_edit_job_title_permissions': user_has_role_permission(request.user, 'can_edit_job_titles_permissions'),
        **account_navigation_context(
            f'صلاحيات الموظف #{employee.pk}',
            section_label='الموظفون',
            section_url_name='employees',
            show_back_button=True,
            back_url_name='employee_detail',
            back_url_kwargs={'pk': employee.pk},
        ),
    })
    return render(request, 'employee_permissions.html', context)


@login_required(login_url='login')
def Profile(request):
    employee = get_object_or_404(employee_queryset(), pk=request.user.id)
    context = get_employee_context(employee)
    context.update({
        'title': 'My Profile',
        'can_manage_employee': False,
        **account_navigation_context(
            'الملف الشخصي',
            show_back_button=True,
            back_url_name='home_redirect',
        ),
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
        **account_navigation_context(
            'تغيير كلمة المرور',
            section_label='الملف الشخصي',
            section_url_name='profile',
            show_back_button=True,
            back_url_name='profile',
        ),
    })


@login_required(login_url='login')
@transaction.atomic
def EditEmployee(request, pk):
    require_role_permission(request, 'can_edit_employees')
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
            old_can_manage = user_can_manage_system_setup(employee)
            new_can_manage = bool(
                new_job_title
                and hasattr(new_job_title, 'permissions')
                and new_job_title.permissions.can_manage_system_setup
            )
            system_setup_overrides = set(
                UserPermissionOverride.objects.filter(
                    user=employee,
                    permission_name='can_manage_system_setup',
                ).values_list('action', flat=True)
            )
            if UserPermissionOverride.ACTION_GRANT in system_setup_overrides:
                new_can_manage = True
            if UserPermissionOverride.ACTION_DENY in system_setup_overrides:
                new_can_manage = False
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
                        **account_navigation_context(
                            f'تعديل الموظف #{employee.pk}',
                            section_label='الموظفون',
                            section_url_name='employees',
                            show_back_button=True,
                            back_url_name='employee_detail',
                            back_url_kwargs={'pk': employee.pk},
                        ),
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
        **account_navigation_context(
            f'تعديل الموظف #{employee.pk}',
            section_label='الموظفون',
            section_url_name='employees',
            show_back_button=True,
            back_url_name='employee_detail',
            back_url_kwargs={'pk': employee.pk},
        ),
    })


@login_required(login_url='login')
def DeleteEmployee(request, pk):
    require_role_permission(request, 'can_delete_employees')
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
    require_role_permission(request, 'can_edit_employees')
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
    required_role_permission = 'can_view_job_titles'
    model = JobTitle
    template_name = 'roles.html'
    context_object_name = 'job_titles'

    def get_queryset(self):
        queryset = JobTitle.objects.select_related('permissions')
        params = self.request.GET

        query = _get_filter_value(params, 'q')
        if query:
            queryset = queryset.filter(title_name__icontains=query)

        is_engineer = _get_boolean_filter_value(params, 'is_engineer')
        if is_engineer is not None:
            queryset = queryset.filter(permissions__is_engineer=is_engineer)

        is_department_manager = _get_boolean_filter_value(params, 'is_department_manager')
        if is_department_manager is not None:
            queryset = queryset.filter(permissions__is_department_manager=is_department_manager)

        can_manage_system_setup = _get_boolean_filter_value(params, 'can_manage_system_setup')
        if can_manage_system_setup is not None:
            queryset = queryset.filter(permissions__can_manage_system_setup=can_manage_system_setup)

        employee_permissions = _get_boolean_filter_value(params, 'employee_permissions')
        if employee_permissions is not None:
            employee_permissions_query = (
                Q(permissions__can_view_employees=True)
                | Q(permissions__can_add_employees=True)
                | Q(permissions__can_edit_employees=True)
                | Q(permissions__can_delete_employees=True)
            )
            queryset = queryset.filter(employee_permissions_query if employee_permissions else ~employee_permissions_query)

        job_title_permissions = _get_boolean_filter_value(params, 'job_title_permissions')
        if job_title_permissions is not None:
            job_title_permissions_query = (
                Q(permissions__can_view_job_titles=True)
                | Q(permissions__can_add_job_titles=True)
                | Q(permissions__can_edit_job_titles_permissions=True)
                | Q(permissions__can_delete_job_titles=True)
            )
            queryset = queryset.filter(job_title_permissions_query if job_title_permissions else ~job_title_permissions_query)

        organization_permissions = _get_boolean_filter_value(params, 'organization_permissions')
        if organization_permissions is not None:
            organization_permissions_query = (
                Q(permissions__can_view_organization_structure=True)
                | Q(permissions__can_manage_departments=True)
                | Q(permissions__can_manage_buildings=True)
                | Q(permissions__can_manage_floors=True)
                | Q(permissions__can_manage_engineer_specialties=True)
            )
            queryset = queryset.filter(organization_permissions_query if organization_permissions else ~organization_permissions_query)

        ordering = _get_filter_value(params, 'ordering')
        ordering_map = {
            'newest': '-created_at',
            'oldest': 'created_at',
            'name': 'title_name',
            '-name': '-title_name',
        }
        return queryset.distinct().order_by(ordering_map.get(ordering, 'title_name'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['can_add_job_titles'] = user_has_role_permission(self.request.user, 'can_add_job_titles')
        context['can_edit_job_titles_permissions'] = user_has_role_permission(self.request.user, 'can_edit_job_titles_permissions')
        context['can_delete_job_titles'] = user_has_role_permission(self.request.user, 'can_delete_job_titles')
        context['filters'] = {
            'q': _get_filter_value(self.request.GET, 'q'),
            'is_engineer': _get_filter_value(self.request.GET, 'is_engineer'),
            'is_department_manager': _get_filter_value(self.request.GET, 'is_department_manager'),
            'can_manage_system_setup': _get_filter_value(self.request.GET, 'can_manage_system_setup'),
            'employee_permissions': _get_filter_value(self.request.GET, 'employee_permissions'),
            'job_title_permissions': _get_filter_value(self.request.GET, 'job_title_permissions'),
            'organization_permissions': _get_filter_value(self.request.GET, 'organization_permissions'),
            'ordering': _get_filter_value(self.request.GET, 'ordering'),
        }
        context['has_active_filters'] = _has_admin_filters(self.request.GET)
        context['filter_query_string'] = _query_string_without_page(self.request.GET)
        applied_filters = []
        _append_applied_filter(applied_filters, self.request, 'q', 'بحث', context['filters']['q'])
        _append_applied_filter(
            applied_filters,
            self.request,
            'is_engineer',
            'مهندس',
            YES_NO_FILTER_LABELS.get(context['filters']['is_engineer'], context['filters']['is_engineer']),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'is_department_manager',
            'رئيس قسم',
            YES_NO_FILTER_LABELS.get(context['filters']['is_department_manager'], context['filters']['is_department_manager']),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'can_manage_system_setup',
            'تهيئة النظام',
            YES_NO_FILTER_LABELS.get(context['filters']['can_manage_system_setup'], context['filters']['can_manage_system_setup']),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'employee_permissions',
            'صلاحيات الموظفين',
            YES_NO_FILTER_LABELS.get(context['filters']['employee_permissions'], context['filters']['employee_permissions']),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'job_title_permissions',
            'صلاحيات المسميات',
            YES_NO_FILTER_LABELS.get(context['filters']['job_title_permissions'], context['filters']['job_title_permissions']),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'organization_permissions',
            'صلاحيات البنية التنظيمية',
            YES_NO_FILTER_LABELS.get(context['filters']['organization_permissions'], context['filters']['organization_permissions']),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'ordering',
            'الترتيب',
            NAME_ORDERING_FILTER_LABELS.get(context['filters']['ordering'], context['filters']['ordering']),
        )
        context['applied_filters'] = applied_filters
        context['applied_filters_count'] = len(applied_filters)
        context.update(account_navigation_context(
            'المسميات الوظيفية',
            section_label='إدارة الموظفين',
        ))
        return context


JOB_TITLE_DETAIL_PERMISSION_GROUPS = (
    ('صلاحيات إدارة النظام', (
        'can_manage_system_setup',
        'can_manage_system_settings',
        'can_manage_user_permissions',
        'can_view_audit_logs',
        'can_view_audit_log',
        'can_manage_backups',
    )),
    ('صلاحيات إدارة الموظفين', (
        'can_view_employees',
        'can_add_employees',
        'can_edit_employees',
        'can_delete_employees',
    )),
    ('صلاحيات المسميات الوظيفية', (
        'can_view_job_titles',
        'can_add_job_titles',
        'can_edit_job_titles_permissions',
        'can_delete_job_titles',
    )),
    ('صلاحيات البنية التنظيمية', (
        'can_view_organization_structure',
        'can_manage_departments',
        'can_manage_buildings',
        'can_manage_floors',
        'can_manage_engineer_specialties',
    )),
    ('صلاحيات الصيانة', (
        'can_add_maintenance',
        'can_approve_maintenance',
        'can_edit_pending_maintenance',
        'can_view_all_departments',
        'can_view_dashboard',
    )),
    ('صلاحيات قطع الغيار', (
        'can_add_spare_parts',
        'can_edit_delete_pending_parts',
        'can_approve_store_requisition',
        'can_approve_purchase_order',
        'can_issue_from_store',
        'can_confirm_issuance',
    )),
    ('صلاحيات تقرير الإنجاز', (
        'can_add_achievement_report',
        'can_edit_delete_pending_report',
        'can_approve_achievement_report',
    )),
)


JOB_TITLE_DETAIL_APPROVAL_WORKFLOWS = (
    (ApprovalWorkflow.PURCHASE, 'مستوى تعميد الشراء'),
    (ApprovalWorkflow.STORE_ISSUE, 'مستوى تعميد الصرف من المخازن'),
    (ApprovalWorkflow.ACHIEVEMENT_REPORT, 'مستوى تعميد تقرير الإنجاز'),
)


def enabled_job_title_permission_groups(permissions):
    if not permissions:
        return []

    groups = []
    for group_label, field_names in JOB_TITLE_DETAIL_PERMISSION_GROUPS:
        enabled_permissions = []
        for field_name in field_names:
            if not getattr(permissions, field_name, False):
                continue
            field = permissions._meta.get_field(field_name)
            enabled_permissions.append({
                'name': field_name,
                'label': field.verbose_name,
            })
        if enabled_permissions:
            groups.append({
                'label': group_label,
                'permissions': enabled_permissions,
            })
    return groups


def job_title_approval_summary(job_title):
    normal_levels = []
    for workflow_type, label in JOB_TITLE_DETAIL_APPROVAL_WORKFLOWS:
        levels = list(
            ApprovalLevel.objects.filter(
                workflow_type=workflow_type,
                job_title=job_title,
            ).order_by('order_number')
        )
        normal_levels.append({
            'workflow_type': workflow_type,
            'label': label,
            'levels': levels,
        })

    workflow_labels = {
        workflow_type: label
        for workflow_type, label in JOB_TITLE_DETAIL_APPROVAL_WORKFLOWS
    }
    delegated_levels = []
    for delegation in (
        ApprovalDelegation.objects.filter(source_job_title=job_title)
        .select_related('approval_level', 'approval_level__job_title')
        .order_by('approval_level__workflow_type', 'approval_level__order_number')
    ):
        delegated_levels.append({
            'workflow_label': workflow_labels.get(
                delegation.approval_level.workflow_type,
                delegation.approval_level.workflow_type,
            ),
            'order_number': delegation.approval_level.order_number,
            'required_job_title': delegation.approval_level.job_title,
        })

    return {
        'normal_levels': normal_levels,
        'delegated_levels': delegated_levels,
    }


class JobTitleDetail(SystemAdminRequiredMixin, DetailView):
    required_role_permission = 'can_view_job_titles'
    model = JobTitle
    template_name = 'job_title_detail.html'
    context_object_name = 'job_title'

    def get_queryset(self):
        return JobTitle.objects.select_related('permissions')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            permissions = self.object.permissions
        except JobTitlePermission.DoesNotExist:
            permissions = None

        context.update({
            'permissions': permissions,
            'employees': (
                self.object.job_titles_profiles
                .select_related('user', 'managing_department')
                .order_by('user__first_name', 'user__last_name', 'user__username')
            ),
            'permission_groups': enabled_job_title_permission_groups(permissions),
            'approval_summary': job_title_approval_summary(self.object),
            'can_edit_job_title_permissions': user_has_role_permission(
                self.request.user,
                'can_edit_job_titles_permissions',
            ),
            **account_navigation_context(
                f'تفاصيل المسمى الوظيفي #{self.object.pk}',
                section_label='المسميات الوظيفية',
                section_url_name='roles',
                show_back_button=True,
                back_url_name='roles',
            ),
        })
        return context


class AddJobTitle(SystemAdminRequiredMixin, CreateView):
    required_role_permission = 'can_add_job_titles'
    model = JobTitle
    form_class = JobTitleForm
    template_name = 'add.html'
    success_url = '/accounts/roles/'

    def form_valid(self, form):
        self.object = form.save()
        create_audit_log(
            self.request.user,
            AuditLog.ACTION_CREATE,
            self.object,
            new_data=serialize_instance(self.object),
            description=f"تم إنشاء المسمى الوظيفي {self.object.title_name}",
            request=self.request,
        )
        messages.success(self.request, "تم إنشاء المسمى الوظيفي بنجاح، يمكنك الآن ضبط الصلاحيات.")
        return redirect('job_title_permissions', pk=self.object.pk)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'إضافة مسمى وظيفي'
        context.update(account_navigation_context(
            'إضافة مسمى وظيفي',
            section_label='المسميات الوظيفية',
            section_url_name='roles',
            show_back_button=True,
            back_url_name='roles',
        ))
        return context


class DeleteJobTitle(SystemAdminRequiredMixin, DeleteView):
    required_role_permission = 'can_delete_job_titles'
    model = JobTitle
    success_url = '/accounts/roles/'

    def post(self, request, *args, **kwargs):
        job_title = self.get_object()
        if job_title.job_titles_profiles.exists():
            messages.error(request, "لا يمكن حذف المسمى الوظيفي لأنه مرتبط بملفات تعريف المستخدمين.")
            return redirect('roles')
        if job_title.approval_levels.exists():
            messages.error(request, "لا يمكن حذف المسمى الوظيفي لأنه مستخدم في مراحل التعميد.")
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
    require_role_permission(request, 'can_edit_job_titles_permissions')
    job_title = get_object_or_404(JobTitle, pk=pk)
    permissions, _ = JobTitlePermission.objects.get_or_create(job_title=job_title)
    old_data = serialize_instance(permissions)
    job_title_name = job_title.title_name
    job_title_name_error = None

    if request.method == 'POST':
        job_title_name = (request.POST.get('job_title_name') or '').strip()
        if not job_title_name:
            job_title_name_error = "اسم المسمى الوظيفي مطلوب."
        elif JobTitle.objects.filter(title_name__iexact=job_title_name).exclude(pk=job_title.pk).exists():
            job_title_name_error = "يوجد مسمى وظيفي آخر بنفس الاسم."

        form = JobTitlePermissionForm(request.POST, instance=permissions, job_title=job_title)
        if job_title_name_error:
            form.is_valid()
        elif form.is_valid():
            try:
                old_job_title_name = job_title.title_name
                with transaction.atomic():
                    if job_title.title_name != job_title_name:
                        job_title.title_name = job_title_name
                        job_title.full_clean()
                        job_title.save(update_fields=['title_name'])

                    permission_instance = form.save(commit=False)
                    permission_instance.job_title = job_title
                    if permissions.can_manage_system_setup and not permission_instance.can_manage_system_setup:
                        ensure_at_least_one_system_admin_exists(exclude_job_title=job_title)

                    permission_instance.full_clean()
                    permission_instance.save()
                    form.save_approval_configuration(permission_instance)

                new_data = serialize_instance(permission_instance)
                audit_old_data = old_data
                if old_job_title_name != job_title.title_name:
                    audit_old_data = {**old_data, 'job_title_name': old_job_title_name}
                    new_data = {**new_data, 'job_title_name': job_title.title_name}
                create_audit_log(
                    request.user,
                    AuditLog.ACTION_UPDATE,
                    permission_instance,
                    old_data=audit_old_data,
                    new_data=new_data,
                    description=f"تم تعديل صلاحيات المسمى الوظيفي {job_title.title_name}",
                    request=request,
                )
                messages.success(request, "تم حفظ اسم المسمى الوظيفي والصلاحيات بنجاح.")
                return redirect('roles')
            except (ValidationError, PermissionDenied) as error:
                form.add_error(None, error)
    else:
        form = JobTitlePermissionForm(instance=permissions, job_title=job_title)

    return render(request, 'job_title_permissions.html', {
        'form': form,
        'job_title': job_title,
        'job_title_name': job_title_name,
        'job_title_name_error': job_title_name_error,
        **account_navigation_context(
            f'صلاحيات {job_title.title_name}',
            section_label='المسميات الوظيفية',
            section_url_name='roles',
            show_back_button=True,
            back_url_name='roles',
        ),
    })


class SpecialtiesList(SystemAdminRequiredMixin, ListView):
    required_role_permission = 'can_manage_engineer_specialties'
    model = Specialty
    template_name = 'specialties.html'
    context_object_name = 'specialties'

    def get_queryset(self):
        queryset = Specialty.objects.all()
        query = _get_filter_value(self.request.GET, 'q')
        if query:
            queryset = queryset.filter(Q(name__icontains=query) | Q(description__icontains=query))
        ordering = _get_filter_value(self.request.GET, 'ordering')
        ordering_map = {
            'name': 'name',
            '-name': '-name',
            'newest': '-id',
            'oldest': 'id',
        }
        return queryset.order_by(ordering_map.get(ordering, 'name'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['can_manage_engineer_specialties'] = user_has_role_permission(self.request.user, 'can_manage_engineer_specialties')
        context['filters'] = {
            'q': _get_filter_value(self.request.GET, 'q'),
            'ordering': _get_filter_value(self.request.GET, 'ordering'),
        }
        context['has_active_filters'] = _has_admin_filters(self.request.GET)
        context['filter_query_string'] = _query_string_without_page(self.request.GET)
        applied_filters = []
        _append_applied_filter(applied_filters, self.request, 'q', 'بحث', context['filters']['q'])
        _append_applied_filter(
            applied_filters,
            self.request,
            'ordering',
            'الترتيب',
            NAME_ORDERING_FILTER_LABELS.get(context['filters']['ordering'], context['filters']['ordering']),
        )
        context['applied_filters'] = applied_filters
        context['applied_filters_count'] = len(applied_filters)
        context.update(account_navigation_context(
            'التخصصات',
            section_label='إدارة الموظفين',
        ))
        return context


class AddSpecialty(SystemAdminRequiredMixin, CreateView):
    required_role_permission = 'can_manage_engineer_specialties'
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
        context.update(account_navigation_context(
            'إضافة تخصص هندسي',
            section_label='التخصصات',
            section_url_name='specialties',
            show_back_button=True,
            back_url_name='specialties',
        ))
        return context


class EditSpecialty(SystemAdminRequiredMixin, UpdateView):
    required_role_permission = 'can_manage_engineer_specialties'
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
        context.update(account_navigation_context(
            f'تعديل تخصص #{self.object.pk}',
            section_label='التخصصات',
            section_url_name='specialties',
            show_back_button=True,
            back_url_name='specialties',
        ))
        return context


class DeleteSpecialty(SystemAdminRequiredMixin, DeleteView):
    required_role_permission = 'can_manage_engineer_specialties'
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
    if user_has_role_permission(request.user, 'can_view_dashboard', auth_permission='maintenance.can_view_dashboard'):
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
