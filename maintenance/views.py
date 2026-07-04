from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.core.paginator import Paginator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from accounts.audit_log_service import create_audit_log, serialize_instance, status_change_data
from accounts.models import ApprovalDelegation, ApprovalLevel, ApprovalWorkflow, JobTitlePermission, Profile, UserPermissionOverride
from accounts.permissions import get_effective_permissions, user_has_role_permission, user_permission_override_matches
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
    notify_delegated_complete_report_approval,
    notify_delegated_spare_part_approval,
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


def maintenance_navigation_context(
    current_label,
    parent_label=None,
    parent_url_name=None,
    show_back_button=False,
    back_url_name=None,
    back_url_kwargs=None,
):
    if current_label == 'لوحة التحكم' and not parent_label:
        breadcrumbs = [{'label': 'لوحة التحكم'}]
    else:
        breadcrumbs = [{'label': 'لوحة التحكم', 'url': reverse('home_redirect')}]
    if parent_label:
        breadcrumbs.append({
            'label': parent_label,
            'url': reverse(parent_url_name) if parent_url_name else None,
        })
    breadcrumbs.append({'label': current_label})
    context = {'breadcrumbs': breadcrumbs}
    if show_back_button:
        context['show_back_button'] = True
        if back_url_name:
            context['back_url'] = reverse(back_url_name, kwargs=back_url_kwargs or {})
    return context
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
MAINTENANCE_HISTORY_SORT_OPTIONS = {
    'newest': '-created_at',
    'oldest': 'created_at',
    'id_asc': 'pk',
    'id_desc': '-pk',
    'status': 'status',
    'department': 'department__name',
    'engineer': 'assigned_technician__user__first_name',
}
MAINTENANCE_HISTORY_SORT_LABELS = {
    'newest': 'الأحدث أولاً',
    'oldest': 'الأقدم أولاً',
    'id_asc': 'رقم الطلب تصاعدياً',
    'id_desc': 'رقم الطلب تنازلياً',
    'priority': 'حسب الأولوية',
    'status': 'حسب الحالة',
    'department': 'حسب القسم',
    'engineer': 'حسب المهندس',
}
SPARE_PART_HISTORY_SORT_OPTIONS = {
    'newest': '-created_at',
    'oldest': 'created_at',
    'id_asc': 'pk',
    'id_desc': '-pk',
    'status': 'status',
    'order_kind': 'order_kind',
    'department': 'maintenance_request__department__name',
    'engineer': 'engineer__user__first_name',
}
SPARE_PART_HISTORY_SORT_LABELS = {
    'newest': 'الأحدث أولاً',
    'oldest': 'الأقدم أولاً',
    'id_asc': 'رقم الطلب تصاعدياً',
    'id_desc': 'رقم الطلب تنازلياً',
    'status': 'حسب الحالة',
    'order_kind': 'حسب نوع الطلب',
    'department': 'حسب القسم',
    'engineer': 'حسب المهندس',
}


SPARE_PART_APPROVAL_WORKFLOWS = {
    'store_requisition': ApprovalWorkflow.STORE_ISSUE,
    'purchase_requisition': ApprovalWorkflow.PURCHASE,
}
APPROVAL_TRACKING_LABELS = {
    ApprovalWorkflow.PURCHASE: 'تعميدات طلب الشراء',
    ApprovalWorkflow.STORE_ISSUE: 'تعميدات طلب الصرف من المخازن',
    ApprovalWorkflow.ACHIEVEMENT_REPORT: 'تعميدات تقرير الإنجاز',
}
APPROVAL_PROGRESS_LABELS = {
    ApprovalWorkflow.PURCHASE: 'تقدم تعميد الشراء',
    ApprovalWorkflow.STORE_ISSUE: 'تقدم تعميد الصرف',
    ApprovalWorkflow.ACHIEVEMENT_REPORT: 'تقدم تعميد تقرير الإنجاز',
}
APPROVAL_CONFIRMATION_WORKFLOW_LABELS = {
    ApprovalWorkflow.PURCHASE: 'طلب شراء قطع غيار',
    ApprovalWorkflow.STORE_ISSUE: 'طلب صرف من المخازن',
    ApprovalWorkflow.ACHIEVEMENT_REPORT: 'تقرير إنجاز',
}
APPROVAL_MODE_NORMAL = 'normal'
APPROVAL_MODE_DELEGATED = 'delegated'
VALID_APPROVAL_MODES = (APPROVAL_MODE_NORMAL, APPROVAL_MODE_DELEGATED)


def check_assined_status_and_assined_engineer(maintenance_request, user):
    if maintenance_request.status != 'assigned':
        raise PermissionDenied("لا يمكن بدء تنفيذ طلب صيانة لم يعد في حالة التعيين.")
    if maintenance_request.assigned_technician != get_profile(user):
        raise PermissionDenied("لا يمكنك بدء تنفيذ طلب صيانة لم تكن مخصصًا لك.")

def get_profile(user):
    return getattr(user, 'profile', None) if user.is_authenticated else None


def get_permissions(user):
    if not user or not user.is_authenticated:
        return JobTitlePermission()
    return get_effective_permissions(user)


def get_user_permissions_or_403(request):
    permissions = get_permissions(request.user)
    if permissions is None:
        raise PermissionDenied("لا توجد صلاحيات مرتبطة بحسابك. الرجاء التواصل مع مدير النظام.")
    return permissions


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
        'spare_parts__approvals__approval_level',
        'completed_report__approvals__approver',
        'completed_report__approvals__approval_level',
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
    ).prefetch_related('spare_parts', 'approvals__approver', 'approvals__approval_level')


def get_spare_part_workflow_type(spare_part_request):
    return SPARE_PART_APPROVAL_WORKFLOWS.get(
        getattr(spare_part_request, 'order_kind', None),
        ApprovalWorkflow.PURCHASE,
    )


def get_workflow_max_order(workflow_type):
    level = ApprovalLevel.objects.filter(workflow_type=workflow_type).order_by('-order_number').first()
    return level.order_number if level else 0


def get_next_approval_level(workflow_type, current_order):
    return (
        ApprovalLevel.objects.filter(workflow_type=workflow_type, order_number__gt=current_order)
        .select_related('job_title')
        .order_by('order_number')
        .first()
    )


def is_workflow_complete(current_order, workflow_type):
    max_order = get_workflow_max_order(workflow_type)
    return bool(max_order and current_order >= max_order)


def approval_progress_percent(current_order, workflow_type):
    max_order = get_workflow_max_order(workflow_type)
    if not max_order:
        return 0
    return min(100, int((current_order / max_order) * 100))


def compact_employee_names(names, limit=3):
    if len(names) <= limit:
        return names
    return [*names[:limit], f"و {len(names) - limit} آخرون"]


def get_job_title_employee_names(job_title, cache=None):
    if not job_title:
        return []
    cache = cache if cache is not None else {}
    if job_title.pk not in cache:
        profiles = (
            Profile.objects.filter(job_title=job_title, user__is_active=True)
            .select_related('user')
            .order_by('user__first_name', 'user__last_name', 'user__username')
        )
        cache[job_title.pk] = [
            profile.user.get_full_name() or profile.user.username
            for profile in profiles
        ]
    return cache[job_title.pk]


def build_card_approval_summary(workflow_type, current_order, is_waiting_for_approval, employee_cache=None):
    total = get_workflow_max_order(workflow_type)
    display_current = min(current_order, total) if total else current_order
    is_complete = bool(total and current_order >= total)
    current_level = None
    if total and is_waiting_for_approval and not is_complete:
        current_level = get_next_approval_level(workflow_type, current_order)

    required_job_title = current_level.job_title if current_level else None
    employee_names = compact_employee_names(get_job_title_employee_names(required_job_title, employee_cache))
    if not total:
        responsible_state = 'not_configured'
        responsible_message = 'لا توجد مستويات تعميد مضافة لهذا النوع من الإعدادات.'
    elif is_complete:
        responsible_state = 'complete'
        responsible_message = 'اكتمل التعميد'
    elif current_level:
        responsible_state = 'pending'
        responsible_message = 'المسؤول عن التعميد الحالي'
    else:
        responsible_state = 'inactive'
        responsible_message = 'لا يوجد تعميد نشط حالياً'

    return {
        'workflow_type': workflow_type,
        'progress_label': APPROVAL_PROGRESS_LABELS.get(workflow_type, 'تقدم التعميد'),
        'progress_current': display_current,
        'progress_total': total,
        'progress_percent': approval_progress_percent(current_order, workflow_type),
        'current_level': current_level,
        'current_level_order': current_level.order_number if current_level else None,
        'current_required_job_title': required_job_title.title_name if required_job_title else '',
        'current_required_employees': employee_names,
        'current_required_employees_text': '، '.join(employee_names),
        'employee_label': 'الموظفون' if len(employee_names) > 1 else 'الموظف',
        'responsible_state': responsible_state,
        'responsible_message': responsible_message,
        'has_configured_levels': bool(total),
        'is_complete': is_complete,
        'is_current_pending': bool(current_level),
    }


def build_spare_part_card_summary(spare_request, employee_cache=None):
    workflow_type = get_spare_part_workflow_type(spare_request)
    parts = list(spare_request.spare_parts.all())
    return {
        'request': spare_request,
        'request_id': spare_request.pk,
        'request_type': spare_request.get_order_kind_display(),
        'status': spare_request.get_status_display(),
        'description': spare_request.description,
        'parts_count': len(parts),
        'parts': parts[:3],
        'extra_parts_count': max(len(parts) - 3, 0),
        'approval': build_card_approval_summary(
            workflow_type,
            spare_request.acceptance,
            spare_request.status == SPARE_PART_WAITING_STATUS,
            employee_cache,
        ),
        'is_accepted_all_part_or_some_none':spare_request.is_accepted_all_part_or_some_none(),
    }


def build_report_card_summary(report, employee_cache=None):
    return {
        'report': report,
        'report_id': report.pk,
        'summary': report.complete_report,
        'status': report.get_status_display(),
        'approval': build_card_approval_summary(
            ApprovalWorkflow.ACHIEVEMENT_REPORT,
            report.acceptance,
            report.status == REPORT_WAITING_STATUS,
            employee_cache,
        ),
    }


def select_active_card_workflow(spare_part_summary, report_summary):
    summaries = [summary for summary in (spare_part_summary, report_summary) if summary]
    for summary in summaries:
        approval = summary['approval']
        if approval['is_current_pending']:
            return approval
    for summary in summaries:
        approval = summary['approval']
        if approval['has_configured_levels'] and not approval['is_complete']:
            return approval
    return None


def build_card_latest_action(spare_request, report):
    candidates = []
    if spare_request and getattr(spare_request, 'latest_approval', None):
        candidates.append(('طلب قطع الغيار', spare_request.latest_approval))
    if report and getattr(report, 'latest_approval', None):
        candidates.append(('تقرير الإنجاز', report.latest_approval))
    if not candidates:
        return None
    label, approval = max(candidates, key=lambda item: item[1].created_at)
    approver_name = approval.approver.get_full_name() or approval.approver.username
    approver_profile = get_approver_profile(approval)
    approver_job_title = getattr(approver_profile, 'job_title', None)
    required_job_title = (
        approval.delegated_for_job_title
        or approval.required_job_title
        or getattr(approval.approval_level, 'job_title', None)
    )
    is_delegated = bool(
        
        getattr(approval, 'approval_mode', '') == APPROVAL_MODE_DELEGATED
        or (
            required_job_title
            and approver_job_title
            and approver_job_title.pk != required_job_title.pk
        )
    )
    return {
        'label': label,
        'decision': approval.get_decision_display(),
        'approver': approver_name,
        'approver_job_title': approver_job_title.title_name if approver_job_title else '',
        'is_delegated': is_delegated,
        'delegated_for_job_title': required_job_title.title_name if is_delegated and required_job_title else '',
        'approval_mode_label': approval_history_mode_label(approval, is_delegated),
        'created_at': approval.created_at,
    }


def normalize_requested_approval_mode(value):
    value = (value or '').strip()
    if not value:
        return None
    if value in VALID_APPROVAL_MODES:
        return value
    raise PermissionDenied("وضع التعميد غير صالح.")


def get_requested_approval_mode(request):
    return normalize_requested_approval_mode(request.POST.get('approval_mode'))


def resolve_approval_level_authorization(permissions, approval_level, requested_mode=None):
    job_title = getattr(permissions, 'job_title', None)
    effective_user = getattr(permissions, '_effective_user', None)
    if not job_title or approval_level is None:
        return {
            'allowed': False,
            'approval_mode': '',
            'approval_level': approval_level,
            'required_job_title': getattr(approval_level, 'job_title', None),
            'delegated_for_job_title': None,
            'approver_job_title': job_title,
            'normal_allowed': False,
            'delegated_allowed': False,
        }
    normal_permission_name = f"approval_level:{approval_level.pk}"
    delegation_permission_name = f"approval_delegation:{approval_level.pk}"
    normal_denied = user_permission_override_matches(
        effective_user,
        normal_permission_name,
        UserPermissionOverride.ACTION_DENY,
    )
    delegated_denied = user_permission_override_matches(
        effective_user,
        delegation_permission_name,
        UserPermissionOverride.ACTION_DENY,
    )
    delegated_granted = user_permission_override_matches(
        effective_user,
        delegation_permission_name,
        UserPermissionOverride.ACTION_GRANT,
    )
    normal_allowed = approval_level.job_title_id == job_title.id and not normal_denied
    delegated_allowed = ApprovalDelegation.objects.filter(
        source_job_title=job_title,
        approval_level=approval_level,
    ).exists() or delegated_granted
    delegated_allowed = delegated_allowed and not delegated_denied

    if requested_mode == APPROVAL_MODE_NORMAL:
        selected_mode = APPROVAL_MODE_NORMAL if normal_allowed else ''
    elif requested_mode == APPROVAL_MODE_DELEGATED:
        selected_mode = APPROVAL_MODE_DELEGATED if delegated_allowed else ''
    elif normal_allowed:
        selected_mode = APPROVAL_MODE_NORMAL
    elif delegated_allowed:
        selected_mode = APPROVAL_MODE_DELEGATED
    else:
        selected_mode = ''

    if selected_mode == APPROVAL_MODE_NORMAL:
        return {
            'allowed': True,
            'approval_mode': APPROVAL_MODE_NORMAL,
            'approval_level': approval_level,
            'required_job_title': approval_level.job_title,
            'delegated_for_job_title': None,
            'approver_job_title': job_title,
            'normal_allowed': normal_allowed,
            'delegated_allowed': delegated_allowed,
        }
    if selected_mode == APPROVAL_MODE_DELEGATED:
        return {
            'allowed': True,
            'approval_mode': APPROVAL_MODE_DELEGATED,
            'approval_level': approval_level,
            'required_job_title': approval_level.job_title,
            'delegated_for_job_title': approval_level.job_title,
            'approver_job_title': job_title,
            'normal_allowed': normal_allowed,
            'delegated_allowed': delegated_allowed,
        }
    return {
        'allowed': False,
        'approval_mode': '',
        'approval_level': approval_level,
        'required_job_title': approval_level.job_title,
        'delegated_for_job_title': None,
        'approver_job_title': job_title,
        'normal_allowed': normal_allowed,
        'delegated_allowed': delegated_allowed,
    }


def can_approve_level(permissions, approval_level):
    return resolve_approval_level_authorization(permissions, approval_level)['allowed']


def approval_record_metadata(authorization):
    return {
        'approval_mode': authorization['approval_mode'] or 'normal',
        'required_job_title': authorization['required_job_title'],
        'delegated_for_job_title': authorization['delegated_for_job_title'],
    }


def approval_action_label(authorization, normal_label):
    required_job_title = authorization.get('required_job_title')
    if authorization.get('approval_mode') == APPROVAL_MODE_DELEGATED and required_job_title:
        return f"{normal_label} نيابة عن {required_job_title.title_name}"
    return normal_label


def approval_history_mode_label(approval, approved_by_delegation=None):
    if not approval:
        return ''
    if approved_by_delegation is None:
        approved_by_delegation = getattr(approval, 'approval_mode', '') == APPROVAL_MODE_DELEGATED
    return 'تعميد نيابة' if approved_by_delegation else 'تعميد بصفته'


def build_approval_action_options(
    request,
    authorization,
    workflow_type,
    subject_number,
    action_url,
    extra_action='',
    employee_cache=None,
):
    if not authorization or not authorization.get('allowed'):
        return []

    current_user_name = request.user.get_full_name() or request.user.username
    approver_job_title = authorization.get('approver_job_title')
    required_job_title = authorization.get('required_job_title')
    approval_level = authorization.get('approval_level')
    required_employee_names = compact_employee_names(get_job_title_employee_names(required_job_title, employee_cache))
    required_employee_names_text = '، '.join(required_employee_names)
    workflow_label = APPROVAL_CONFIRMATION_WORKFLOW_LABELS.get(workflow_type, 'طلب')
    current_job_title_name = approver_job_title.title_name if approver_job_title else 'بدون مسمى وظيفي'
    required_job_title_name = required_job_title.title_name if required_job_title else 'غير محدد'
    level_label = f"المستوى {approval_level.order_number}" if approval_level else 'المستوى الحالي'

    base_data = {
        'action_url': action_url,
        'extra_action': extra_action,
        'workflow_label': workflow_label,
        'subject_number': subject_number,
        'current_user_name': current_user_name,
        'current_user_job_title': current_job_title_name,
        'required_level': level_label,
        'required_job_title': required_job_title_name,
        'required_employee_names': required_employee_names,
        'required_employee_names_text': required_employee_names_text,
        'has_required_employees': bool(required_employee_names),
    }

    actions = []
    if authorization.get('normal_allowed'):
        actions.append({
            **base_data,
            'mode': APPROVAL_MODE_NORMAL,
            'button_label': f"تعميد بصفتي - {current_job_title_name}",
            'button_class': 'btn-success',
            'modal_title': 'تأكيد التعميد',
            'confirm_label': 'تأكيد التعميد',
            'confirmation_text': f"هل تريد تعميد {workflow_label} رقم #{subject_number} بصفتك {current_job_title_name}؟",
        })
    if authorization.get('delegated_allowed'):
        actions.append({
            **base_data,
            'mode': APPROVAL_MODE_DELEGATED,
            'button_label': f"تعميد نيابة عن {required_job_title_name}",
            'button_class': 'btn-warning',
            'modal_title': 'تأكيد التعميد نيابة عن',
            'confirm_label': 'تأكيد التعميد نيابة عن',
            'confirmation_text': (
                f"أنت على وشك تعميد {workflow_label} رقم #{subject_number} نيابة عن {required_job_title_name}. "
                "سيتم تسجيل العملية باسمك مع توضيح أنك اعتمدت نيابة عن هذا المستوى. هل تريد المتابعة؟"
            ),
        })
    return actions


def has_user_approval(obj, user, approval_level=None):
    if not obj or not user.is_authenticated:
        return False
    prefetched = getattr(obj, '_prefetched_objects_cache', {})
    if 'approvals' in prefetched:
        approvals = prefetched['approvals']
        if approval_level is not None:
            return any(
                approval.approver_id == user.pk and approval.approval_level_id == approval_level.pk
                for approval in approvals
            )
        return any(approval.approver_id == user.pk for approval in approvals)
    queryset = obj.approvals.filter(approver=user)
    if approval_level is not None:
        queryset = queryset.filter(approval_level=approval_level)
    return queryset.exists()


def latest_related(obj, related_name):
    related_objects = list(getattr(obj, related_name).all())
    return max(related_objects, key=lambda item: item.pk, default=None)


def latest_approval(obj):
    if not obj:
        return None
    approvals = list(obj.approvals.all())
    return max(approvals, key=lambda approval: approval.created_at, default=None)


def get_approver_profile(approval):
    return getattr(approval.approver, 'profile', None) if approval and approval.approver else None


def approval_tracking_status_for_level(level, approval, current_pending_order):
    if approval and approval.decision == 'accepted':
        return {
            'status_key': 'approved',
            'status_label': 'تم التعميد',
            'status_badge_class': 'badge-success',
            'status_icon': 'check_circle',
            'is_approved': True,
            'is_rejected': False,
            'is_current_pending_level': False,
        }
    if approval and approval.decision == 'rejected':
        return {
            'status_key': 'rejected',
            'status_label': 'تم الرفض',
            'status_badge_class': 'badge-danger',
            'status_icon': 'cancel',
            'is_approved': False,
            'is_rejected': True,
            'is_current_pending_level': False,
        }
    if current_pending_order == level.order_number:
        return {
            'status_key': 'current_pending',
            'status_label': 'بانتظار التعميد',
            'status_badge_class': 'badge-warning',
            'status_icon': 'hourglass_empty',
            'is_approved': False,
            'is_rejected': False,
            'is_current_pending_level': True,
        }
    return {
        'status_key': 'waiting',
        'status_label': 'لم يصل الدور بعد / قيد الانتظار',
        'status_badge_class': 'badge-secondary',
        'status_icon': 'schedule',
        'is_approved': False,
        'is_rejected': False,
        'is_current_pending_level': False,
    }


def build_approval_tracking_section(workflow_type, subject, subject_label, current_order, is_waiting_for_approval):
    levels = list(
        ApprovalLevel.objects.filter(workflow_type=workflow_type)
        .select_related('job_title')
        .order_by('order_number')
    )
    approvals = list(
        subject.approvals.filter(
            approval_level__workflow_type=workflow_type,
            approval_level__isnull=False,
        )
        .select_related(
            'approval_level',
            'approval_level__job_title',
            'required_job_title',
            'delegated_for_job_title',
            'approver',
            'approver__profile__job_title',
        )
        .order_by('created_at')
    )
    approvals_by_level = {
        approval.approval_level_id: approval
        for approval in approvals
    }
    current_pending_order = None
    if is_waiting_for_approval:
        pending_level = next(
            (
                level for level in levels
                if level.order_number > current_order and level.pk not in approvals_by_level
            ),
            None,
        )
        current_pending_order = pending_level.order_number if pending_level else None

    tracking_levels = []
    for level in levels:
        approval = approvals_by_level.get(level.pk)
        approver_profile = get_approver_profile(approval)
        approved_by_job_title = getattr(approver_profile, 'job_title', None)
        status = approval_tracking_status_for_level(level, approval, current_pending_order)
        approved_by_name = ''
        if approval and approval.approver:
            approved_by_name = approval.approver.get_full_name() or approval.approver.username
        if approval and getattr(approval, 'approval_mode', ''):
            approved_by_delegation = approval.approval_mode == APPROVAL_MODE_DELEGATED
        else:
            approved_by_delegation = bool(
                approval
                and approval.decision == 'accepted'
                and approved_by_job_title
                and approved_by_job_title.pk != level.job_title_id
            )
        delegated_for_job_title = ''
        if approved_by_delegation:
            delegated_for_job_title = (
                approval.delegated_for_job_title.title_name
                if approval and approval.delegated_for_job_title
                else level.job_title.title_name
            )
        tracking_levels.append({
            'order_number': level.order_number,
            'required_job_title': level.job_title.title_name,
            'is_approved': status['is_approved'],
            'is_rejected': status['is_rejected'],
            'is_current_pending_level': status['is_current_pending_level'],
            'status_key': status['status_key'],
            'status_label': status['status_label'],
            'status_badge_class': status['status_badge_class'],
            'status_icon': status['status_icon'],
            'approved_by': approved_by_name,
            'approved_by_job_title': approved_by_job_title.title_name if approved_by_job_title else '',
            'approved_at': approval.created_at if approval else None,
            'approved_by_delegation': approved_by_delegation,
            'delegated_for_job_title': delegated_for_job_title,
            'approval_mode_label': approval_history_mode_label(approval, approved_by_delegation) if approval else '',
            'decision': approval.decision if approval else '',
            'decision_label': approval.get_decision_display() if approval else '',
        })

    return {
        'workflow_label': APPROVAL_TRACKING_LABELS.get(workflow_type, workflow_type),
        'workflow_type': workflow_type,
        'subject_label': subject_label,
        'levels': tracking_levels,
        'has_configured_levels': bool(levels),
    }


def build_maintenance_approval_tracking_sections(_maintenance_request, spare_part_requests, latest_report):
    sections = []
    for spare_request in spare_part_requests:
        workflow_type = get_spare_part_workflow_type(spare_request)
        sections.append(
            build_approval_tracking_section(
                workflow_type=workflow_type,
                subject=spare_request,
                subject_label=f"طلب قطع الغيار #{spare_request.pk}",
                current_order=spare_request.acceptance,
                is_waiting_for_approval=spare_request.status == SPARE_PART_WAITING_STATUS,
            )
        )
    if latest_report:
        sections.append(
            build_approval_tracking_section(
                workflow_type=ApprovalWorkflow.ACHIEVEMENT_REPORT,
                subject=latest_report,
                subject_label=f"تقرير الإنجاز #{latest_report.pk}",
                current_order=latest_report.acceptance,
                is_waiting_for_approval=latest_report.status == REPORT_WAITING_STATUS,
            )
        )
    return sections


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
    elif is_workflow_complete(spare_part_request.acceptance, get_spare_part_workflow_type(spare_part_request)):
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


def can_approve_order(current_order, permissions, workflow_type):
    next_level = get_next_approval_level(workflow_type, current_order)
    return can_approve_level(permissions, next_level)


def get_approval_authorization(current_order, permissions, workflow_type):
    next_level = get_next_approval_level(workflow_type, current_order)
    return next_level, resolve_approval_level_authorization(permissions, next_level)


def get_spare_part_approval_settings(spare_part_request, permissions):
    if spare_part_request.order_kind == 'store_requisition':
        return {
            'permission_name': 'can_approve_store_requisition',
            'workflow_type': ApprovalWorkflow.STORE_ISSUE,
        }
    return {
        'permission_name': 'can_approve_purchase_order',
        'workflow_type': ApprovalWorkflow.PURCHASE,
    }


def can_user_approve_spare_part_request(spare_part_request, permissions):
    if spare_part_request.status != SPARE_PART_WAITING_STATUS:
        return False
    settings = get_spare_part_approval_settings(spare_part_request, permissions)
    current_level, authorization = get_approval_authorization(
        spare_part_request.acceptance,
        permissions,
        settings['workflow_type'],
    )
    return (
        getattr(permissions, settings['permission_name'], False)
        and current_level is not None
        and authorization['allowed']
    )


def can_show_spare_part_approval_buttons(user, spare_request):
    if not spare_request or not user.is_authenticated:
        return False
    if spare_request.status != SPARE_PART_WAITING_STATUS:
        return False
    if spare_request.maintenance_request.status != 'awaiting_parts':
        return False
    next_level = get_next_approval_level(get_spare_part_workflow_type(spare_request), spare_request.acceptance)
    if has_user_approval(spare_request, user, next_level):
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
    current_level, authorization = get_approval_authorization(
        report.acceptance,
        permissions,
        ApprovalWorkflow.ACHIEVEMENT_REPORT,
    )
    return (
        permissions.can_approve_achievement_report
        and current_level is not None
        and authorization['allowed']
    )


def can_show_complete_report_approval_buttons(user, report):
    if not report or not user.is_authenticated:
        return False
    if report.status != REPORT_WAITING_STATUS:
        return False
    if report.maintenance_request.status != MAINTENANCE_REPORT_WAITING_STATUS:
        return False
    next_level = get_next_approval_level(ApprovalWorkflow.ACHIEVEMENT_REPORT, report.acceptance)
    if has_user_approval(report, user, next_level):
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


def apply_maintenance_history_filters(queryset, params):
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
    if status in FINAL_MAINTENANCE_STATUSES:
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

    return queryset


def apply_maintenance_history_sorting(queryset, params):
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
    return queryset.order_by(MAINTENANCE_HISTORY_SORT_OPTIONS.get(sort, '-created_at'))


def get_maintenance_history_filter_options(base_queryset):
    department_ids = base_queryset.values_list('department_id', flat=True).distinct()
    engineer_ids = base_queryset.exclude(assigned_technician_id__isnull=True).values_list('assigned_technician_id', flat=True).distinct()
    return {
        'statuses': [
            {'value': value, 'label': _choice_label(MaintenanceRequest.STATUS_CHOICES, value)}
            for value in FINAL_MAINTENANCE_STATUSES
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
            for value, label in MAINTENANCE_HISTORY_SORT_LABELS.items()
        ],
    }


def get_maintenance_history_active_filters(params):
    filters = []
    q = (params.get('q') or '').strip()
    if q:
        filters.append({'label': 'بحث', 'value': q, 'remove_query': _query_string_without(params, 'q')})
    status = params.get('status')
    if status in FINAL_MAINTENANCE_STATUSES:
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
    sort = params.get('sort')
    if sort in MAINTENANCE_HISTORY_SORT_LABELS and sort != 'newest':
        filters.append({'label': 'الترتيب', 'value': MAINTENANCE_HISTORY_SORT_LABELS.get(sort, 'الأحدث أولاً'), 'remove_query': _query_string_without(params, 'sort')})
    return filters


def apply_spare_part_history_filters(queryset, params):
    q = (params.get('q') or '').strip()
    if q:
        search_query = (
            Q(description__icontains=q)
            | Q(maintenance_request__description__icontains=q)
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
    if status in FINAL_SPARE_PART_STATUSES:
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

    return queryset


def apply_spare_part_history_sorting(queryset, params):
    sort = params.get('sort') or 'newest'
    return queryset.order_by(SPARE_PART_HISTORY_SORT_OPTIONS.get(sort, '-created_at'))


def get_spare_part_history_filter_options(base_queryset):
    requester_engineer_ids = base_queryset.exclude(engineer_id__isnull=True).values_list('engineer_id', flat=True)
    assigned_engineer_ids = base_queryset.exclude(
        maintenance_request__assigned_technician_id__isnull=True,
    ).values_list('maintenance_request__assigned_technician_id', flat=True)
    engineer_ids = set(requester_engineer_ids) | set(assigned_engineer_ids)
    return {
        'statuses': [
            {'value': value, 'label': _choice_label(SparePartRequest.STATUS_CHOICES, value)}
            for value in FINAL_SPARE_PART_STATUSES
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
            for value, label in SPARE_PART_HISTORY_SORT_LABELS.items()
        ],
    }


def get_spare_part_history_active_filters(params):
    filters = []
    q = (params.get('q') or '').strip()
    if q:
        filters.append({'label': 'بحث', 'value': q, 'remove_query': _query_string_without(params, 'q')})
    status = params.get('status')
    if status in FINAL_SPARE_PART_STATUSES:
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
    sort = params.get('sort')
    if sort in SPARE_PART_HISTORY_SORT_LABELS and sort != 'newest':
        filters.append({'label': 'الترتيب', 'value': SPARE_PART_HISTORY_SORT_LABELS.get(sort, 'الأحدث أولاً'), 'remove_query': _query_string_without(params, 'sort')})
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
    return {
        level.order_number: level.job_title.title_name
        for level in ApprovalLevel.objects.filter(
            workflow_type=SPARE_PART_APPROVAL_WORKFLOWS.get(order_kind, ApprovalWorkflow.PURCHASE),
        ).select_related('job_title')
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
        can_view_dashboard = user_has_role_permission(
            request.user,
            'can_view_dashboard',
            auth_permission='maintenance.can_view_dashboard',
        )
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
            **maintenance_navigation_context('لوحة التحكم'),
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
        employee_cache = {}
        show_spare_part_reject_modals = False

        for maintenance_request in objects:
            latest_report = latest_related(maintenance_request, 'completed_report')
            latest_spare_part = latest_related(maintenance_request, 'spare_parts')
            maintenance_request.latest_report = latest_report
            maintenance_request.latest_spare_part = latest_spare_part
            if latest_spare_part:
                latest_spare_part.latest_approval = latest_approval(latest_spare_part)
                latest_spare_part.approval_total = get_workflow_max_order(get_spare_part_workflow_type(latest_spare_part))
                latest_spare_part.approval_percent = approval_progress_percent(
                    latest_spare_part.acceptance,
                    get_spare_part_workflow_type(latest_spare_part),
                )
            if latest_report:
                latest_report.latest_approval = latest_approval(latest_report)
                latest_report.approval_total = get_workflow_max_order(ApprovalWorkflow.ACHIEVEMENT_REPORT)
                latest_report.approval_percent = approval_progress_percent(
                    latest_report.acceptance,
                    ApprovalWorkflow.ACHIEVEMENT_REPORT,
                )
            spare_part_summary = (
                build_spare_part_card_summary(latest_spare_part, employee_cache)
                if latest_spare_part else None
            )
            report_summary = (
                build_report_card_summary(latest_report, employee_cache)
                if latest_report else None
            )
            active_workflow_summary = select_active_card_workflow(spare_part_summary, report_summary)
            maintenance_request.spare_part_summary = spare_part_summary
            maintenance_request.achievement_report_summary = report_summary
            maintenance_request.active_workflow_type = active_workflow_summary['workflow_type'] if active_workflow_summary else ''
            maintenance_request.current_approval_level = active_workflow_summary['current_level'] if active_workflow_summary else None
            maintenance_request.current_required_job_title = active_workflow_summary['current_required_job_title'] if active_workflow_summary else ''
            maintenance_request.current_required_employees = active_workflow_summary['current_required_employees'] if active_workflow_summary else []
            maintenance_request.approval_progress_current = active_workflow_summary['progress_current'] if active_workflow_summary else 0
            maintenance_request.approval_progress_total = active_workflow_summary['progress_total'] if active_workflow_summary else 0
            maintenance_request.latest_action_summary = build_card_latest_action(latest_spare_part, latest_report)
            maintenance_request.can_approve_spare_part_from_card = can_show_spare_part_approval_buttons(
                self.request.user,
                latest_spare_part,
            )
            if latest_spare_part:
                spare_workflow_type = get_spare_part_workflow_type(latest_spare_part)
                _spare_level, spare_authorization = get_approval_authorization(
                    latest_spare_part.acceptance,
                    permissions,
                    spare_workflow_type,
                )
                maintenance_request.spare_part_approval_action_label = approval_action_label(
                    spare_authorization,
                    'اعتماد طلب القطع',
                )
                maintenance_request.spare_part_partial_reject_action_label = approval_action_label(
                    spare_authorization,
                    'قبول الطلب ورفض بعض القطع',
                )
                maintenance_request.spare_part_reject_action_label = approval_action_label(
                    spare_authorization,
                    'رفض كل القطع',
                )
                maintenance_request.spare_part_approval_actions = build_approval_action_options(
                    self.request,
                    spare_authorization,
                    spare_workflow_type,
                    latest_spare_part.pk,
                    reverse('spare_part_approve_from_card', kwargs={'pk': latest_spare_part.pk}),
                    employee_cache=employee_cache,
                ) if maintenance_request.can_approve_spare_part_from_card else []
            else:
                maintenance_request.spare_part_approval_action_label = 'اعتماد طلب القطع'
                maintenance_request.spare_part_partial_reject_action_label = 'قبول الطلب ورفض بعض القطع'
                maintenance_request.spare_part_reject_action_label = 'رفض كل القطع'
                maintenance_request.spare_part_approval_actions = []
            if maintenance_request.can_approve_spare_part_from_card:
                show_spare_part_reject_modals = True
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
                and can_show_complete_report_approval_buttons(self.request.user, latest_report)
            )
            if latest_report:
                _report_level, report_authorization = get_approval_authorization(
                    latest_report.acceptance,
                    permissions,
                    ApprovalWorkflow.ACHIEVEMENT_REPORT,
                )
                maintenance_request.report_approval_action_label = approval_action_label(
                    report_authorization,
                    'تعميد التقرير',
                )
                maintenance_request.report_approval_actions = build_approval_action_options(
                    self.request,
                    report_authorization,
                    ApprovalWorkflow.ACHIEVEMENT_REPORT,
                    latest_report.pk,
                    reverse('maintenance_complete_report_acceptance', kwargs={'pk': maintenance_request.pk}),
                    extra_action='approve_report',
                    employee_cache=employee_cache,
                ) if maintenance_request.can_approve_report else []
            else:
                maintenance_request.report_approval_action_label = 'تعميد التقرير'
                maintenance_request.report_approval_actions = []
        
        context['permissions'] = permissions
        context['objects'] = objects
        context['object_list'] = objects
        context['show_spare_part_reject_modals'] = show_spare_part_reject_modals
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
        context.update(maintenance_navigation_context('الطلبات النشطة'))
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
    paginate_by = 12

    def get_queryset(self):
        self.base_queryset = get_final_maintenance_requests(self.request.user, maintenance_queryset())
        queryset = apply_maintenance_history_filters(self.base_queryset, self.request.GET)
        return apply_maintenance_history_sorting(queryset, self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = getattr(
            self,
            'base_queryset',
            get_final_maintenance_requests(self.request.user, maintenance_queryset()),
        )
        filter_options = get_maintenance_history_filter_options(base_queryset)
        active_filters = get_maintenance_history_active_filters(self.request.GET)
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
        context['permissions'] = get_permissions(self.request.user)
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
        }
        context['result_count'] = context['paginator'].count if context.get('is_paginated') else len(objects)
        context.update(maintenance_navigation_context('سجل طلبات الصيانة'))
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
            spare_request.approval_total = get_workflow_max_order(get_spare_part_workflow_type(spare_request))
            spare_request.approval_percent = approval_progress_percent(
                spare_request.acceptance,
                get_spare_part_workflow_type(spare_request),
            )
            spare_request.can_approve = can_show_spare_part_approval_buttons(user, spare_request)
            spare_request.can_reject = can_show_spare_part_reject_button(user, spare_request)
            spare_request.can_resubmit = can_show_spare_part_resubmit_button(user, spare_request)
            spare_request.can_mark_available = can_show_purchase_available_button(user, spare_request)
            spare_request.can_issue = can_show_issue_from_store_button(user, spare_request)
            spare_request.can_confirm = can_show_confirm_issuance_button(user, spare_request)
            _spare_level, spare_authorization = get_approval_authorization(
                spare_request.acceptance,
                permissions,
                get_spare_part_workflow_type(spare_request),
            )
            spare_request.approval_action_label = approval_action_label(spare_authorization, 'اعتماد')
            spare_request.reject_action_label = approval_action_label(spare_authorization, 'رفض')
            spare_request.partial_reject_action_label = approval_action_label(
                spare_authorization,
                'قبول الطلب ورفض بعض القطع',
            )
            spare_request.approval_actions = build_approval_action_options(
                self.request,
                spare_authorization,
                get_spare_part_workflow_type(spare_request),
                spare_request.pk,
                reverse('spare_part_approve_from_detail', kwargs={'pk': spare_request.pk}),
            ) if spare_request.can_approve else []

        if latest_report: 
            latest_report.latest_approval = latest_approval(latest_report)
            latest_report.approval_total = get_workflow_max_order(ApprovalWorkflow.ACHIEVEMENT_REPORT)
            latest_report.approval_percent = approval_progress_percent(
                latest_report.acceptance,
                ApprovalWorkflow.ACHIEVEMENT_REPORT,
            )
            latest_report.can_approve = can_show_complete_report_approval_buttons(user, latest_report)
            latest_report.can_reject = can_show_complete_report_reject_button(user, latest_report)
            latest_report.can_resubmit = can_show_complete_report_resubmit_button(user, latest_report)
            _report_level, report_authorization = get_approval_authorization(
                latest_report.acceptance,
                permissions,
                ApprovalWorkflow.ACHIEVEMENT_REPORT,
            )
            latest_report.approval_action_label = approval_action_label(report_authorization, 'اعتماد التقرير')
            latest_report.reject_action_label = approval_action_label(report_authorization, 'رفض التقرير')
            latest_report.approval_actions = build_approval_action_options(
                self.request,
                report_authorization,
                ApprovalWorkflow.ACHIEVEMENT_REPORT,
                latest_report.pk,
                reverse('complete_report_approve_from_detail', kwargs={'pk': latest_report.pk}),
            ) if latest_report.can_approve else []

        context['latest_report'] = latest_report
        context['complete_report'] = latest_report
        context['spare_part_requests'] = spare_part_requests
        context['approval_tracking_sections'] = build_maintenance_approval_tracking_sections(
            maintenance_request,
            spare_part_requests,
            latest_report,
        )
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
        context.update(maintenance_navigation_context(
            f'تفاصيل طلب صيانة #{maintenance_request.pk}',
            parent_label='الطلبات النشطة',
            parent_url_name='request_list',
            show_back_button=True,
            back_url_name='request_list',
        ))
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
        if get_permissions(self.request.user).can_add_maintenance and get_permissions(self.request.user).is_department_manager:
            if profile and profile.managing_department:
                form.instance.department = profile.managing_department
            else:
                messages.error(self.request, "عذراً، لا يمكن تقديم طلب صيانة بدون تحديد قسم. يرجى التأكد من أن لديك قسمًا مرتبطًا في ملفك الشخصي.")
                form.add_error('department', "يرجى تحديد قسم في ملفك الشخصي أو الاتصال بالدعم الفني.")
                return self.form_invalid(form)
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
        context.update(maintenance_navigation_context(
            'إضافة طلب صيانة',
            parent_label='الطلبات النشطة',
            parent_url_name='request_list',
            show_back_button=True,
            back_url_name='request_list',
        ))
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(maintenance_navigation_context(
            f'تعديل طلب صيانة #{self.object.pk}',
            parent_label='الطلبات النشطة',
            parent_url_name='request_list',
            show_back_button=True,
            back_url_name='request_maintenance_details',
            back_url_kwargs={'pk': self.object.pk},
        ))
        return context


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

            current_report_level = get_next_approval_level(ApprovalWorkflow.ACHIEVEMENT_REPORT, report.acceptance)
            requested_approval_mode = get_requested_approval_mode(request)
            report_authorization = resolve_approval_level_authorization(
                permissions,
                current_report_level,
                requested_approval_mode,
            )

            if action == 'reject_report':
                if not can_user_approve_complete_report(report, permissions) or current_report_level is None or not report_authorization['allowed']:
                    raise PermissionDenied("لا يمكنك رفض التقرير في مرحلة التعميد الحالية.")
                if current_report_level and report.approvals.filter(approver=request.user, approval_level=current_report_level).exists():
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
                        approval_level=current_report_level,
                        **approval_record_metadata(report_authorization),
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
                        new_data={
                            'status': report.status,
                            'maintenance_status': report.maintenance_request.status,
                            'cause': cause,
                            'rejected_by': request.user.pk,
                            'approval_level': current_report_level.pk,
                            'approval_mode': report_authorization['approval_mode'],
                            'required_job_title': report_authorization['required_job_title'].pk if report_authorization['required_job_title'] else None,
                            'delegated_for_job_title': report_authorization['delegated_for_job_title'].pk if report_authorization['delegated_for_job_title'] else None,
                        },
                        description=f"تم رفض تقرير الإنجاز رقم {report.pk}. السبب: {cause}",
                        request=request,
                    )
                notify_complete_report_rejected(report)
                messages.warning(request, "تم رفض تقرير الإنجاز وإعادته للمهندس للتعديل")
                return redirect_after_maintenance_action(request, maintenance_request)

            if not can_user_approve_complete_report(report, permissions) or current_report_level is None or not report_authorization['allowed']:
                raise PermissionDenied("لا يمكن تعميد التقرير قبل اكتمال التعميدات السابقة أو بعد تعميدك له.")
            if current_report_level and report.approvals.filter(approver=request.user, approval_level=current_report_level).exists():
                raise PermissionDenied("لا يمكنك تعميد التقرير أكثر من مرة.")
            with transaction.atomic():
                old_report_data = {'status': report.status, 'acceptance': report.acceptance}
                old_maintenance_status = maintenance_request.status
                approval = CompleteReportApproval.objects.create(
                    report=report,
                    approver=request.user,
                    approval_level=current_report_level,
                    **approval_record_metadata(report_authorization),
                    decision='accepted',
                )
                report.acceptance = max(report.acceptance, current_report_level.order_number)
                if is_workflow_complete(report.acceptance, ApprovalWorkflow.ACHIEVEMENT_REPORT):
                    report.status = 'accepted'
                report.save(update_fields=['acceptance', 'status'])
                update_maintenance_status_from_complete_report(report)
                create_audit_log(
                    request.user,
                    AuditLog.ACTION_APPROVE,
                    report,
                    old_data={**old_report_data, 'maintenance_status': old_maintenance_status},
                    new_data={
                        'status': report.status,
                        'acceptance': report.acceptance,
                        'maintenance_status': report.maintenance_request.status,
                        'approver': request.user.pk,
                        'approval_level': current_report_level.pk,
                        'approval_mode': report_authorization['approval_mode'],
                        'required_job_title': report_authorization['required_job_title'].pk if report_authorization['required_job_title'] else None,
                        'delegated_for_job_title': report_authorization['delegated_for_job_title'].pk if report_authorization['delegated_for_job_title'] else None,
                    },
                    description=f"تم اعتماد تقرير الإنجاز رقم {report.pk}",
                    request=request,
                )
                notify_delegated_complete_report_approval(report, approval)
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

        objects = list(context['objects'])
        employee_cache = {}

        for part_request in objects:
            workflow_type = get_spare_part_workflow_type(part_request)
            next_level = get_next_approval_level(workflow_type, part_request.acceptance)
            _approval_level, approval_authorization = get_approval_authorization(
                part_request.acceptance,
                permissions,
                workflow_type,
            )
            part_request.approval_total = get_workflow_max_order(workflow_type)
            part_request.approval_percent = approval_progress_percent(part_request.acceptance, workflow_type)
            part_request.next_approval_title = (
                "مكتمل التعميد"
                if next_level is None
                else next_level.job_title.title_name
            )
            part_request.accept_all_action_label = approval_action_label(
                approval_authorization,
                'قبول الطلب بجميع القطع',
            )
            part_request.partial_reject_action_label = approval_action_label(
                approval_authorization,
                'قبول الطلب ورفض بعض القطع',
            )
            part_request.reject_action_label = approval_action_label(
                approval_authorization,
                'رفض كل القطع',
            )
            already_approved = has_user_approval(part_request, self.request.user, next_level)
            part_request.can_take_approval_action = (
                can_user_approve_spare_part_request(part_request, permissions)
                and not already_approved
            )
            part_request.approval_actions = build_approval_action_options(
                self.request,
                approval_authorization,
                workflow_type,
                part_request.pk,
                reverse('spare_part_acceptance', kwargs={'pk': part_request.pk}),
                employee_cache=employee_cache,
            ) if part_request.can_take_approval_action else []
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
        context.update(maintenance_navigation_context('طلبات قطع الغيار'))
        return context


class SparePartRequestHistoryView(LoginRequiredMixin, ListView):
    model = SparePartRequest
    template_name = 'requests_spare_part_history.html'
    context_object_name = 'objects'
    paginate_by = 10

    def get_queryset(self):
        self.base_queryset = get_final_spare_part_requests(self.request.user, spare_part_request_queryset())
        queryset = apply_spare_part_history_filters(self.base_queryset, self.request.GET)
        return apply_spare_part_history_sorting(queryset, self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = getattr(
            self,
            'base_queryset',
            get_final_spare_part_requests(self.request.user, spare_part_request_queryset()),
        )
        filter_options = get_spare_part_history_filter_options(base_queryset)
        active_filters = get_spare_part_history_active_filters(self.request.GET)
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
        for part_request in objects:
            part_request.approval_total = get_workflow_max_order(get_spare_part_workflow_type(part_request))

        context['objects'] = objects
        context['object_list'] = objects
        context['permissions'] = get_permissions(self.request.user)
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
        }
        context['result_count'] = context['paginator'].count if context.get('is_paginated') else len(objects)
        context['list_title'] = 'سجل طلبات قطع الغيار المكتملة والمرفوضة'
        context['list_description'] = 'يعرض هذا السجل طلبات قطع الغيار التي انتهت بالصرف المؤكد أو الرفض فقط'
        context['show_spare_part_actions'] = False
        context.update(maintenance_navigation_context('سجل طلبات القطع'))
        return context


class SparePartRequestDetails(LoginRequiredMixin, DetailView):
    model = SparePartRequest
    template_name = 'request_spare_part_details.html'
    context_object_name = 'part_request'

    def get_queryset(self):
        return get_visible_spare_part_requests(self.request.user, spare_part_request_queryset())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        part_request = context['part_request']
        permissions = get_permissions(self.request.user)
        workflow_type = get_spare_part_workflow_type(part_request)
        next_level = get_next_approval_level(workflow_type, part_request.acceptance)
        _approval_level, approval_authorization = get_approval_authorization(
            part_request.acceptance,
            permissions,
            workflow_type,
        )
        part_request.approval_total = get_workflow_max_order(workflow_type)
        part_request.approval_percent = approval_progress_percent(part_request.acceptance, workflow_type)
        part_request.can_take_approval_action = (
            can_user_approve_spare_part_request(part_request, permissions)
            and not has_user_approval(part_request, self.request.user, next_level)
        )
        part_request.approval_actions = build_approval_action_options(
            self.request,
            approval_authorization,
            workflow_type,
            part_request.pk,
            reverse('spare_part_acceptance', kwargs={'pk': part_request.pk}),
        ) if part_request.can_take_approval_action else []
        context['approval_levels'] = ApprovalLevel.objects.filter(
            workflow_type=workflow_type,
        ).select_related('job_title').order_by('order_number')
        context['approval_tracking_section'] = build_approval_tracking_section(
            workflow_type=workflow_type,
            subject=part_request,
            subject_label=f"طلب قطع الغيار #{part_request.pk}",
            current_order=part_request.acceptance,
            is_waiting_for_approval=part_request.status == SPARE_PART_WAITING_STATUS,
        )
        context['next_approval_level'] = next_level
        context.update(maintenance_navigation_context(
            f'تفاصيل طلب قطع غيار #{part_request.pk}',
            parent_label='طلبات قطع الغيار',
            parent_url_name='spare_part_list',
            show_back_button=True,
            back_url_name='spare_part_list',
        ))
        return context


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
    workflow_type = get_spare_part_workflow_type(obj)
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
        if obj.status not in ('bought_available_parts', 'issue_available_parts_issued_waiting_confirmation') or not is_workflow_complete(obj.acceptance, workflow_type) or not permissions.can_issue_from_store:
            messages.error(request, "لا يمكن صرف القطع قبل اكتمال التعميد أو بدون صلاحية الصرف.")
            raise PermissionDenied("لا يمكن صرف القطع قبل اكتمال التعميد أو بدون صلاحية الصرف.")
        old_status = obj.status
        max_order = get_workflow_max_order(workflow_type)
        with transaction.atomic():
            obj.spare_parts.exclude(is_rejected=True).update(acceptance=max_order, is_rejected=False)
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
    current_level = get_next_approval_level(workflow_type, obj.acceptance)
    requested_approval_mode = get_requested_approval_mode(request)
    authorization = resolve_approval_level_authorization(permissions, current_level, requested_approval_mode)
    if current_level and obj.approvals.filter(approver=request.user, approval_level=current_level).exists():
        messages.error(request, "لا يمكنك تعميد طلب قطع الغيار أكثر من مرة.")
        raise PermissionDenied("لا يمكنك تعميد طلب قطع الغيار أكثر من مرة.")

    settings = get_spare_part_approval_settings(obj, permissions)
    permission_name = settings['permission_name']

    if getattr(permissions, permission_name, False):
        if current_level is None or not authorization['allowed']:
            messages.error(request, "لا يمكن تعميد الطلب قبل اكتمال التعميدات السابقة.")
            raise PermissionDenied("لا يمكن تعميد الطلب قبل اكتمال التعميدات السابقة.")
        old_status = obj.status
        old_acceptance = obj.acceptance
        with transaction.atomic():
            approval = SparePartApproval.objects.create(
                request=obj,
                approver=request.user,
                approval_level=current_level,
                **approval_record_metadata(authorization),
                decision='accepted',
            )
            obj.acceptance = max(obj.acceptance, current_level.order_number)
            obj.spare_parts.exclude(is_rejected=True).update(acceptance=obj.acceptance, is_rejected=False)
            sync_spare_part_request_status(obj)
            create_audit_log(
                request.user,
                AuditLog.ACTION_APPROVE,
                obj,
                old_data={'status': old_status, 'acceptance': old_acceptance},
                new_data={
                    'status': obj.status,
                    'acceptance': obj.acceptance,
                    'approver': request.user.pk,
                    'approval_level': current_level.pk,
                    'approval_mode': authorization['approval_mode'],
                    'required_job_title': authorization['required_job_title'].pk if authorization['required_job_title'] else None,
                    'delegated_for_job_title': authorization['delegated_for_job_title'].pk if authorization['delegated_for_job_title'] else None,
                },
                description=f"تم تعميد طلب قطع الغيار رقم {obj.pk}",
                request=request,
            )
            notify_delegated_spare_part_approval(obj, approval)
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
    workflow_type = get_spare_part_workflow_type(spare_part_request)
    current_level = get_next_approval_level(workflow_type, spare_part_request.acceptance)
    authorization = resolve_approval_level_authorization(permissions, current_level)
    if spare_part_request.status in ('avaliable_parts_issued', 'rejected'):
        messages.error(request, "لا يمكن رفض طلب قطع غيار مكتمل أو مرفوض.")
        raise PermissionDenied("لا يمكن رفض طلب قطع غيار مكتمل أو مرفوض.")
    if spare_part_request.status != SPARE_PART_WAITING_STATUS:
        messages.error(request, "حالة طلب قطع الغيار لا تسمح بالرفض حالياً.")
        raise PermissionDenied("رفض طلب قطع الغيار متاح فقط أثناء انتظار التعميد.")
    can_reject = can_user_approve_spare_part_request(spare_part_request, permissions)
    if not can_reject or current_level is None or not authorization['allowed']:
        messages.error(request, "عذراً، لا تمتلك صلاحية رفض طلب قطع الغيار في هذه المرحلة.")
        raise PermissionDenied("عذراً، لا تمتلك الصلاحية للقيام بهذا الإجراء.")
    if current_level and spare_part_request.approvals.filter(approver=request.user, approval_level=current_level).exists():
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
            approval_level=current_level,
            **approval_record_metadata(authorization),
            decision='rejected',
            reason=cause,
        )
        if rejected_part_ids:
            order = current_level.order_number if current_level else spare_part_request.acceptance
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
                'approval_level': current_level.pk,
                'approval_mode': authorization['approval_mode'],
                'required_job_title': authorization['required_job_title'].pk if authorization['required_job_title'] else None,
                'delegated_for_job_title': authorization['delegated_for_job_title'].pk if authorization['delegated_for_job_title'] else None,
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
