from datetime import datetime, timedelta

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce, TruncDay, TruncMonth, TruncWeek
from django.utils import timezone

from accounts.models import Profile
from .models import (
    CompleteReportApproval,
    MaintenanceRequest,
    SparePartApproval,
    SparePartRequest,
    SpareParts,
    complete_report as CompleteReport,
)
from .access import (
    get_visible_complete_reports,
    get_visible_maintenance_requests,
    get_visible_spare_part_requests,
)


FINAL_MAINTENANCE_STATUSES = ('completed', 'rejected')
FINAL_SPARE_PART_STATUSES = ('avaliable_parts_issued', 'rejected')


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


def get_dashboard_date_range(request):
    now = timezone.localtime(timezone.now())
    today = now.date()
    period = request.GET.get('period', 'last_30_days')

    if period == 'today':
        start_date = today
        end_date = today
    elif period == 'last_7_days':
        start_date = today - timedelta(days=6)
        end_date = today
    elif period == 'last_90_days':
        start_date = today - timedelta(days=89)
        end_date = today
    elif period == 'this_year':
        start_date = today.replace(month=1, day=1)
        end_date = today
    elif period == 'custom':
        start_date = _parse_date(request.GET.get('from_date')) or today - timedelta(days=29)
        end_date = _parse_date(request.GET.get('to_date')) or today
        if start_date > end_date:
            start_date, end_date = end_date, start_date
    else:
        period = 'last_30_days'
        start_date = today - timedelta(days=29)
        end_date = today

    start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()))
    end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()))
    days_count = (end_date - start_date).days + 1
    if days_count <= 31:
        group_by = 'day'
    elif days_count <= 120:
        group_by = 'week'
    else:
        group_by = 'month'

    return {
        'period': period,
        'start_date': start_date,
        'end_date': end_date,
        'start_dt': start_dt,
        'end_dt': end_dt,
        'group_by': group_by,
    }


def _date_filtered(queryset, field_name, start_dt, end_dt):
    return queryset.filter(**{f'{field_name}__gte': start_dt, f'{field_name}__lte': end_dt})


def _format_duration(delta):
    if not delta:
        return 'غير متاح'
    total_seconds = int(delta.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return f'{days} يوم و {hours} ساعة'
    if hours:
        return f'{hours} ساعة و {minutes} دقيقة'
    return f'{minutes} دقيقة'


def _average_completion_duration(queryset):
    return queryset.annotate(
        finished_at=Coalesce('completed_at', 'updated_at'),
        duration=ExpressionWrapper(F('finished_at') - F('created_at'), output_field=DurationField()),
    ).aggregate(avg_duration=Avg('duration'))['avg_duration']


def _average_created_updated_duration(queryset):
    return queryset.annotate(
        duration=ExpressionWrapper(F('updated_at') - F('created_at'), output_field=DurationField()),
    ).aggregate(avg_duration=Avg('duration'))['avg_duration']


def _user_label(user):
    return user.get_full_name() or user.username


def _chart_from_counts(rows, label_key='label', value_key='count'):
    return {
        'labels': [str(row[label_key]) for row in rows],
        'values': [row[value_key] for row in rows],
    }


def get_dashboard_querysets(user):
    maintenance_queryset = get_visible_maintenance_requests(
        user,
        MaintenanceRequest.objects.select_related('requester', 'department', 'assigned_technician__user'),
    )
    spare_queryset = get_visible_spare_part_requests(
        user,
        SparePartRequest.objects.select_related('maintenance_request', 'engineer__user').prefetch_related('spare_parts'),
    )
    report_queryset = get_visible_complete_reports(
        user,
        CompleteReport.objects.select_related('maintenance_request', 'maintenance_request__assigned_technician__user'),
    )
    return {
        'maintenance': maintenance_queryset,
        'spare_parts': spare_queryset,
        'reports': report_queryset,
    }


def dashboard_has_visible_data(querysets):
    return (
        querysets['maintenance'].exists()
        or querysets['spare_parts'].exists()
        or querysets['reports'].exists()
    )


def get_maintenance_kpis(start_dt, end_dt, querysets):
    queryset = _date_filtered(querysets['maintenance'], 'created_at', start_dt, end_dt)
    completed = queryset.filter(status='completed')
    rejected_count = queryset.filter(status='rejected').count()
    completed_count = completed.count()
    closed_total = completed_count + rejected_count
    avg_duration = _average_completion_duration(completed)
    success_rate = round((completed_count / closed_total) * 100, 1) if closed_total else 0
    delayed_threshold = timezone.now() - timedelta(days=3)

    spare_queryset = _date_filtered(querysets['spare_parts'], 'created_at', start_dt, end_dt)
    reports = _date_filtered(querysets['reports'], 'created_at', start_dt, end_dt)

    return {
        'total_maintenance': queryset.count(),
        'active_maintenance': queryset.exclude(status__in=FINAL_MAINTENANCE_STATUSES).count(),
        'completed_maintenance': completed_count,
        'rejected_maintenance': rejected_count,
        'avg_completion_time': _format_duration(avg_duration),
        'total_spare_parts': spare_queryset.count(),
        'completed_spare_parts': spare_queryset.filter(status='avaliable_parts_issued').count(),
        'rejected_spare_parts': spare_queryset.filter(status='rejected').count(),
        'total_reports': reports.count(),
        'success_rate': success_rate,
        'delayed_requests': queryset.exclude(status__in=FINAL_MAINTENANCE_STATUSES).filter(created_at__lt=delayed_threshold).count(),
    }


def get_maintenance_charts_data(start_dt, end_dt, group_by, querysets):
    queryset = _date_filtered(querysets['maintenance'], 'created_at', start_dt, end_dt)
    if group_by == 'month':
        trunc = TruncMonth('created_at')
        date_format = '%Y-%m'
    elif group_by == 'week':
        trunc = TruncWeek('created_at')
        date_format = 'أسبوع %Y-%m-%d'
    else:
        trunc = TruncDay('created_at')
        date_format = '%Y-%m-%d'
    timeline_rows = queryset.annotate(period=trunc).values('period').annotate(count=Count('id')).order_by('period')
    timeline = {
        'labels': [row['period'].strftime(date_format) for row in timeline_rows],
        'values': [row['count'] for row in timeline_rows],
    }
    status_rows = queryset.values('status').annotate(count=Count('id')).order_by('status')
    department_rows = queryset.values('department__name').annotate(count=Count('id')).order_by('-count')[:8]
    latest_active = queryset.exclude(status__in=FINAL_MAINTENANCE_STATUSES).select_related(
        'department',
        'assigned_technician__user',
    ).order_by('-created_at')[:10]
    return {
        'timeline': timeline,
        'status': {
            'labels': [MaintenanceRequest(status=row['status']).get_status_display() for row in status_rows],
            'values': [row['count'] for row in status_rows],
        },
        'departments': {
            'labels': [row['department__name'] or 'غير محدد' for row in department_rows],
            'values': [row['count'] for row in department_rows],
        },
        'latest_active': latest_active,
    }


def get_engineer_performance_data(start_dt, end_dt, querysets):
    maintenance_queryset = querysets['maintenance']
    engineers = Profile.objects.filter(specialty__isnull=False).select_related('user', 'specialty').annotate(
        assigned_count=Count(
            'assigned_tasks',
            filter=Q(assigned_tasks__in=maintenance_queryset, assigned_tasks__created_at__gte=start_dt, assigned_tasks__created_at__lte=end_dt),
            distinct=True,
        ),
        completed_count=Count(
            'assigned_tasks',
            filter=Q(assigned_tasks__in=maintenance_queryset, assigned_tasks__created_at__gte=start_dt, assigned_tasks__created_at__lte=end_dt, assigned_tasks__status='completed'),
            distinct=True,
        ),
        active_count=Count(
            'assigned_tasks',
            filter=Q(assigned_tasks__in=maintenance_queryset, assigned_tasks__created_at__gte=start_dt, assigned_tasks__created_at__lte=end_dt) & ~Q(assigned_tasks__status__in=FINAL_MAINTENANCE_STATUSES),
            distinct=True,
        ),
        avg_completion_duration=Avg(
            ExpressionWrapper(
                Coalesce(F('assigned_tasks__completed_at'), F('assigned_tasks__updated_at')) - F('assigned_tasks__created_at'),
                output_field=DurationField(),
            ),
            filter=Q(assigned_tasks__in=maintenance_queryset, assigned_tasks__created_at__gte=start_dt, assigned_tasks__created_at__lte=end_dt, assigned_tasks__status='completed'),
        ),
    ).order_by('-completed_count', '-assigned_count')
    rows = [
        {
            'engineer': engineer,
            'assigned_count': engineer.assigned_count,
            'completed_count': engineer.completed_count,
            'active_count': engineer.active_count,
            'avg_completion_time': _format_duration(engineer.avg_completion_duration),
        }
        for engineer in engineers
        if engineer.assigned_count or engineer.completed_count or engineer.active_count
    ]
    return {
        'rows': rows,
        'top_completed_chart': {
            'labels': [_user_label(row['engineer'].user) for row in rows[:5]],
            'values': [row['completed_count'] for row in rows[:5]],
        },
    }


def get_spare_part_analytics(start_dt, end_dt, querysets):
    queryset = _date_filtered(querysets['spare_parts'], 'created_at', start_dt, end_dt)
    status_rows = queryset.values('status').annotate(count=Count('id')).order_by('status')
    kind_rows = queryset.values('order_kind').annotate(count=Count('id')).order_by('order_kind')
    part_rows = SpareParts.objects.filter(order__in=querysets['spare_parts'], order__created_at__gte=start_dt, order__created_at__lte=end_dt).values('part_name').annotate(
        total_quantity=Sum('quantity'),
    ).order_by('-total_quantity')[:8]
    latest_active = queryset.exclude(status__in=FINAL_SPARE_PART_STATUSES).select_related(
        'maintenance_request',
        'engineer__user',
    ).prefetch_related('spare_parts').order_by('-created_at')[:10]
    return {
        'status': {
            'labels': [SparePartRequest(status=row['status']).get_status_display() for row in status_rows],
            'values': [row['count'] for row in status_rows],
        },
        'kind': {
            'labels': [SparePartRequest(order_kind=row['order_kind']).get_order_kind_display() for row in kind_rows],
            'values': [row['count'] for row in kind_rows],
        },
        'top_parts': {
            'labels': [row['part_name'] for row in part_rows],
            'values': [row['total_quantity'] for row in part_rows],
        },
        'latest_active': latest_active,
    }


def get_complete_report_analytics(start_dt, end_dt, querysets):
    queryset = _date_filtered(querysets['reports'], 'created_at', start_dt, end_dt)
    status_rows = queryset.values('status').annotate(count=Count('id')).order_by('status')
    accepted_reports = queryset.filter(status='accepted')
    avg_duration = _average_created_updated_duration(accepted_reports)
    awaiting_reports = queryset.filter(status='awaiting_acceptenace').select_related(
        'maintenance_request',
        'maintenance_request__assigned_technician__user',
    ).order_by('-created_at')[:10]
    return {
        'status': {
            'labels': [CompleteReport(status=row['status']).get_status_display() for row in status_rows],
            'values': [row['count'] for row in status_rows],
        },
        'avg_acceptance_time': _format_duration(avg_duration),
        'awaiting_reports': awaiting_reports,
    }


def get_approval_analytics(start_dt, end_dt, querysets):
    spare_approvals = _date_filtered(
        SparePartApproval.objects.filter(request__in=querysets['spare_parts']).select_related('approver'),
        'created_at',
        start_dt,
        end_dt,
    )
    report_approvals = _date_filtered(
        CompleteReportApproval.objects.filter(report__in=querysets['reports']).select_related('approver'),
        'created_at',
        start_dt,
        end_dt,
    )
    spare_approval_rows = spare_approvals.values('approver_id', 'approver__username', 'approver__first_name', 'approver__last_name').annotate(
        accepted=Count('id', filter=Q(decision='accepted')),
        rejected=Count('id', filter=Q(decision='rejected')),
        total=Count('id'),
    )
    report_approval_rows = report_approvals.values('approver_id', 'approver__username', 'approver__first_name', 'approver__last_name').annotate(
        accepted=Count('id', filter=Q(decision='accepted')),
        rejected=Count('id', filter=Q(decision='rejected')),
        total=Count('id'),
    )
    approver_map = {}
    for approval_rows in (spare_approval_rows, report_approval_rows):
        for row in approval_rows:
            key = row['approver_id']
            full_name = f"{row['approver__first_name']} {row['approver__last_name']}".strip()
            approver_map.setdefault(key, {'name': full_name or row['approver__username'], 'accepted': 0, 'rejected': 0, 'total': 0})
            approver_map[key]['accepted'] += row['accepted']
            approver_map[key]['rejected'] += row['rejected']
            approver_map[key]['total'] += row['total']
    approvers = sorted(approver_map.values(), key=lambda item: item['total'], reverse=True)

    return {
        'spare_requests_approved': _date_filtered(querysets['spare_parts'].filter(acceptance__gte=8), 'updated_at', start_dt, end_dt).count(),
        'spare_requests_rejected': _date_filtered(querysets['spare_parts'].filter(status='rejected'), 'updated_at', start_dt, end_dt).count(),
        'reports_approved': _date_filtered(querysets['reports'].filter(status='accepted'), 'updated_at', start_dt, end_dt).count(),
        'reports_rejected': _date_filtered(querysets['reports'].filter(status='rejected'), 'updated_at', start_dt, end_dt).count(),
        'spare_avg_acceptance_time': _format_duration(_average_created_updated_duration(_date_filtered(querysets['spare_parts'].filter(acceptance__gte=8), 'updated_at', start_dt, end_dt))),
        'report_avg_acceptance_time': _format_duration(_average_created_updated_duration(_date_filtered(querysets['reports'].filter(status='accepted'), 'updated_at', start_dt, end_dt))),
        'approvers': approvers[:10],
        'top_approvers_chart': {
            'labels': [item['name'] for item in approvers[:5]],
            'values': [item['total'] for item in approvers[:5]],
        },
    }


def get_executive_analytics(start_dt, end_dt, querysets):
    queryset = _date_filtered(querysets['maintenance'], 'created_at', start_dt, end_dt)
    completed = queryset.filter(status='completed').count()
    rejected = queryset.filter(status='rejected').count()
    closed_total = completed + rejected
    delayed_now = timezone.now()
    delayed_base = queryset.exclude(status__in=FINAL_MAINTENANCE_STATUSES)
    return {
        'success_rate': round((completed / closed_total) * 100, 1) if closed_total else 0,
        'avg_lifecycle': _format_duration(_average_completion_duration(queryset.filter(status='completed'))),
        'delayed_table': [
            {'label': 'أكثر من 3 أيام', 'count': delayed_base.filter(created_at__lt=delayed_now - timedelta(days=3)).count()},
            {'label': 'أكثر من 7 أيام', 'count': delayed_base.filter(created_at__lt=delayed_now - timedelta(days=7)).count()},
            {'label': 'أكثر من 14 يوم', 'count': delayed_base.filter(created_at__lt=delayed_now - timedelta(days=14)).count()},
        ],
    }


def get_latest_activity_data(start_dt, end_dt, querysets):
    activities = []
    maintenance_rows = _date_filtered(querysets['maintenance'], 'updated_at', start_dt, end_dt).order_by('-updated_at')[:10]
    spare_rows = _date_filtered(querysets['spare_parts'], 'updated_at', start_dt, end_dt).order_by('-updated_at')[:10]
    report_rows = _date_filtered(querysets['reports'], 'updated_at', start_dt, end_dt).order_by('-updated_at')[:10]
    spare_approvals = _date_filtered(SparePartApproval.objects.filter(request__in=querysets['spare_parts']).select_related('approver', 'request'), 'created_at', start_dt, end_dt).order_by('-created_at')[:10]
    report_approvals = _date_filtered(CompleteReportApproval.objects.filter(report__in=querysets['reports']).select_related('approver', 'report'), 'created_at', start_dt, end_dt).order_by('-created_at')[:10]

    for item in maintenance_rows:
        activities.append({
            'time': item.updated_at,
            'type': 'طلب صيانة',
            'title': f'طلب صيانة #{item.pk}',
            'description': item.get_status_display(),
            'actor': _user_label(item.requester) if item.requester_id else 'النظام',
        })
    for item in spare_rows:
        activities.append({
            'time': item.updated_at,
            'type': 'قطع غيار',
            'title': f'طلب قطع غيار #{item.pk}',
            'description': item.get_status_display(),
            'actor': _user_label(item.engineer.user) if item.engineer and item.engineer.user_id else 'النظام',
        })
    for item in report_rows:
        actor = item.maintenance_request.assigned_technician.user if item.maintenance_request.assigned_technician else None
        activities.append({
            'time': item.updated_at,
            'type': 'تقرير إنجاز',
            'title': f'تقرير إنجاز #{item.pk}',
            'description': item.get_status_display(),
            'actor': _user_label(actor) if actor else 'النظام',
        })
    for item in spare_approvals:
        activities.append({
            'time': item.created_at,
            'type': 'تعميد قطع',
            'title': f'طلب قطع غيار #{item.request_id}',
            'description': item.get_decision_display(),
            'actor': _user_label(item.approver),
        })
    for item in report_approvals:
        activities.append({
            'time': item.created_at,
            'type': 'تعميد تقرير',
            'title': f'تقرير إنجاز #{item.report_id}',
            'description': item.get_decision_display(),
            'actor': _user_label(item.approver),
        })
    return sorted(activities, key=lambda item: item['time'], reverse=True)[:10]
