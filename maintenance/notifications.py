from django.contrib.auth.models import User
from django.db.models import Q
from django.urls import reverse

from accounts.models import JobTitlePermission
from .models import Notification


PERMISSION_ALIASES = {
    'can_accept_maintenance_request': 'can_approve_maintenance',
    'can_approve_spare_part_request': ('can_approve_store_requisition', 'can_approve_purchase_order'),
    'can_approve_complete_report': 'can_approve_achievement_report',
    'can_issue_from_stor': 'can_issue_from_store',
}


def maintenance_detail_url(maintenance_request):
    if not maintenance_request:
        return ''
    return reverse('request_maintenance_details', kwargs={'pk': maintenance_request.pk})


def _permission_fields(permission_codename):
    mapped = PERMISSION_ALIASES.get(permission_codename, permission_codename)
    if isinstance(mapped, (tuple, list)):
        return tuple(mapped)
    return (mapped,)


def get_users_with_permission(permission_codename):
    fields = _permission_fields(permission_codename)
    query = Q()
    for field in fields:
        query |= Q(**{f'profile__job_title__permissions__{field}': True})

    role_users = User.objects.filter(is_active=True).filter(query)
    auth_users = User.objects.filter(is_active=True).filter(
        Q(user_permissions__codename=permission_codename)
        | Q(groups__permissions__codename=permission_codename)
        | Q(is_superuser=True)
    )
    return (role_users | auth_users).distinct()


def _default_url(maintenance_request=None, spare_part_request=None, complete_report=None):
    if maintenance_request:
        return maintenance_detail_url(maintenance_request)
    if spare_part_request:
        return maintenance_detail_url(spare_part_request.maintenance_request)
    if complete_report:
        return maintenance_detail_url(complete_report.maintenance_request)
    return reverse('notifications_list')


def create_notification(
    recipient,
    title,
    message,
    notification_type='general',
    maintenance_request=None,
    spare_part_request=None,
    complete_report=None,
    url=None,
):
    if not recipient:
        return None

    url = url or _default_url(maintenance_request, spare_part_request, complete_report)
    duplicate_qs = Notification.objects.filter(
        recipient=recipient,
        notification_type=notification_type,
        related_maintenance_request=maintenance_request,
        related_spare_part_request=spare_part_request,
        related_complete_report=complete_report,
        is_read=False,
    )
    if duplicate_qs.exists():
        return None

    return Notification.objects.create(
        recipient=recipient,
        title=title,
        message=message,
        notification_type=notification_type,
        related_maintenance_request=maintenance_request,
        related_spare_part_request=spare_part_request,
        related_complete_report=complete_report,
        url=url,
    )


def notify_users(users, title, message, notification_type='general', **kwargs):
    notifications = []
    for user in users:
        notification = create_notification(user, title, message, notification_type, **kwargs)
        if notification:
            notifications.append(notification)
    return notifications


def notify_maintenance_needs_acceptance(maintenance_request):
    notify_users(
        get_users_with_permission('can_approve_maintenance'),
        "طلب صيانة جديد يحتاج معالجة",
        f"تم إنشاء طلب صيانة جديد رقم {maintenance_request.pk} ويحتاج قبول الطلب وتكليف مهندس.",
        'maintenance_needs_acceptance',
        maintenance_request=maintenance_request,
    )


def notify_maintenance_assigned(maintenance_request):
    engineer = maintenance_request.assigned_technician
    if engineer and engineer.user_id:
        create_notification(
            engineer.user,
            f"تم تعيينك على طلب صيانة #{maintenance_request.pk}",
            f"تم تعيينك على طلب صيانة رقم {maintenance_request.pk}.",
            'maintenance_assigned',
            maintenance_request=maintenance_request,
        )


def notify_maintenance_rejected(maintenance_request):
    create_notification(
        maintenance_request.requester,
        f"تم رفض طلب الصيانة #{maintenance_request.pk}",
        f"تم رفض طلب الصيانة رقم {maintenance_request.pk}.",
        'maintenance_rejected',
        maintenance_request=maintenance_request,
    )


def notify_assignment_rejected(maintenance_request, engineer_user):
    engineer_name = engineer_user.get_full_name() or engineer_user.username
    notify_users(
        get_users_with_permission('can_approve_maintenance'),
        f"رفض تكليف طلب صيانة #{maintenance_request.pk}",
        f"المهندس {engineer_name} رفض التكليف على طلب الصيانة رقم {maintenance_request.pk} ويحتاج إعادة تعيين.",
        'assignment_rejected',
        maintenance_request=maintenance_request,
    )


def notify_maintenance_completed(maintenance_request):
    recipients = []
    if maintenance_request.requester_id:
        recipients.append(maintenance_request.requester)
    if maintenance_request.assigned_technician and maintenance_request.assigned_technician.user_id:
        recipients.append(maintenance_request.assigned_technician.user)
    notify_users(
        {user for user in recipients if user},
        f"اكتمل طلب الصيانة #{maintenance_request.pk}",
        f"تم إكمال طلب الصيانة رقم {maintenance_request.pk}.",
        'maintenance_completed',
        maintenance_request=maintenance_request,
    )


def notify_spare_part_needs_approval(spare_part_request):
    permission = (
        'can_approve_purchase_order'
        if spare_part_request.order_kind == 'purchase_requisition'
        else 'can_approve_store_requisition'
    )
    notify_users(
        get_users_with_permission(permission),
        f"طلب قطع غيار يحتاج تعميد #{spare_part_request.pk}",
        f"يوجد طلب قطع غيار رقم {spare_part_request.pk} يحتاج تعميدك.",
        'spare_part_needs_approval',
        spare_part_request=spare_part_request,
    )


def notify_spare_part_rejected(spare_part_request):
    engineer = spare_part_request.engineer
    reason = spare_part_request.rejected_reason or spare_part_request.cause or 'غير محدد'
    if engineer and engineer.user_id:
        create_notification(
            engineer.user,
            f"تم رفض طلب قطع الغيار #{spare_part_request.pk}",
            f"تم رفض طلب قطع الغيار رقم {spare_part_request.pk}. السبب: {reason}",
            'spare_part_rejected',
            spare_part_request=spare_part_request,
        )


def notify_spare_part_purchase_ready(spare_part_request):
    notify_users(
        get_users_with_permission('can_issue_from_store'),
        f"طلب قطع غيار بانتظار التوفير #{spare_part_request.pk}",
        f"طلب قطع الغيار رقم {spare_part_request.pk} تم تعميده وينتظر توفير القطع.",
        'spare_part_purchase_ready',
        spare_part_request=spare_part_request,
    )


def notify_spare_part_ready_to_issue(spare_part_request):
    notify_users(
        get_users_with_permission('can_issue_from_store'),
        f"طلب قطع غيار جاهز للصرف #{spare_part_request.pk}",
        f"طلب قطع الغيار رقم {spare_part_request.pk} جاهز للصرف من المخزن.",
        'spare_part_ready_to_issue',
        spare_part_request=spare_part_request,
    )


def notify_spare_part_waiting_confirmation(spare_part_request):
    engineer = spare_part_request.engineer
    if engineer and engineer.user_id:
        create_notification(
            engineer.user,
            f"طلب قطع غيار ينتظر تأكيدك #{spare_part_request.pk}",
            f"طلب قطع الغيار رقم {spare_part_request.pk} ينتظر تأكيد استلامك/صرفك للقطع.",
            'spare_part_waiting_confirmation',
            spare_part_request=spare_part_request,
        )


def notify_spare_part_issued(spare_part_request):
    engineer = spare_part_request.engineer
    if engineer and engineer.user_id:
        create_notification(
            engineer.user,
            f"تم صرف قطع الغيار #{spare_part_request.pk}",
            f"تم تأكيد صرف قطع الغيار لطلب رقم {spare_part_request.pk}.",
            'spare_part_issued',
            spare_part_request=spare_part_request,
        )


def notify_complete_report_needs_approval(report):
    notify_users(
        get_users_with_permission('can_approve_achievement_report'),
        f"تقرير إنجاز يحتاج تعميد #{report.pk}",
        f"يوجد تقرير إنجاز رقم {report.pk} يحتاج تعميدك.",
        'complete_report_needs_approval',
        complete_report=report,
    )


def notify_complete_report_rejected(report):
    engineer = report.maintenance_request.assigned_technician
    reason = report.rejected_reason or report.cause or 'غير محدد'
    if engineer and engineer.user_id:
        create_notification(
            engineer.user,
            f"تم رفض تقرير الإنجاز #{report.pk}",
            f"تم رفض تقرير الإنجاز رقم {report.pk}. السبب: {reason}",
            'complete_report_rejected',
            complete_report=report,
        )


def notify_complete_report_accepted(report):
    maintenance_request = report.maintenance_request
    recipients = []
    if maintenance_request.requester_id:
        recipients.append(maintenance_request.requester)
    if maintenance_request.assigned_technician and maintenance_request.assigned_technician.user_id:
        recipients.append(maintenance_request.assigned_technician.user)
    notify_users(
        {user for user in recipients if user},
        f"تم اعتماد تقرير الإنجاز #{report.pk}",
        f"تم اعتماد تقرير الإنجاز وإكمال طلب الصيانة رقم {maintenance_request.pk}.",
        'maintenance_completed',
        complete_report=report,
        maintenance_request=maintenance_request,
    )
