from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.paginator import Paginator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from accounts.audit_log_service import create_audit_log, serialize_instance, status_change_data
from accounts.models import JobTitlePermission, Profile
from accounts.models import AuditLog
from .forms import MaintenanceRequestForm, SparePartRequestForm, SparePartsFormSet
from .models import (
    CompleteReportApproval,
    MaintenanceRequest,
    Notification,
    SparePartApproval,
    SparePartRequest,
    complete_report as CompleteReport,
)
from .notifications import (
    notify_assignment_rejected,
    notify_complete_report_accepted,
    notify_complete_report_needs_approval,
    notify_complete_report_rejected,
    notify_maintenance_assigned,
    notify_maintenance_needs_acceptance,
    notify_maintenance_rejected,
    notify_spare_part_issued,
    notify_spare_part_needs_approval,
    notify_spare_part_purchase_ready,
    notify_spare_part_ready_to_issue,
    notify_spare_part_rejected,
    notify_spare_part_waiting_confirmation,
)
from .dashboard_services import (
    dashboard_has_visible_data,
    get_approval_analytics,
    get_complete_report_analytics,
    get_dashboard_date_range,
    get_dashboard_querysets,
    get_engineer_performance_data,
    get_executive_analytics,
    get_latest_activity_data,
    get_maintenance_charts_data,
    get_maintenance_kpis,
    get_spare_part_analytics,
)
from .access import (
    get_active_maintenance_requests,
    get_active_spare_part_requests,
    get_final_maintenance_requests,
    get_final_spare_part_requests,
    get_visible_complete_reports,
    get_visible_maintenance_requests,
    get_visible_spare_part_requests,
    user_can_view_complete_report,
    user_can_view_maintenance_request,
    user_can_view_spare_part_request,
)


MAINTENANCE_REPORT_WAITING_STATUS = 'awaiting_completed_report_acceptenace'
SPARE_PART_WAITING_STATUS = 'awaiting_for_accepenace'
REPORT_WAITING_STATUS = 'awaiting_acceptenace'
FINAL_MAINTENANCE_STATUSES = ('completed', 'rejected')
FINAL_SPARE_PART_STATUSES = ('avaliable_parts_issued', 'rejected')
ACTIVE_MAINTENANCE_STATUSES = (
    'pending',
    'assigned',
    'reject_assignment',
    'accept_assignment_start_progress',
    'awaiting_parts',
    'spare_parts_rejected',
    'obtained_parts',
    'awaiting_completed_report_acceptenace',
    'completed_report_rejected',
)
ACTIVE_SPARE_PART_STATUSES = (
    'awaiting_for_accepenace',
    'waiting_purchase_accepted_parts',
    'bought_available_parts',
    'issue_available_parts_issued_waiting_confirmation',
    'avaliable_parts_issued_waiting_confirmation',
)
MAINTENANCE_SORT_OPTIONS = {
    'newest': '-created_at',
    'oldest': 'created_at',
    'updated': '-updated_at',
    'status': 'status',
    'department': 'department__name',
    'engineer': 'assigned_technician__user__first_name',
}
SPARE_PART_SORT_OPTIONS = {
    'newest': '-created_at',
    'oldest': 'created_at',
    'updated': '-updated_at',
    'status': 'status',
    'order_kind': 'order_kind',
    'acceptance_desc': '-acceptance',
    'acceptance_asc': 'acceptance',
}
MAINTENANCE_SORT_LABELS = {
    'newest': 'الأحدث أولاً',
    'oldest': 'الأقدم أولاً',
    'updated': 'آخر تحديث',
    'priority': 'الأعلى أولوية',
    'status': 'حسب الحالة',
    'department': 'حسب القسم',
    'engineer': 'حسب المهندس',
}
SPARE_PART_SORT_LABELS = {
    'newest': 'الأحدث أولاً',
    'oldest': 'الأقدم أولاً',
    'updated': 'آخر تحديث',
    'status': 'حسب الحالة',
    'order_kind': 'حسب نوع الطلب',
    'acceptance_desc': 'الأعلى تعميداً',
    'acceptance_asc': 'الأقل تعميداً',
}


def check_assined_status_and_assined_engineer(maintenance_request, user):
    if maintenance_request.status != 'assigned':
        raise PermissionDenied("لا يمكن بدء تنفيذ طلب صيانة لم يعد في حالة التعيين.")
    if maintenance_request.assigned_technician != get_profile(user):
        raise PermissionDenied("لا يمكنك بدء تنفيذ طلب صيانة لم تكن مخصصًا لك.")

def get_profile(user):
    return getattr(user, 'profile', None) if user.is_authenticated else None


def get_permissions(user):
    profile = get_profile(user)
    if not profile or not profile.job_title:
        return JobTitlePermission()
    try:
        return profile.job_title.permissions
    except JobTitlePermission.DoesNotExist:
        return JobTitlePermission(job_title=profile.job_title)


def get_user_permissions_or_403(request):
    try:
        return request.user.profile.job_title.permissions
    except (AttributeError, JobTitlePermission.DoesNotExist):
        raise PermissionDenied("لا توجد صلاحيات مرتبطة بحسابك. الرجاء التواصل مع مدير النظام.")


def redirect_after_maintenance_action(request, maintenance_request):
    if request.POST.get('next') == 'detail':
        return redirect('request_maintenance_details', pk=maintenance_request.pk)
    return redirect('request_list')


def redirect_after_spare_part_action(redirect_to, spare_part_request):
    if redirect_to == 'maintenance_detail':
        return redirect('request_maintenance_details', pk=spare_part_request.maintenance_request_id)
    return redirect(redirect_to)


def require_permission(request, permission_name, message):
    user_perms = get_user_permissions_or_403(request)
    if not getattr(user_perms, permission_name, False):
        raise PermissionDenied(message)
    return user_perms


def require_pending_status(obj, message="لا يمكن تعديل أو حذف هذا العنصر لأنه لم يعد في حالة الانتظار."):
    if obj.status != 'pending':
        raise PermissionDenied(message)


def require_spare_part_pending_status(obj):
    if obj.status not in (SPARE_PART_WAITING_STATUS, 'rejected'):
        raise PermissionDenied("لا يمكن تعديل أو حذف طلب قطع الغيار لأنه لم يعد في حالة الانتظار.")


def maintenance_queryset():
    return MaintenanceRequest.objects.select_related(
        'requester',
        'department',
        'department__floor',
        'department__floor__building',
        'required_specialty',
        'assigned_technician',
        'assigned_technician__user',
    ).prefetch_related(
        'spare_parts__spare_parts',
        'spare_parts__approvals__approver',
        'completed_report__approvals__approver',
    )


def spare_part_request_queryset():
    return SparePartRequest.objects.select_related(
        'maintenance_request',
        'maintenance_request__department',
        'maintenance_request__department__floor',
        'maintenance_request__department__floor__building',
        'maintenance_request__assigned_technician',
        'maintenance_request__assigned_technician__user',
        'engineer',
        'engineer__user',
    ).prefetch_related('spare_parts', 'approvals__approver')


def has_user_approval(obj, user):
    if not obj or not user.is_authenticated:
        return False
    prefetched = getattr(obj, '_prefetched_objects_cache', {})
    if 'approvals' in prefetched:
        return any(approval.approver_id == user.pk for approval in prefetched['approvals'])
    return obj.approvals.filter(approver=user).exists()


def latest_related(obj, related_name):
    related_objects = list(getattr(obj, related_name).all())
    return max(related_objects, key=lambda item: item.pk, default=None)


def latest_approval(obj):
    if not obj:
        return None
    approvals = list(obj.approvals.all())
    return max(approvals, key=lambda approval: approval.created_at, default=None)


def update_maintenance_status_from_spare_part_request(spare_part_request):
    status_map = {
        'rejected': 'spare_parts_rejected',
        'avaliable_parts_issued': 'obtained_parts',
    }
    maintenance_status = status_map.get(spare_part_request.status, 'awaiting_parts')
    MaintenanceRequest.objects.filter(pk=spare_part_request.maintenance_request_id).update(status=maintenance_status)


def next_spare_part_status_after_acceptance(spare_part_request):
    if spare_part_request.order_kind == 'purchase_requisition':
        return 'waiting_purchase_accepted_parts'
    return 'issue_available_parts_issued_waiting_confirmation'


def sync_spare_part_request_status(spare_part_request):
    if hasattr(spare_part_request, '_prefetched_objects_cache'):
        spare_part_request._prefetched_objects_cache.pop('spare_parts', None)

    part_acceptance_state = spare_part_request.is_accepted_all_part_or_some_none()

    if spare_part_request.status == 'rejected':
        pass
    elif part_acceptance_state == -1:
        spare_part_request.status = 'rejected'
    elif spare_part_request.acceptance >= 8:
        spare_part_request.status = next_spare_part_status_after_acceptance(spare_part_request)
    else:
        spare_part_request.status = SPARE_PART_WAITING_STATUS

    spare_part_request.save(update_fields=['status', 'acceptance', 'updated_at'])
    update_maintenance_status_from_spare_part_request(spare_part_request)


def reset_spare_part_approval(spare_part_request):
    spare_part_request.approvals.all().delete()
    spare_part_request.acceptance = 0
    spare_part_request.status = SPARE_PART_WAITING_STATUS
    spare_part_request.cause = ''
    spare_part_request.rejected_by = None
    spare_part_request.rejected_reason = ''
    spare_part_request.rejected_at = None
    spare_part_request.spare_parts.update(acceptance=0, is_rejected=False)


def reset_complete_report_approval(report):
    report.approvals.all().delete()
    report.acceptance = 0
    report.status = REPORT_WAITING_STATUS
    report.is_rejected = False
    report.cause = ''
    report.rejected_by = None
    report.rejected_reason = ''
    report.rejected_at = None
 

def visible_maintenance_requests(user, queryset):
    return get_visible_maintenance_requests(user, queryset)


def active_maintenance_queryset(queryset):
    return queryset.exclude(status__in=FINAL_MAINTENANCE_STATUSES)


def final_maintenance_queryset(queryset):
    return queryset.filter(status__in=FINAL_MAINTENANCE_STATUSES)


def visible_spare_part_requests(user, queryset):
    return get_visible_spare_part_requests(user, queryset)


def active_spare_part_queryset(queryset):
    return queryset.exclude(status__in=FINAL_SPARE_PART_STATUSES)


def final_spare_part_queryset(queryset):
    return queryset.filter(status__in=FINAL_SPARE_PART_STATUSES)


def can_approve_order(current_order, approval_order, can_bypass, max_bypass_order):
    if not approval_order:
        return False
    if current_order == approval_order - 1:
        return True
    print(f"Current order: {current_order}, Approval order: {approval_order}, Can bypass: {can_bypass}, Max bypass order: {max_bypass_order}")
    print(f"the return value is: {bool(can_bypass and max_bypass_order and (current_order >= max_bypass_order - 1 and current_order < approval_order) )}    ")
    return bool(can_bypass and max_bypass_order and (current_order >= max_bypass_order - 1 and current_order < approval_order) )


def get_spare_part_approval_settings(spare_part_request, permissions):
    if spare_part_request.order_kind == 'store_requisition':
        return {
            'permission_name': 'can_approve_store_requisition',
            'approval_order': permissions.store_approval_order,
            'can_bypass': permissions.can_bypass_store_approval,
            'max_bypass_order': permissions.max_store_bypass_order,
            'order_field': 'store_approval_order',
        }
    return {
        'permission_name': 'can_approve_purchase_order',
        'approval_order': permissions.purchase_approval_order,
        'can_bypass': permissions.can_bypass_purchase_approval,
        'max_bypass_order': permissions.max_purchase_bypass_order,
        'order_field': 'purchase_approval_order',
    }


def can_user_approve_spare_part_request(spare_part_request, permissions):
    if spare_part_request.status != SPARE_PART_WAITING_STATUS:
        return False
    settings = get_spare_part_approval_settings(spare_part_request, permissions)
    return (
        getattr(permissions, settings['permission_name'], False)
        and can_approve_order(
            spare_part_request.acceptance,
            settings['approval_order'],
            settings['can_bypass'],
            settings['max_bypass_order'],
        )
    )


def can_show_spare_part_approval_buttons(user, spare_request):
    if not spare_request or not user.is_authenticated:
        return False
    if spare_request.status != SPARE_PART_WAITING_STATUS:
        return False
    if spare_request.maintenance_request.status != 'awaiting_parts':
        return False
    if has_user_approval(spare_request, user):
        return False
    return can_user_approve_spare_part_request(spare_request, get_permissions(user))


def can_show_spare_part_reject_button(user, spare_request):
    return can_show_spare_part_approval_buttons(user, spare_request)


def can_show_maintenance_accept_button(user, maintenance_request):
    return (
        get_permissions(user).can_approve_maintenance
        and maintenance_request.status in ('pending', 'reject_assignment')
    )


def can_show_maintenance_reject_button(user, maintenance_request):
    return get_permissions(user).can_approve_maintenance and maintenance_request.status == 'pending'


def can_show_assignment_accept_button(user, maintenance_request):
    return (
        maintenance_request.status == 'assigned'
        and maintenance_request.assigned_technician == get_profile(user)
        and get_permissions(user).is_engineer
    )


def can_show_assignment_reject_button(user, maintenance_request):
    return can_show_assignment_accept_button(user, maintenance_request)


def can_show_reassign_engineer_button(user, maintenance_request):
    return get_permissions(user).can_approve_maintenance and maintenance_request.status == 'reject_assignment'


def can_show_create_spare_part_button(user, maintenance_request):
    return (
        maintenance_request.status == 'accept_assignment_start_progress'
        and maintenance_request.assigned_technician == get_profile(user)
        and get_permissions(user).can_add_spare_parts
    )


def can_show_create_complete_report_button(user, maintenance_request):
    return (
        maintenance_request.status in ('accept_assignment_start_progress', 'obtained_parts', 'completed_report_rejected')
        and maintenance_request.assigned_technician == get_profile(user)
        and get_permissions(user).can_add_achievement_report
    )


def can_show_spare_part_resubmit_button(user, spare_request):
    return (
        spare_request
        and spare_request.status == 'rejected'
        and spare_request.engineer == get_profile(user)
        and get_permissions(user).can_edit_delete_pending_parts
    )


def can_show_purchase_available_button(user, spare_request):
    return (
        spare_request
        and spare_request.status == 'waiting_purchase_accepted_parts'
        and get_permissions(user).can_issue_from_store
    )


def can_show_issue_from_store_button(user, spare_request):
    return (
        spare_request
        and spare_request.status in ('bought_available_parts', 'issue_available_parts_issued_waiting_confirmation')
        and get_permissions(user).can_issue_from_store
    )


def can_show_confirm_issuance_button(user, spare_request):
    return (
        spare_request
        and spare_request.status == 'avaliable_parts_issued_waiting_confirmation'
        and spare_request.engineer == get_profile(user)
        and get_permissions(user).can_confirm_issuance
    )


def can_user_approve_complete_report(report, permissions):
    if not report or report.status != REPORT_WAITING_STATUS:
        return False
    return (
        permissions.can_approve_achievement_report
        and can_approve_order(
            report.acceptance,
            permissions.achievement_approval_order,
            permissions.can_bypass_achievement_approval,
            permissions.max_achievement_bypass_order,
        )
    )


def can_show_complete_report_approval_buttons(user, report):
    if not report or not user.is_authenticated:
        return False
    if report.status != REPORT_WAITING_STATUS:
        return False
    if report.maintenance_request.status != MAINTENANCE_REPORT_WAITING_STATUS:
        return False
    if has_user_approval(report, user):
        return False
    return can_user_approve_complete_report(report, get_permissions(user))


def can_show_complete_report_reject_button(user, report):
    return can_show_complete_report_approval_buttons(user, report)


def can_show_complete_report_resubmit_button(user, report):
    return (
        report
        and report.status == 'rejected'
        and report.maintenance_request.assigned_technician == get_profile(user)
        and get_permissions(user).can_edit_delete_pending_report
    )


def user_can_take_maintenance_action(user, maintenance_request):
    if maintenance_request.status in FINAL_MAINTENANCE_STATUSES:
        return False

    direct_action = any((
        can_show_maintenance_accept_button(user, maintenance_request),
        can_show_maintenance_reject_button(user, maintenance_request),
        can_show_assignment_accept_button(user, maintenance_request),
        can_show_assignment_reject_button(user, maintenance_request),
        can_show_reassign_engineer_button(user, maintenance_request),
        can_show_create_spare_part_button(user, maintenance_request),
        can_show_create_complete_report_button(user, maintenance_request),
    ))
    if direct_action:
        return True

    for spare_request in maintenance_request.spare_parts.all():
        if any((
            can_show_spare_part_approval_buttons(user, spare_request),
            can_show_spare_part_resubmit_button(user, spare_request),
            can_show_purchase_available_button(user, spare_request),
            can_show_issue_from_store_button(user, spare_request),
            can_show_confirm_issuance_button(user, spare_request),
        )):
            return True

    for report in maintenance_request.completed_report.all():
        if any((
            can_show_complete_report_approval_buttons(user, report),
            can_show_complete_report_resubmit_button(user, report),
        )):
            return True

    return False


def get_user_actionable_maintenance_requests(user):
    if not user.is_authenticated:
        return []
    queryset = active_maintenance_queryset(
        visible_maintenance_requests(user, maintenance_queryset())
    ).prefetch_related(
        'spare_parts__approvals',
        'completed_report__approvals',
    )
    return [
        maintenance_request
        for maintenance_request in queryset
        if user_can_take_maintenance_action(user, maintenance_request)
    ]


def get_pending_maintenance_actions_count(user):
    return len(get_user_actionable_maintenance_requests(user))


def user_can_take_spare_part_action(user, spare_request):
    return any((
        can_show_spare_part_approval_buttons(user, spare_request),
        can_show_spare_part_resubmit_button(user, spare_request),
        can_show_purchase_available_button(user, spare_request),
        can_show_issue_from_store_button(user, spare_request),
        can_show_confirm_issuance_button(user, spare_request),
    ))


def get_user_actionable_spare_part_requests(user):
    if not user.is_authenticated:
        return []
    queryset = visible_spare_part_requests(
        user,
        spare_part_request_queryset().prefetch_related('approvals'),
    ).exclude(status='avaliable_parts_issued')
    return [
        spare_request
        for spare_request in queryset
        if user_can_take_spare_part_action(user, spare_request)
    ]


def get_pending_spare_part_actions_count(user):
    return len(get_user_actionable_spare_part_requests(user))


def _date_value(params, key):
    return parse_date(params.get(key) or '')


def _query_string_without(params, *keys):
    query = params.copy()
    for key in keys:
        query.pop(key, None)
    query.pop('page', None)
    return query.urlencode()


def _choice_label(choices, value):
    return dict(choices).get(value, value)


def apply_maintenance_filters(queryset, params, user):
    q = (params.get('q') or '').strip()
    if q:
        search_query = (
            Q(description__icontains=q)
            | Q(cause__icontains=q)
            | Q(rejected_reason__icontains=q)
            | Q(completed_report__complete_report__icontains=q)
            | Q(department__name__icontains=q)
            | Q(assigned_technician__user__first_name__icontains=q)
            | Q(assigned_technician__user__last_name__icontains=q)
            | Q(assigned_technician__user__username__icontains=q)
        )
        if q.isdigit():
            search_query |= Q(pk=int(q))
        queryset = queryset.filter(search_query).distinct()

    status = params.get('status')
    if status in ACTIVE_MAINTENANCE_STATUSES:
        queryset = queryset.filter(status=status)

    department = params.get('department')
    if department and department.isdigit():
        queryset = queryset.filter(department_id=int(department))

    engineer = params.get('engineer')
    if engineer and engineer.isdigit():
        queryset = queryset.filter(assigned_technician_id=int(engineer))

    priority = params.get('priority')
    if priority in dict(MaintenanceRequest.PRIORITY_CHOICES):
        queryset = queryset.filter(priority=priority)

    created_from = _date_value(params, 'created_from')
    if created_from:
        queryset = queryset.filter(created_at__date__gte=created_from)

    created_to = _date_value(params, 'created_to')
    if created_to:
        queryset = queryset.filter(created_at__date__lte=created_to)

    if params.get('only_my_actions') == '1':
        actionable_ids = [request.pk for request in get_user_actionable_maintenance_requests(user)]
        queryset = queryset.filter(pk__in=actionable_ids)

    return queryset


def apply_maintenance_sorting(queryset, params):
    sort = params.get('sort') or 'newest'
    if sort == 'priority':
        return queryset.annotate(
            priority_rank=Case(
                When(priority='critical', then=Value(0)),
                When(priority='high', then=Value(1)),
                When(priority='medium', then=Value(2)),
                When(priority='low', then=Value(3)),
                default=Value(4),
                output_field=IntegerField(),
            )
        ).order_by('priority_rank', '-created_at')
    return queryset.order_by(MAINTENANCE_SORT_OPTIONS.get(sort, '-created_at'))


def get_maintenance_filter_options(base_queryset, user):
    department_ids = base_queryset.values_list('department_id', flat=True).distinct()
    engineer_ids = base_queryset.exclude(assigned_technician_id__isnull=True).values_list('assigned_technician_id', flat=True).distinct()
    return {
        'statuses': [
            {'value': value, 'label': _choice_label(MaintenanceRequest.STATUS_CHOICES, value)}
            for value in ACTIVE_MAINTENANCE_STATUSES
        ],
        'priorities': [
            {'value': value, 'label': label}
            for value, label in MaintenanceRequest.PRIORITY_CHOICES
        ],
        'departments': [
            {'id': row['department_id'], 'name': row['department__name']}
            for row in base_queryset.filter(department_id__in=department_ids)
                .values('department_id', 'department__name')
                .distinct()
                .order_by('department__name')
            if row['department_id']
        ],
        'engineers': Profile.objects.filter(pk__in=engineer_ids)
            .select_related('user', 'specialty')
            .order_by('user__first_name', 'user__last_name', 'user__username'),
        'sorts': [
            {'value': value, 'label': label}
            for value, label in MAINTENANCE_SORT_LABELS.items()
        ],
    }


def get_maintenance_active_filters(params):
    filters = []
    q = (params.get('q') or '').strip()
    if q:
        filters.append({'label': 'بحث', 'value': q, 'remove_query': _query_string_without(params, 'q')})
    status = params.get('status')
    if status in ACTIVE_MAINTENANCE_STATUSES:
        filters.append({'label': 'الحالة', 'value': _choice_label(MaintenanceRequest.STATUS_CHOICES, status), 'remove_query': _query_string_without(params, 'status')})
    department = params.get('department')
    if department:
        filters.append({'label': 'القسم', 'value': department, 'remove_query': _query_string_without(params, 'department')})
    engineer = params.get('engineer')
    if engineer:
        filters.append({'label': 'المهندس', 'value': engineer, 'remove_query': _query_string_without(params, 'engineer')})
    priority = params.get('priority')
    if priority in dict(MaintenanceRequest.PRIORITY_CHOICES):
        filters.append({'label': 'الأولوية', 'value': _choice_label(MaintenanceRequest.PRIORITY_CHOICES, priority), 'remove_query': _query_string_without(params, 'priority')})
    if params.get('created_from'):
        filters.append({'label': 'من تاريخ', 'value': params.get('created_from'), 'remove_query': _query_string_without(params, 'created_from')})
    if params.get('created_to'):
        filters.append({'label': 'إلى تاريخ', 'value': params.get('created_to'), 'remove_query': _query_string_without(params, 'created_to')})
    if params.get('only_my_actions') == '1':
        filters.append({'label': 'إجراء مني', 'value': 'نعم', 'remove_query': _query_string_without(params, 'only_my_actions')})
    sort = params.get('sort')
    if sort in MAINTENANCE_SORT_LABELS and sort != 'newest':
        filters.append({'label': 'الترتيب', 'value': MAINTENANCE_SORT_LABELS.get(sort, 'الأحدث أولاً'), 'remove_query': _query_string_without(params, 'sort')})
    return filters


def apply_spare_part_filters(queryset, params, user):
    q = (params.get('q') or '').strip()
    if q:
        search_query = (
            Q(description__icontains=q)
            | Q(maintenance_request__department__name__icontains=q)
            | Q(engineer__user__first_name__icontains=q)
            | Q(engineer__user__last_name__icontains=q)
            | Q(engineer__user__username__icontains=q)
            | Q(maintenance_request__assigned_technician__user__first_name__icontains=q)
            | Q(maintenance_request__assigned_technician__user__last_name__icontains=q)
            | Q(maintenance_request__assigned_technician__user__username__icontains=q)
            | Q(spare_parts__part_name__icontains=q)
        )
        if q.isdigit():
            search_query |= Q(pk=int(q)) | Q(maintenance_request_id=int(q))
        queryset = queryset.filter(search_query).distinct()

    status = params.get('status')
    if status in ACTIVE_SPARE_PART_STATUSES:
        queryset = queryset.filter(status=status)

    order_kind = params.get('order_kind')
    if order_kind in dict(SparePartRequest.order_kinds_choices):
        queryset = queryset.filter(order_kind=order_kind)

    department = params.get('department')
    if department and department.isdigit():
        queryset = queryset.filter(maintenance_request__department_id=int(department))

    engineer = params.get('engineer')
    if engineer and engineer.isdigit():
        queryset = queryset.filter(
            Q(engineer_id=int(engineer))
            | Q(maintenance_request__assigned_technician_id=int(engineer))
        )

    created_from = _date_value(params, 'created_from')
    if created_from:
        queryset = queryset.filter(created_at__date__gte=created_from)

    created_to = _date_value(params, 'created_to')
    if created_to:
        queryset = queryset.filter(created_at__date__lte=created_to)

    if params.get('only_my_actions') == '1':
        actionable_ids = [request.pk for request in get_user_actionable_spare_part_requests(user)]
        queryset = queryset.filter(pk__in=actionable_ids)

    return queryset


def apply_spare_part_sorting(queryset, params):
    sort = params.get('sort') or 'newest'
    return queryset.order_by(SPARE_PART_SORT_OPTIONS.get(sort, '-created_at'))


def get_spare_part_filter_options(base_queryset, user):
    requester_engineer_ids = base_queryset.exclude(engineer_id__isnull=True).values_list('engineer_id', flat=True)
    assigned_engineer_ids = base_queryset.exclude(
        maintenance_request__assigned_technician_id__isnull=True,
    ).values_list('maintenance_request__assigned_technician_id', flat=True)
    engineer_ids = set(requester_engineer_ids) | set(assigned_engineer_ids)
    return {
        'statuses': [
            {'value': value, 'label': _choice_label(SparePartRequest.STATUS_CHOICES, value)}
            for value in ACTIVE_SPARE_PART_STATUSES
        ],
        'order_kinds': [
            {'value': value, 'label': label}
            for value, label in SparePartRequest.order_kinds_choices
        ],
        'departments': [
            {'id': row['maintenance_request__department_id'], 'name': row['maintenance_request__department__name']}
            for row in base_queryset.values('maintenance_request__department_id', 'maintenance_request__department__name')
                .distinct()
                .order_by('maintenance_request__department__name')
            if row['maintenance_request__department_id']
        ],
        'engineers': Profile.objects.filter(pk__in=engineer_ids)
            .select_related('user', 'specialty')
            .order_by('user__first_name', 'user__last_name', 'user__username'),
        'sorts': [
            {'value': value, 'label': label}
            for value, label in SPARE_PART_SORT_LABELS.items()
        ],
    }


def get_spare_part_active_filters(params):
    filters = []
    q = (params.get('q') or '').strip()
    if q:
        filters.append({'label': 'بحث', 'value': q, 'remove_query': _query_string_without(params, 'q')})
    status = params.get('status')
    if status in ACTIVE_SPARE_PART_STATUSES:
        filters.append({'label': 'الحالة', 'value': _choice_label(SparePartRequest.STATUS_CHOICES, status), 'remove_query': _query_string_without(params, 'status')})
    order_kind = params.get('order_kind')
    if order_kind in dict(SparePartRequest.order_kinds_choices):
        filters.append({'label': 'نوع الطلب', 'value': _choice_label(SparePartRequest.order_kinds_choices, order_kind), 'remove_query': _query_string_without(params, 'order_kind')})
    department = params.get('department')
    if department:
        filters.append({'label': 'القسم', 'value': department, 'remove_query': _query_string_without(params, 'department')})
    engineer = params.get('engineer')
    if engineer:
        filters.append({'label': 'المهندس', 'value': engineer, 'remove_query': _query_string_without(params, 'engineer')})
    if params.get('created_from'):
        filters.append({'label': 'من تاريخ', 'value': params.get('created_from'), 'remove_query': _query_string_without(params, 'created_from')})
    if params.get('created_to'):
        filters.append({'label': 'إلى تاريخ', 'value': params.get('created_to'), 'remove_query': _query_string_without(params, 'created_to')})
    if params.get('only_my_actions') == '1':
        filters.append({'label': 'إجراء مني', 'value': 'نعم', 'remove_query': _query_string_without(params, 'only_my_actions')})
    sort = params.get('sort')
    if sort in SPARE_PART_SORT_LABELS and sort != 'newest':
        filters.append({'label': 'الترتيب', 'value': SPARE_PART_SORT_LABELS.get(sort, 'الأحدث أولاً'), 'remove_query': _query_string_without(params, 'sort')})
    return filters


def update_maintenance_status_from_complete_report(report):
    maintenance_request = report.maintenance_request
    if report.status == 'accepted':
        maintenance_request.status = 'completed'
        maintenance_request.completed_at = maintenance_request.completed_at or timezone.now()
        maintenance_request.save(update_fields=['status', 'completed_at', 'updated_at'])
    elif report.status == 'rejected':
        maintenance_request.status = 'completed_report_rejected'
        maintenance_request.save(update_fields=['status', 'updated_at'])
    else:
        maintenance_request.status = MAINTENANCE_REPORT_WAITING_STATUS
        maintenance_request.save(update_fields=['status', 'updated_at'])


def spare_part_approval_titles(order_kind):
    if order_kind == 'store_requisition':
        queryset = JobTitlePermission.objects.filter(
            can_approve_store_requisition=True,
            store_approval_order__isnull=False,
        )
        order_field = 'store_approval_order'
    else:
        queryset = JobTitlePermission.objects.filter(
            can_approve_purchase_order=True,
            purchase_approval_order__isnull=False,
        )
        order_field = 'purchase_approval_order'

    return {
        getattr(permission, order_field): permission.job_title.title_name
        for permission in queryset.select_related('job_title')
        if permission.job_title
    }


class OwnerOrPendingEditorRequiredMixin(LoginRequiredMixin):
    def get_queryset(self):
        return get_visible_maintenance_requests(self.request.user, maintenance_queryset())

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        require_permission(
            self.request,
            'can_edit_pending_maintenance',
            "عذراً، لا تمتلك صلاحية تعديل أو حذف طلبات الصيانة المعلقة.",
        )
        require_pending_status(obj, "لا يمكن تعديل أو حذف طلب الصيانة لأنه لم يعد في حالة الانتظار.")
        return obj


@login_required
def notifications_list(request):
    status_filter = request.GET.get('status', 'all')
    queryset = Notification.objects.filter(recipient=request.user).select_related(
        'related_maintenance_request',
        'related_spare_part_request',
        'related_complete_report',
    )
    if status_filter == 'unread':
        queryset = queryset.filter(is_read=False)
    elif status_filter == 'read':
        queryset = queryset.filter(is_read=True)
    else:
        status_filter = 'all'

    paginator = Paginator(queryset, 15)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'notifications_list.html', {
        'page_obj': page_obj,
        'notifications': page_obj.object_list,
        'status_filter': status_filter,
    })


@login_required
@require_POST
def mark_notification_as_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.is_read = True
    notification.save(update_fields=['is_read'])
    messages.success(request, "تم تعليم الإشعار كمقروء.")
    return redirect('notifications_list')


@login_required
@require_POST
def mark_all_notifications_as_read(request):
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    messages.success(request, "تم تعليم جميع الإشعارات كمقروءة.")
    return redirect('notifications_list')


@login_required
def notification_redirect(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=['is_read'])
    if (
        notification.related_maintenance_request
        and not user_can_view_maintenance_request(request.user, notification.related_maintenance_request)
    ):
        messages.error(request, "ليس لديك صلاحية الوصول إلى طلب الصيانة المرتبط بهذا الإشعار.")
        raise PermissionDenied("ليس لديك صلاحية الوصول إلى طلب الصيانة المرتبط بهذا الإشعار.")
    if (
        notification.related_spare_part_request
        and not user_can_view_spare_part_request(request.user, notification.related_spare_part_request)
    ):
        messages.error(request, "ليس لديك صلاحية الوصول إلى طلب قطع الغيار المرتبط بهذا الإشعار.")
        raise PermissionDenied("ليس لديك صلاحية الوصول إلى طلب قطع الغيار المرتبط بهذا الإشعار.")
    if (
        notification.related_complete_report
        and not user_can_view_complete_report(request.user, notification.related_complete_report)
    ):
        messages.error(request, "ليس لديك صلاحية الوصول إلى تقرير الإنجاز المرتبط بهذا الإشعار.")
        raise PermissionDenied("ليس لديك صلاحية الوصول إلى تقرير الإنجاز المرتبط بهذا الإشعار.")
    if notification.url and url_has_allowed_host_and_scheme(notification.url, allowed_hosts={request.get_host()}):
        return redirect(notification.url)
    return redirect('notifications_list')


class MaintenanceDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'maintenance_dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        permissions = get_permissions(request.user)
        can_view_dashboard = request.user.has_perm('maintenance.can_view_dashboard') or permissions.can_view_dashboard
        if not can_view_dashboard:
            raise PermissionDenied("عذراً، لا تمتلك صلاحية عرض لوحة التحكم.")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        date_range = get_dashboard_date_range(self.request)
        start_dt = date_range['start_dt']
        end_dt = date_range['end_dt']
        dashboard_querysets = get_dashboard_querysets(self.request.user)
        context.update({
            'date_range': date_range,
            'dashboard_has_data': dashboard_has_visible_data(dashboard_querysets),
            'kpis': get_maintenance_kpis(start_dt, end_dt, dashboard_querysets),
            'maintenance_analytics': get_maintenance_charts_data(start_dt, end_dt, date_range['group_by'], dashboard_querysets),
            'engineer_performance': get_engineer_performance_data(start_dt, end_dt, dashboard_querysets),
            'spare_part_analytics': get_spare_part_analytics(start_dt, end_dt, dashboard_querysets),
            'report_analytics': get_complete_report_analytics(start_dt, end_dt, dashboard_querysets),
            'approval_analytics': get_approval_analytics(start_dt, end_dt, dashboard_querysets),
            'executive_analytics': get_executive_analytics(start_dt, end_dt, dashboard_querysets),
            'latest_activities': get_latest_activity_data(start_dt, end_dt, dashboard_querysets),
        })
        return context


class maintenanceRequestListView(LoginRequiredMixin, ListView):
    model = MaintenanceRequest
    template_name = 'requests_maintenance_list.html'
    context_object_name = 'objects'
    paginate_by = 12

    def get_queryset(self):
        self.base_queryset = get_active_maintenance_requests(self.request.user, maintenance_queryset())
        queryset = apply_maintenance_filters(self.base_queryset, self.request.GET, self.request.user)
        return apply_maintenance_sorting(queryset, self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        permissions = get_permissions(self.request.user)
        profile = get_profile(self.request.user)
        base_queryset = getattr(
            self,
            'base_queryset',
            get_active_maintenance_requests(self.request.user, maintenance_queryset()),
        )
        filter_options = get_maintenance_filter_options(base_queryset, self.request.user)
        active_filters = get_maintenance_active_filters(self.request.GET)
        department_labels = {str(option['id']): option['name'] for option in filter_options['departments']}
        engineer_labels = {
            str(engineer.pk): engineer.user.get_full_name() or engineer.user.username
            for engineer in filter_options['engineers']
        }
        for active_filter in active_filters:
            if active_filter['label'] == 'القسم':
                active_filter['value'] = department_labels.get(active_filter['value'], active_filter['value'])
            elif active_filter['label'] == 'المهندس':
                active_filter['value'] = engineer_labels.get(active_filter['value'], active_filter['value'])

        objects = list(context['objects'])

        for maintenance_request in objects:
            latest_report = latest_related(maintenance_request, 'completed_report')
            latest_spare_part = latest_related(maintenance_request, 'spare_parts')
            maintenance_request.latest_report = latest_report
            maintenance_request.latest_spare_part = latest_spare_part
            if latest_spare_part:
                latest_spare_part.latest_approval = latest_approval(latest_spare_part)
                latest_spare_part.approval_percent = min(100, int((latest_spare_part.acceptance / 8) * 100))
            maintenance_request.can_approve_spare_part_from_card = can_show_spare_part_approval_buttons(
                self.request.user,
                latest_spare_part,
            )
            maintenance_request.is_assigned_to_current_user = bool(profile and maintenance_request.assigned_technician == profile)
            maintenance_request.can_create_spare_part = (
                maintenance_request.is_assigned_to_current_user
                and permissions.can_add_spare_parts
                and maintenance_request.status == 'accept_assignment_start_progress'
            )
            maintenance_request.can_create_complete_report = (
                maintenance_request.is_assigned_to_current_user
                and permissions.can_add_achievement_report
                and maintenance_request.status in ('accept_assignment_start_progress', 'obtained_parts', 'completed_report_rejected')
            )
            maintenance_request.can_edit_rejected_spare_part = (
                maintenance_request.is_assigned_to_current_user
                and permissions.can_edit_delete_pending_parts
                and maintenance_request.status == 'spare_parts_rejected'
                and latest_spare_part
                and latest_spare_part.status == 'rejected'
            )
            maintenance_request.can_approve_report = (
                latest_report
                and can_user_approve_complete_report(latest_report, permissions)
                and not has_user_approval(latest_report, self.request.user)
            )
        
        context['permissions'] = permissions
        context['objects'] = objects
        context['object_list'] = objects
        context['filter_options'] = filter_options
        context['active_filters'] = active_filters
        context['filter_query_string'] = _query_string_without(self.request.GET)
        context['filters'] = {
            'q': self.request.GET.get('q', ''),
            'status': self.request.GET.get('status', ''),
            'department': self.request.GET.get('department', ''),
            'engineer': self.request.GET.get('engineer', ''),
            'priority': self.request.GET.get('priority', ''),
            'created_from': self.request.GET.get('created_from', ''),
            'created_to': self.request.GET.get('created_to', ''),
            'sort': self.request.GET.get('sort', 'newest'),
            'only_my_actions': self.request.GET.get('only_my_actions') == '1',
        }
        context['result_count'] = context['paginator'].count if context.get('is_paginated') else len(objects)
        if permissions.can_add_maintenance:
            context['formNRmaintenance'] = MaintenanceRequestForm(user=self.request.user)
        if permissions.can_approve_maintenance:
            context['engineers'] = Profile.objects.filter(specialty__isnull=False)
        if permissions.can_add_spare_parts:
            context['formRSPart'] = SparePartRequestForm()
            context['formRSParts'] = SparePartsFormSet(prefix='spare_parts')
        context['list_title'] = 'طلبات الصيانة النشطة'
        context['list_description'] = 'طلبات الصيانة النشطة التي لم تكتمل بعد'
        return context


class maintenanceRequestHistoryView(LoginRequiredMixin, ListView):
    model = MaintenanceRequest
    template_name = 'requests_maintenance_history.html'
    context_object_name = 'objects'

    def get_queryset(self):
        return get_final_maintenance_requests(self.request.user, maintenance_queryset())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['permissions'] = get_permissions(self.request.user)
        context['list_title'] = 'سجل طلبات الصيانة المكتملة والمرفوضة'
        context['list_description'] = 'يعرض هذا السجل الطلبات التي انتهت بحالة مكتملة أو مرفوضة فقط'
        return context


class maintenanceRequestDetails(LoginRequiredMixin, DetailView):
    model = MaintenanceRequest
    template_name = 'request_maintenance_details.html'
    context_object_name = 'objects'

    def get_queryset(self):
        return get_visible_maintenance_requests(self.request.user, maintenance_queryset())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        maintenance_request = context['object']
        user = self.request.user
        permissions = get_permissions(user)
        latest_report = latest_related(maintenance_request, 'completed_report')
        spare_part_requests = list(maintenance_request.spare_parts.all())

        for spare_request in spare_part_requests:
            spare_request.latest_approval = latest_approval(spare_request)
            spare_request.approval_percent = min(100, int((spare_request.acceptance / 8) * 100))
            spare_request.can_approve = can_show_spare_part_approval_buttons(user, spare_request)
            spare_request.can_reject = can_show_spare_part_reject_button(user, spare_request)
            spare_request.can_resubmit = can_show_spare_part_resubmit_button(user, spare_request)
            spare_request.can_mark_available = can_show_purchase_available_button(user, spare_request)
            spare_request.can_issue = can_show_issue_from_store_button(user, spare_request)
            spare_request.can_confirm = can_show_confirm_issuance_button(user, spare_request)

        if latest_report:
            latest_report.latest_approval = latest_approval(latest_report)
            latest_report.approval_percent = min(100, int((latest_report.acceptance / 5) * 100))
            latest_report.can_approve = can_show_complete_report_approval_buttons(user, latest_report)
            latest_report.can_reject = can_show_complete_report_reject_button(user, latest_report)
            latest_report.can_resubmit = can_show_complete_report_resubmit_button(user, latest_report)

        context['latest_report'] = latest_report
        context['complete_report'] = latest_report
        context['spare_part_requests'] = spare_part_requests
        context['permissions'] = permissions
        context['actions'] = {
            'can_accept_maintenance': can_show_maintenance_accept_button(user, maintenance_request),
            'can_reject_maintenance': can_show_maintenance_reject_button(user, maintenance_request),
            'can_accept_assignment': can_show_assignment_accept_button(user, maintenance_request),
            'can_reject_assignment': can_show_assignment_reject_button(user, maintenance_request),
            'can_reassign_engineer': can_show_reassign_engineer_button(user, maintenance_request),
            'can_create_spare_part': can_show_create_spare_part_button(user, maintenance_request),
            'can_create_report': can_show_create_complete_report_button(user, maintenance_request),
        }
        if permissions.can_approve_maintenance:
            context['engineers'] = Profile.objects.filter(specialty__isnull=False)
        if permissions.can_add_spare_parts:
            context['formRSPart'] = SparePartRequestForm()
            context['formRSParts'] = SparePartsFormSet(prefix='spare_parts')
        return context


class maintenanceRequestCreateView(LoginRequiredMixin, CreateView):
    model = MaintenanceRequest
    form_class = MaintenanceRequestForm
    template_name = 'add.html'
    success_url = reverse_lazy('request_list')

    def dispatch(self, request, *args, **kwargs):
        require_permission(request, 'can_add_maintenance', "عذراً، لا تمتلك صلاحية إضافة طلب صيانة.")
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        profile = get_profile(self.request.user)
        if profile and profile.managing_department:
            initial['department'] = profile.managing_department
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.requester = self.request.user
        profile = get_profile(self.request.user)
        if not get_permissions(self.request.user).can_view_all_departments and profile.managing_department:
            form.instance.department = profile.managing_department
        response = super().form_valid(form)
        create_audit_log(
            self.request.user,
            AuditLog.ACTION_CREATE,
            self.object,
            new_data=serialize_instance(self.object, fields=['description', 'requester', 'department', 'required_specialty', 'status', 'priority']),
            description=f"تم إنشاء طلب الصيانة رقم {self.object.pk}",
            request=self.request,
        )
        notify_maintenance_needs_acceptance(self.object)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'تقديم طلب صيانة جديد'
        context['username'] = self.request.user.get_full_name() or self.request.user.username
        return context


class maintenanceRequestEditView(OwnerOrPendingEditorRequiredMixin, UpdateView):
    model = MaintenanceRequest
    form_class = MaintenanceRequestForm
    template_name = 'edit.html'
    success_url = reverse_lazy('request_list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        old_data = serialize_instance(self.get_object(), fields=['description', 'department', 'required_specialty', 'status', 'priority'])
        response = super().form_valid(form)
        create_audit_log(
            self.request.user,
            AuditLog.ACTION_UPDATE,
            self.object,
            old_data=old_data,
            new_data=serialize_instance(self.object, fields=['description', 'department', 'required_specialty', 'status', 'priority']),
            description=f"تم تعديل طلب الصيانة رقم {self.object.pk}",
            request=self.request,
        )
        return response


class maintenanceRequestDeleteView(OwnerOrPendingEditorRequiredMixin, DeleteView):
    model = MaintenanceRequest
    success_url = reverse_lazy('request_list')

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        old_data = serialize_instance(self.object, fields=['description', 'department', 'required_specialty', 'status', 'priority'])
        create_audit_log(
            request.user,
            AuditLog.ACTION_DELETE,
            self.object,
            old_data=old_data,
            description=f"تم حذف طلب الصيانة رقم {self.object.pk}",
            request=request,
        )
        messages.success(request, "تم حذف الطلب بنجاح")
        return super().post(request, *args, **kwargs)


class MaintenanceRequestApproveView(LoginRequiredMixin, View):
    def post(self, request, pk):
        require_permission(
            request,
            'can_approve_maintenance',
            "عذراً، لا تمتلك صلاحية قبول طلبات الصيانة أو تكليف مهندس.",
        )
        maintenance_request = get_object_or_404(
            get_visible_maintenance_requests(request.user, maintenance_queryset()),
            pk=pk,
        )
        if maintenance_request.status not in ('pending', 'reject_assignment'):
            raise PermissionDenied("لا يمكن قبول أو تكليف طلب صيانة في حالته الحالية.")
        engineer_profile = get_object_or_404(Profile, id=request.POST.get('assigned_to'), specialty__isnull=False)
        old_data = {
            'status': maintenance_request.status,
            'assigned_technician': maintenance_request.assigned_technician_id,
        }
        maintenance_request.status = 'assigned'
        maintenance_request.assigned_technician = engineer_profile
        maintenance_request.save(update_fields=['status', 'assigned_technician', 'updated_at'])
        create_audit_log(
            request.user,
            AuditLog.ACTION_ASSIGN,
            maintenance_request,
            old_data=old_data,
            new_data={'status': maintenance_request.status, 'assigned_technician': engineer_profile.pk},
            description=f"تم تكليف المهندس {engineer_profile.user.get_full_name() or engineer_profile.user.username}",
            request=request,
        )
        notify_maintenance_assigned(maintenance_request)
        messages.success(request, f"تم تعيين المهندس {engineer_profile.user.first_name} بنجاح")
        return redirect_after_maintenance_action(request, maintenance_request)
    

class MaintenanceRequestExcuteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        require_permission(
            request,
            'is_engineer',
            "عذراً، لا تمتلك صلاحية بدء تنفيذ طلبات الصيانة.",
        )
        maintenance_request = get_object_or_404(
            get_visible_maintenance_requests(request.user, maintenance_queryset()),
            pk=pk,
        )
        check_assined_status_and_assined_engineer(maintenance_request, request.user)
        action = request.POST.get('action', 'accept_assignment')
        if action == 'reject_assignment':
            rejected_engineer_user = request.user
            old_status = maintenance_request.status
            old_technician = maintenance_request.assigned_technician_id
            maintenance_request.status = 'reject_assignment'
            maintenance_request.assigned_technician = None
            maintenance_request.save(update_fields=['status', 'assigned_technician', 'updated_at'])
            create_audit_log(
                request.user,
                AuditLog.ACTION_REJECT,
                maintenance_request,
                old_data={'status': old_status, 'assigned_technician': old_technician},
                new_data={'status': maintenance_request.status, 'assigned_technician': None},
                description=f"رفض المهندس {request.user.get_full_name() or request.user.username} التكليف",
                request=request,
            )
            notify_assignment_rejected(maintenance_request, rejected_engineer_user)
            messages.warning(request, f"تم رفض تكليف طلب الصيانة {maintenance_request.id}")
            return redirect_after_maintenance_action(request, maintenance_request)
        old_status = maintenance_request.status
        maintenance_request.status = 'accept_assignment_start_progress'
        maintenance_request.save(update_fields=['status', 'updated_at'])
        old_data, new_data = status_change_data(old_status, maintenance_request.status)
        create_audit_log(
            request.user,
            AuditLog.ACTION_STATUS_CHANGE,
            maintenance_request,
            old_data=old_data,
            new_data=new_data,
            description=f"قبل المهندس {request.user.get_full_name() or request.user.username} التكليف وبدأ التنفيذ",
            request=request,
        )
        messages.success(request, f"تم قبول التكليف وبدء تنفيذ طلب الصيانة {maintenance_request.id} بنجاح")
        return redirect_after_maintenance_action(request, maintenance_request)


class MaintenanceRequestRejectedView(LoginRequiredMixin, View):
    def post(self, request, pk):
        require_permission(
            request,
            'can_approve_maintenance',
            "عذراً، لا تمتلك صلاحية رفض طلبات الصيانة.",
        )
        cause = (request.POST.get('cause') or '').strip()
        if not cause:
            messages.error(request, "سبب الرفض مطلوب")
            return redirect('request_list')
        maintenance_request = get_object_or_404(
            get_visible_maintenance_requests(request.user, maintenance_queryset()),
            pk=pk,
        )
        require_pending_status(maintenance_request, "لا يمكن رفض طلب صيانة لم يعد في حالة الانتظار.")
        old_status = maintenance_request.status
        maintenance_request.status = 'rejected'
        maintenance_request.is_rejected = True
        maintenance_request.cause = cause
        maintenance_request.rejected_by = request.user
        maintenance_request.rejected_reason = cause
        maintenance_request.rejected_at = timezone.now()
        maintenance_request.save(update_fields=[
            'status', 'is_rejected', 'cause', 'rejected_by', 'rejected_reason', 'rejected_at', 'updated_at'
        ])
        create_audit_log(
            request.user,
            AuditLog.ACTION_REJECT,
            maintenance_request,
            old_data={'status': old_status},
            new_data={'status': maintenance_request.status, 'cause': cause, 'rejected_by': request.user.pk},
            description=f"تم رفض طلب الصيانة رقم {maintenance_request.pk}. السبب: {cause}",
            request=request,
        )
        notify_maintenance_rejected(maintenance_request)
        messages.success(request, "تم رفض الطلب بنجاح")
        return redirect_after_maintenance_action(request, maintenance_request)


class MaintenanceRequestCompleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        maintenance_request = get_object_or_404(
            get_visible_maintenance_requests(request.user, maintenance_queryset()),
            pk=pk,
        )
        permissions = get_user_permissions_or_403(request)
        profile = get_profile(request.user)
        complete_report = (request.POST.get('complete_report') or '').strip()
        action = request.POST.get('action', 'approve_report' if not complete_report else 'submit_report')

        if complete_report:
            if not permissions.can_add_achievement_report:
                raise PermissionDenied("عذراً، لا تمتلك صلاحية إضافة تقرير إنجاز.")
            if maintenance_request.assigned_technician != profile:
                raise PermissionDenied("لا يمكن إضافة تقرير إنجاز لطلب غير مسند إليك.")
            if maintenance_request.status not in (
                'accept_assignment_start_progress',
                'obtained_parts',
                'completed_report_rejected',
            ):
                raise PermissionDenied("لا يمكن إضافة تقرير إنجاز لهذا الطلب في حالته الحالية.")
            with transaction.atomic():
                report = maintenance_request.completed_report.order_by('-id').first()
                old_maintenance_status = maintenance_request.status
                if report is None:
                    report = CompleteReport.objects.create(
                        maintenance_request=maintenance_request,
                        complete_report=complete_report,
                        is_rejected=False,
                        cause='',
                        acceptance=0,
                        status=REPORT_WAITING_STATUS,
                    )
                    audit_action = AuditLog.ACTION_CREATE
                    old_report_data = None
                else:
                    audit_action = AuditLog.ACTION_UPDATE
                    old_report_data = serialize_instance(report, fields=['complete_report', 'status', 'acceptance', 'is_rejected', 'cause'])
                    report.complete_report = complete_report
                    reset_complete_report_approval(report)
                    report.save(update_fields=[
                        'complete_report', 'status', 'is_rejected', 'cause', 'rejected_by',
                        'rejected_reason', 'rejected_at', 'acceptance'
                    ])
                maintenance_request.status = MAINTENANCE_REPORT_WAITING_STATUS
                maintenance_request.save(update_fields=['status', 'updated_at'])
                create_audit_log(
                    request.user,
                    audit_action,
                    report,
                    old_data=old_report_data,
                    new_data=serialize_instance(report, fields=['complete_report', 'status', 'acceptance', 'is_rejected', 'cause']),
                    description=f"تم إرسال تقرير الإنجاز لطلب الصيانة رقم {maintenance_request.pk}",
                    request=request,
                )
                create_audit_log(
                    request.user,
                    AuditLog.ACTION_STATUS_CHANGE,
                    maintenance_request,
                    old_data={'status': old_maintenance_status},
                    new_data={'status': maintenance_request.status},
                    description=f"تم تحويل طلب الصيانة رقم {maintenance_request.pk} إلى انتظار تعميد تقرير الإنجاز",
                    request=request,
                )
            notify_complete_report_needs_approval(report)
            messages.success(request, "تم إرسال تقرير الإنجاز بنجاح")
            return redirect_after_maintenance_action(request, maintenance_request)

        if maintenance_request.status == MAINTENANCE_REPORT_WAITING_STATUS:
            report = maintenance_request.completed_report.order_by('-id').first()
            if report is None:
                raise PermissionDenied("لا يوجد تقرير إنجاز مرتبط بهذا الطلب.")

            if action == 'reject_report':
                if not can_user_approve_complete_report(report, permissions):
                    raise PermissionDenied("لا يمكنك رفض التقرير في مرحلة التعميد الحالية.")
                if report.approvals.filter(approver=request.user).exists():
                    raise PermissionDenied("لا يمكنك اتخاذ قرار على تقرير الإنجاز أكثر من مرة.")
                cause = (request.POST.get('cause') or '').strip()
                if not cause:
                    messages.error(request, "سبب رفض التقرير مطلوب")
                    return redirect('request_list')
                with transaction.atomic():
                    old_report_status = report.status
                    old_maintenance_status = maintenance_request.status
                    CompleteReportApproval.objects.create(
                        report=report,
                        approver=request.user,
                        decision='rejected',
                        reason=cause,
                    )
                    report.status = 'rejected'
                    report.is_rejected = True
                    report.cause = cause
                    report.rejected_by = request.user
                    report.rejected_reason = cause
                    report.rejected_at = timezone.now()
                    report.save(update_fields=[
                        'status', 'is_rejected', 'cause', 'rejected_by',
                        'rejected_reason', 'rejected_at'
                    ])
                    update_maintenance_status_from_complete_report(report)
                    create_audit_log(
                        request.user,
                        AuditLog.ACTION_REJECT,
                        report,
                        old_data={'status': old_report_status, 'maintenance_status': old_maintenance_status},
                        new_data={'status': report.status, 'maintenance_status': report.maintenance_request.status, 'cause': cause, 'rejected_by': request.user.pk},
                        description=f"تم رفض تقرير الإنجاز رقم {report.pk}. السبب: {cause}",
                        request=request,
                    )
                notify_complete_report_rejected(report)
                messages.warning(request, "تم رفض تقرير الإنجاز وإعادته للمهندس للتعديل")
                return redirect_after_maintenance_action(request, maintenance_request)

            if not can_user_approve_complete_report(report, permissions):
                raise PermissionDenied("لا يمكن تعميد التقرير قبل اكتمال التعميدات السابقة أو بعد تعميدك له.")
            if report.approvals.filter(approver=request.user).exists():
                raise PermissionDenied("لا يمكنك تعميد التقرير أكثر من مرة.")
            with transaction.atomic():
                old_report_data = {'status': report.status, 'acceptance': report.acceptance}
                old_maintenance_status = maintenance_request.status
                CompleteReportApproval.objects.create(
                    report=report,
                    approver=request.user,
                    decision='accepted',
                )
                report.acceptance = max(report.acceptance, permissions.achievement_approval_order)
                if report.acceptance >= 5:
                    report.status = 'accepted'
                report.save(update_fields=['acceptance', 'status'])
                update_maintenance_status_from_complete_report(report)
                create_audit_log(
                    request.user,
                    AuditLog.ACTION_APPROVE,
                    report,
                    old_data={**old_report_data, 'maintenance_status': old_maintenance_status},
                    new_data={'status': report.status, 'acceptance': report.acceptance, 'maintenance_status': report.maintenance_request.status, 'approver': request.user.pk},
                    description=f"تم اعتماد تقرير الإنجاز رقم {report.pk}",
                    request=request,
                )
            if report.status == 'accepted':
                report.refresh_from_db()
                notify_complete_report_accepted(report)
            messages.success(request, "تم تعميد تقرير الإنجاز بنجاح")
            return redirect_after_maintenance_action(request, maintenance_request)

        raise PermissionDenied("عذراً، لا تمتلك الصلاحية للقيام بهذا الإجراء.")


class SparePartRequestListView(LoginRequiredMixin, ListView):
    model = SparePartRequest
    template_name = 'requests_spare_part_list.html'
    context_object_name = 'objects'
    paginate_by = 10

    def get_queryset(self):
        self.base_queryset = get_active_spare_part_requests(self.request.user, spare_part_request_queryset())
        queryset = apply_spare_part_filters(self.base_queryset, self.request.GET, self.request.user)
        return apply_spare_part_sorting(queryset, self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        permissions = get_permissions(self.request.user)
        profile = get_profile(self.request.user)
        base_queryset = getattr(
            self,
            'base_queryset',
            get_active_spare_part_requests(self.request.user, spare_part_request_queryset()),
        )
        filter_options = get_spare_part_filter_options(base_queryset, self.request.user)
        active_filters = get_spare_part_active_filters(self.request.GET)
        department_labels = {str(option['id']): option['name'] for option in filter_options['departments']}
        engineer_labels = {
            str(engineer.pk): engineer.user.get_full_name() or engineer.user.username
            for engineer in filter_options['engineers']
        }
        for active_filter in active_filters:
            if active_filter['label'] == 'القسم':
                active_filter['value'] = department_labels.get(active_filter['value'], active_filter['value'])
            elif active_filter['label'] == 'المهندس':
                active_filter['value'] = engineer_labels.get(active_filter['value'], active_filter['value'])

        store_titles = spare_part_approval_titles('store_requisition')
        purchase_titles = spare_part_approval_titles('purchase_requisition')
        objects = list(context['objects'])

        for part_request in objects:
            titles = store_titles if part_request.order_kind == 'store_requisition' else purchase_titles
            next_order = min(part_request.acceptance + 1, 8)
            part_request.approval_percent = min(100, int((part_request.acceptance / 8) * 100))
            part_request.next_approval_title = (
                "مكتمل التعميد"
                if part_request.acceptance >= 8
                else titles.get(next_order, f"مرحلة التعميد رقم {next_order}")
            )
            already_approved = has_user_approval(part_request, self.request.user)
            part_request.can_take_approval_action = (
                can_user_approve_spare_part_request(part_request, permissions)
                and not already_approved
            )
            part_request.can_mark_parts_available = (
                permissions.can_issue_from_store
                and part_request.status == 'waiting_purchase_accepted_parts'
            )
            part_request.can_issue_from_store_action = (
                permissions.can_issue_from_store
                and part_request.status in ('bought_available_parts', 'issue_available_parts_issued_waiting_confirmation')
            )
            part_request.can_confirm_issuance_action = (
                permissions.can_confirm_issuance
                and profile
                and part_request.engineer == profile
                and part_request.status == 'avaliable_parts_issued_waiting_confirmation'
            )

        context['objects'] = objects
        context['object_list'] = objects
        context['permissions'] = permissions
        context['filter_options'] = filter_options
        context['active_filters'] = active_filters
        context['filter_query_string'] = _query_string_without(self.request.GET)
        context['filters'] = {
            'q': self.request.GET.get('q', ''),
            'status': self.request.GET.get('status', ''),
            'order_kind': self.request.GET.get('order_kind', ''),
            'department': self.request.GET.get('department', ''),
            'engineer': self.request.GET.get('engineer', ''),
            'created_from': self.request.GET.get('created_from', ''),
            'created_to': self.request.GET.get('created_to', ''),
            'sort': self.request.GET.get('sort', 'newest'),
            'only_my_actions': self.request.GET.get('only_my_actions') == '1',
        }
        context['result_count'] = context['paginator'].count if context.get('is_paginated') else len(objects)
        context['list_title'] = 'طلبات قطع الغيار النشطة'
        context['list_description'] = 'طلبات قطع الغيار النشطة التي لم تكتمل بعد'
        context['show_spare_part_actions'] = True
        return context


class SparePartRequestHistoryView(SparePartRequestListView):
    template_name = 'requests_spare_part_history.html'

    def get_queryset(self):
        return get_final_spare_part_requests(self.request.user, spare_part_request_queryset())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['list_title'] = 'سجل طلبات قطع الغيار المكتملة والمرفوضة'
        context['list_description'] = 'يعرض هذا السجل طلبات قطع الغيار التي انتهت بالصرف المؤكد أو الرفض فقط'
        context['show_spare_part_actions'] = False
        return context


class SparePartRequestDetails(LoginRequiredMixin, DetailView):
    model = SparePartRequest
    template_name = 'request_spare_part_details.html'
    context_object_name = 'part_request'

    def get_queryset(self):
        return get_visible_spare_part_requests(self.request.user, spare_part_request_queryset())


class SparePartRequestFormsetMixin(LoginRequiredMixin):
    model = SparePartRequest
    form_class = SparePartRequestForm
    formset_class = SparePartsFormSet
    success_url = reverse_lazy('request_list')

    def get_queryset(self):
        return get_visible_spare_part_requests(self.request.user, spare_part_request_queryset())

    def get_success_url(self):
        return self.success_url

    def get_formset(self):
        return self.formset_class(
            self.request.POST or None,
            instance=getattr(self, 'object', None),
            prefix='spare_parts',
        )

    def forms_valid(self, form, formset):
        with transaction.atomic():
            existing_object = getattr(self, 'object', None)
            old_data = None
            if existing_object and existing_object.pk:
                old_data = serialize_instance(
                    existing_object,
                    fields=['maintenance_request', 'engineer', 'order_kind', 'description', 'status', 'cause', 'acceptance'],
                )
            self.object = form.save(commit=False)
            self.prepare_object(self.object)
            old_status = old_data.get('status') if old_data else None
            if self.object.pk and self.object.status == 'rejected':
                reset_spare_part_approval(self.object)
            self.object.save()
            formset.instance = self.object
            formset.save()
            if self.object.status == 'rejected':
                reset_spare_part_approval(self.object)
                self.object.save(update_fields=[
                    'status', 'acceptance', 'cause', 'rejected_by',
                    'rejected_reason', 'rejected_at', 'updated_at'
                ])
            sync_spare_part_request_status(self.object)
            if self.object.status == SPARE_PART_WAITING_STATUS:
                notify_spare_part_needs_approval(self.object)
            parts_data = [
                {'id': part.pk, 'part_name': part.part_name, 'quantity': part.quantity, 'acceptance': part.acceptance, 'is_rejected': part.is_rejected}
                for part in self.object.spare_parts.all()
            ]
            action_type = AuditLog.ACTION_UPDATE if old_data else AuditLog.ACTION_CREATE
            description = (
                f"تم إعادة إرسال طلب قطع الغيار رقم {self.object.pk}"
                if old_status == 'rejected'
                else f"تم {'تعديل' if old_data else 'إنشاء'} طلب قطع الغيار رقم {self.object.pk}"
            )
            create_audit_log(
                self.request.user,
                action_type,
                self.object,
                old_data=old_data,
                new_data={
                    **serialize_instance(self.object, fields=['maintenance_request', 'engineer', 'order_kind', 'description', 'status', 'cause', 'acceptance']),
                    'parts': parts_data,
                },
                description=description,
                request=self.request,
            )
        return redirect(self.get_success_url())

    def form_invalid_response(self, form, formset, maintenance_request=None):
        list_view = maintenanceRequestListView()
        list_view.setup(self.request)
        list_view.object_list = list_view.get_queryset()
        context = list_view.get_context_data()
        context.update({
            'formRSPart': form,
            'formRSParts': formset,
            'spare_part_modal_open': True,
            'spare_part_request_id': maintenance_request.pk if maintenance_request else '',
            'modal_form_action': self.request.path,
        })
        return render(self.request, 'requests_maintenance_list.html', context)


class SparePartRequestCreateView(SparePartRequestFormsetMixin, View):
    def dispatch(self, request, *args, **kwargs):
        self.maintenance_request = get_object_or_404(
            get_visible_maintenance_requests(request.user, maintenance_queryset()),
            pk=kwargs['pk'],
        )
        profile = get_profile(request.user)
        require_permission(request, 'can_add_spare_parts', "عذراً، لا تمتلك صلاحية إضافة طلب قطع غيار.")
        if not profile or self.maintenance_request.assigned_technician != profile:
            raise PermissionDenied("لا يمكن إضافة طلب قطع غيار لطلب صيانة غير مسند إليك.")
        if self.maintenance_request.status != 'accept_assignment_start_progress':
            raise PermissionDenied("لا يمكن إضافة طلب قطع غيار لهذا الطلب في حالته الحالية.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = None
        form = self.form_class(request.POST)
        formset = self.get_formset()
        if form.is_valid() and formset.is_valid():
            response = self.forms_valid(form, formset)
            messages.success(request, "تم إرسال طلب قطع الغيار بنجاح.")
            return response
        return self.form_invalid_response(form, formset, self.maintenance_request)

    def prepare_object(self, spare_part_request):
        spare_part_request.engineer = get_profile(self.request.user)
        spare_part_request.maintenance_request = self.maintenance_request
        spare_part_request.status = SPARE_PART_WAITING_STATUS


class SparePartRequestUpdateView(SparePartRequestFormsetMixin, UpdateView):
    template_name = 'requests_maintenance_list.html'

    def dispatch(self, request, *args, **kwargs):
        require_permission(
            request,
            'can_edit_delete_pending_parts',
            "عذراً، لا تمتلك صلاحية تعديل طلبات قطع الغيار المعلقة.",
        )
        self.object = self.get_object()
        require_spare_part_pending_status(self.object)
        if self.object.engineer != get_profile(request.user):
            raise PermissionDenied("لا يمكن تعديل طلب قطع غيار لم تقم بإنشائه.")
        if self.object.status != 'rejected' and self.object.maintenance_request.status != 'awaiting_parts':
            raise PermissionDenied("لا يمكن تعديل طلب قطع الغيار في حالة طلب الصيانة الحالية.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.POST, instance=self.object)
        formset = self.get_formset()
        if form.is_valid() and formset.is_valid():
            response = self.forms_valid(form, formset)
            messages.success(request, "تم تحديث طلب قطع الغيار بنجاح.")
            return response
        return self.form_invalid_response(form, formset, self.object.maintenance_request)

    def prepare_object(self, spare_part_request):
        return spare_part_request


SparePartRequestCreatView = SparePartRequestCreateView.as_view()


def approve_spare_part_request(request, obj, redirect_to='spare_part_list'):
    permissions = get_user_permissions_or_403(request)
    if obj.status in ('avaliable_parts_issued', 'rejected'):
        messages.error(request, "لا يمكن تنفيذ هذا الإجراء على طلب قطع غيار مكتمل أو مرفوض.")
        raise PermissionDenied("لا يمكن تنفيذ هذا الإجراء على طلب قطع غيار مكتمل أو مرفوض.")

    action = request.POST.get('action', 'approve')

    if action == 'mark_available':
        if obj.order_kind != 'purchase_requisition' or obj.status != 'waiting_purchase_accepted_parts' or not permissions.can_issue_from_store:
            messages.error(request, "عذراً، لا تمتلك صلاحية توفير القطع أو الطلب ليس في الحالة المناسبة.")
            raise PermissionDenied("عذراً، لا تمتلك صلاحية توفير القطع أو الطلب ليس في الحالة المناسبة.")
        old_status = obj.status
        with transaction.atomic():
            obj.status = 'bought_available_parts'
            obj.save(update_fields=['status', 'updated_at'])
            MaintenanceRequest.objects.filter(pk=obj.maintenance_request_id).update(status='awaiting_parts')
            create_audit_log(
                request.user,
                AuditLog.ACTION_STATUS_CHANGE,
                obj,
                old_data={'status': old_status},
                new_data={'status': obj.status},
                description=f"تم تسجيل توفر قطع الغيار لطلب رقم {obj.pk}",
                request=request,
            )
        notify_spare_part_ready_to_issue(obj)
        messages.success(request, "تم تسجيل توفير قطع الغيار بنجاح.")
        return redirect_after_spare_part_action(redirect_to, obj)

    if action == 'issue':
        if obj.status not in ('bought_available_parts', 'issue_available_parts_issued_waiting_confirmation') or obj.acceptance < 8 or not permissions.can_issue_from_store:
            messages.error(request, "لا يمكن صرف القطع قبل اكتمال التعميد أو بدون صلاحية الصرف.")
            raise PermissionDenied("لا يمكن صرف القطع قبل اكتمال التعميد أو بدون صلاحية الصرف.")
        old_status = obj.status
        with transaction.atomic():
            obj.spare_parts.exclude(is_rejected=True).update(acceptance=8, is_rejected=False)
            obj.status = 'avaliable_parts_issued_waiting_confirmation'
            obj.save(update_fields=['status', 'updated_at'])
            MaintenanceRequest.objects.filter(pk=obj.maintenance_request_id).update(status='awaiting_parts')
            create_audit_log(
                request.user,
                AuditLog.ACTION_STATUS_CHANGE,
                obj,
                old_data={'status': old_status},
                new_data={'status': obj.status},
                description=f"تم صرف قطع الغيار لطلب رقم {obj.pk} بانتظار تأكيد المهندس",
                request=request,
            )
        notify_spare_part_waiting_confirmation(obj)
        messages.success(request, "تم إصدار أمر الصرف من المخازن.")
        return redirect_after_spare_part_action(redirect_to, obj)

    if action == 'confirm_issuance':
        profile = get_profile(request.user)
        if (
            obj.status != 'avaliable_parts_issued_waiting_confirmation'
            or obj.engineer != profile
            or not permissions.can_confirm_issuance
        ):
            messages.error(request, "تأكيد الصرف متاح فقط للمهندس طالب القطع وفي الحالة المناسبة.")
            raise PermissionDenied("تأكيد الصرف متاح فقط للمهندس طالب القطع وفي الحالة المناسبة.")
        old_status = obj.status
        with transaction.atomic():
            obj.status = 'avaliable_parts_issued'
            obj.completed_at = timezone.now()
            obj.save(update_fields=['status', 'completed_at', 'updated_at'])
            update_maintenance_status_from_spare_part_request(obj)
            create_audit_log(
                request.user,
                AuditLog.ACTION_STATUS_CHANGE,
                obj,
                old_data={'status': old_status},
                new_data={'status': obj.status, 'completed_at': obj.completed_at.isoformat()},
                description=f"تم تأكيد استلام قطع الغيار لطلب رقم {obj.pk}",
                request=request,
            )
        notify_spare_part_issued(obj)
        messages.success(request, "تم تأكيد استلام قطع الغيار وتحديث طلب الصيانة.")
        return redirect_after_spare_part_action(redirect_to, obj)

    if obj.status != SPARE_PART_WAITING_STATUS:
        messages.error(request, "حالة طلب قطع الغيار لا تسمح بالتعميد حالياً.")
        raise PermissionDenied("تعميد طلب قطع الغيار متاح فقط أثناء انتظار التعميد.")
    if obj.approvals.filter(approver=request.user).exists():
        messages.error(request, "لا يمكنك تعميد طلب قطع الغيار أكثر من مرة.")
        raise PermissionDenied("لا يمكنك تعميد طلب قطع الغيار أكثر من مرة.")

    settings = get_spare_part_approval_settings(obj, permissions)
    permission_name = settings['permission_name']
    order = settings['approval_order']
    can_bypass = settings['can_bypass']
    max_bypass = settings['max_bypass_order']

    if getattr(permissions, permission_name, False):
        if not can_approve_order(obj.acceptance, order, can_bypass, max_bypass):
            messages.error(request, "لا يمكن تعميد الطلب قبل اكتمال التعميدات السابقة.")
            raise PermissionDenied("لا يمكن تعميد الطلب قبل اكتمال التعميدات السابقة.")
        old_status = obj.status
        old_acceptance = obj.acceptance
        with transaction.atomic():
            SparePartApproval.objects.create(
                request=obj,
                approver=request.user,
                decision='accepted',
            )
            obj.acceptance = max(obj.acceptance, order)
            obj.spare_parts.exclude(is_rejected=True).update(acceptance=obj.acceptance, is_rejected=False)
            sync_spare_part_request_status(obj)
            create_audit_log(
                request.user,
                AuditLog.ACTION_APPROVE,
                obj,
                old_data={'status': old_status, 'acceptance': old_acceptance},
                new_data={'status': obj.status, 'acceptance': obj.acceptance, 'approver': request.user.pk},
                description=f"تم تعميد طلب قطع الغيار رقم {obj.pk}",
                request=request,
            )
        if old_status != obj.status and obj.status == 'waiting_purchase_accepted_parts':
            notify_spare_part_purchase_ready(obj)
        elif old_status != obj.status and obj.status == 'issue_available_parts_issued_waiting_confirmation':
            notify_spare_part_ready_to_issue(obj)
        messages.success(request, "تم تعميد طلب قطع الغيار بنجاح")
        return redirect_after_spare_part_action(redirect_to, obj)

    messages.error(request, "عذراً، لا تمتلك الصلاحية للقيام بهذا الإجراء.")
    raise PermissionDenied("عذراً، لا تمتلك الصلاحية للقيام بهذا الإجراء.")


def reject_spare_part_request(request, spare_part_request, redirect_to='spare_part_list'):
    permissions = get_user_permissions_or_403(request)
    if spare_part_request.status in ('avaliable_parts_issued', 'rejected'):
        messages.error(request, "لا يمكن رفض طلب قطع غيار مكتمل أو مرفوض.")
        raise PermissionDenied("لا يمكن رفض طلب قطع غيار مكتمل أو مرفوض.")
    if spare_part_request.status != SPARE_PART_WAITING_STATUS:
        messages.error(request, "حالة طلب قطع الغيار لا تسمح بالرفض حالياً.")
        raise PermissionDenied("رفض طلب قطع الغيار متاح فقط أثناء انتظار التعميد.")
    can_reject = can_user_approve_spare_part_request(spare_part_request, permissions)
    if not can_reject:
        messages.error(request, "عذراً، لا تمتلك صلاحية رفض طلب قطع الغيار في هذه المرحلة.")
        raise PermissionDenied("عذراً، لا تمتلك الصلاحية للقيام بهذا الإجراء.")
    if spare_part_request.approvals.filter(approver=request.user).exists():
        messages.error(request, "لا يمكنك اتخاذ قرار على طلب قطع الغيار أكثر من مرة.")
        raise PermissionDenied("لا يمكنك اتخاذ قرار على طلب قطع الغيار أكثر من مرة.")
    cause = (request.POST.get('cause') or '').strip()
    if not cause:
        messages.error(request, "سبب الرفض مطلوب")
        return redirect_after_spare_part_action(redirect_to, spare_part_request)
    rejected_part_ids = request.POST.getlist('rejected_part_ids')
    old_status = spare_part_request.status
    old_acceptance = spare_part_request.acceptance
    with transaction.atomic():
        spare_part_request.cause = cause
        SparePartApproval.objects.create(
            request=spare_part_request,
            approver=request.user,
            decision='rejected',
            reason=cause,
        )
        if rejected_part_ids:
            settings = get_spare_part_approval_settings(spare_part_request, permissions)
            order = settings['approval_order'] or spare_part_request.acceptance
            spare_part_request.spare_parts.filter(pk__in=rejected_part_ids).update(is_rejected=True)
            spare_part_request.spare_parts.exclude(pk__in=rejected_part_ids).update(
                acceptance=max(spare_part_request.acceptance, order),
                is_rejected=False,
            )
            spare_part_request.acceptance = max(spare_part_request.acceptance, order)
            spare_part_request.save(update_fields=['acceptance', 'cause', 'updated_at'])
        else:
            spare_part_request.status = 'rejected'
            spare_part_request.spare_parts.update(is_rejected=True)
        if spare_part_request.spare_parts.exists() and not spare_part_request.spare_parts.exclude(is_rejected=True).exists():
            spare_part_request.status = 'rejected'
            spare_part_request.rejected_by = request.user
            spare_part_request.rejected_reason = cause
            spare_part_request.rejected_at = timezone.now()
            spare_part_request.save(update_fields=[
                'status', 'cause', 'rejected_by', 'rejected_reason', 'rejected_at', 'updated_at'
            ])
        sync_spare_part_request_status(spare_part_request)
        create_audit_log(
            request.user,
            AuditLog.ACTION_REJECT,
            spare_part_request,
            old_data={'status': old_status, 'acceptance': old_acceptance},
            new_data={
                'status': spare_part_request.status,
                'acceptance': spare_part_request.acceptance,
                'cause': cause,
                'rejected_part_ids': rejected_part_ids,
                'rejected_by': request.user.pk,
            },
            description=f"تم رفض {'بعض قطع' if rejected_part_ids else 'كل قطع'} طلب قطع الغيار رقم {spare_part_request.pk}. السبب: {cause}",
            request=request,
        )
    if old_status != spare_part_request.status and spare_part_request.status == 'rejected':
        notify_spare_part_rejected(spare_part_request)
    messages.success(request, "تم تحديث حالة قطع الغيار بنجاح")
    return redirect_after_spare_part_action(redirect_to, spare_part_request)


@login_required
@require_POST
def SparePartRequestAcceptance(request, pk):
    obj = get_object_or_404(get_visible_spare_part_requests(request.user, spare_part_request_queryset()), id=pk)
    return approve_spare_part_request(request, obj)


@login_required
@require_POST
def SparePartRequestRejected(request, pk):
    spare_part_request = get_object_or_404(get_visible_spare_part_requests(request.user, spare_part_request_queryset()), pk=pk)
    return reject_spare_part_request(request, spare_part_request)


@login_required
@require_POST
def SparePartRequestApproveFromCard(request, pk):
    obj = get_object_or_404(get_visible_spare_part_requests(request.user, spare_part_request_queryset()), id=pk)
    return approve_spare_part_request(request, obj, redirect_to='request_list')


@login_required
@require_POST
def SparePartRequestRejectFromCard(request, pk):
    spare_part_request = get_object_or_404(get_visible_spare_part_requests(request.user, spare_part_request_queryset()), pk=pk)
    return reject_spare_part_request(request, spare_part_request, redirect_to='request_list')


@login_required
@require_POST
def SparePartRequestApproveFromDetail(request, pk):
    obj = get_object_or_404(get_visible_spare_part_requests(request.user, spare_part_request_queryset()), id=pk)
    return approve_spare_part_request(request, obj, redirect_to='maintenance_detail')


@login_required
@require_POST
def SparePartRequestRejectFromDetail(request, pk):
    spare_part_request = get_object_or_404(get_visible_spare_part_requests(request.user, spare_part_request_queryset()), pk=pk)
    return reject_spare_part_request(request, spare_part_request, redirect_to='maintenance_detail')


@login_required
@require_POST
def SparePartRequestMarkPurchaseAvailableFromDetail(request, pk):
    obj = get_object_or_404(get_visible_spare_part_requests(request.user, spare_part_request_queryset()), id=pk)
    request.POST = request.POST.copy()
    request.POST['action'] = 'mark_available'
    return approve_spare_part_request(request, obj, redirect_to='maintenance_detail')


@login_required
@require_POST
def SparePartRequestIssueFromStoreFromDetail(request, pk):
    obj = get_object_or_404(get_visible_spare_part_requests(request.user, spare_part_request_queryset()), id=pk)
    request.POST = request.POST.copy()
    request.POST['action'] = 'issue'
    return approve_spare_part_request(request, obj, redirect_to='maintenance_detail')


@login_required
@require_POST
def SparePartRequestConfirmIssuanceFromDetail(request, pk):
    obj = get_object_or_404(get_visible_spare_part_requests(request.user, spare_part_request_queryset()), id=pk)
    request.POST = request.POST.copy()
    request.POST['action'] = 'confirm_issuance'
    return approve_spare_part_request(request, obj, redirect_to='maintenance_detail')


@login_required
@require_POST
def CompleteReportApproveFromDetail(request, pk):
    report = get_object_or_404(
        get_visible_complete_reports(
            request.user,
            CompleteReport.objects.select_related('maintenance_request', 'maintenance_request__department'),
        ),
        pk=pk,
    )
    request.POST = request.POST.copy()
    request.POST['next'] = 'detail'
    request.POST['action'] = 'approve_report'
    return MaintenanceRequestCompleteView.as_view()(request, pk=report.maintenance_request_id)


@login_required
@require_POST
def CompleteReportRejectFromDetail(request, pk):
    report = get_object_or_404(
        get_visible_complete_reports(
            request.user,
            CompleteReport.objects.select_related('maintenance_request', 'maintenance_request__department'),
        ),
        pk=pk,
    )
    request.POST = request.POST.copy()
    request.POST['next'] = 'detail'
    request.POST['action'] = 'reject_report'
    return MaintenanceRequestCompleteView.as_view()(request, pk=report.maintenance_request_id)
