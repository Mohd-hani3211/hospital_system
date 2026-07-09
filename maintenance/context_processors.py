from .views import get_pending_maintenance_actions_count, get_pending_spare_part_actions_count
from .models import Notification
from accounts.permissions import user_can_manage_system_setup


def maintenance_sidebar_counts(request):
    if not request.user.is_authenticated:
        return {
            'maintenance_action_required_count': 0,
            'spare_part_action_required_count': 0,
            'unread_notifications_count': 0,
            'latest_unread_notifications': [],
            'can_manage_system_setup': False,
        }
    notifications = Notification.objects.filter(
        recipient=request.user,
        is_read=False,
    ).select_related(
        'related_maintenance_request',
        'related_spare_part_request',
        'related_complete_report',
    ).order_by('-created_at')
    return {
        'maintenance_action_required_count': get_pending_maintenance_actions_count(request.user),
        'spare_part_action_required_count': get_pending_spare_part_actions_count(request.user),
        'unread_notifications_count': notifications.count(),
        'latest_unread_notifications': notifications[:5],
        'can_manage_system_setup': user_can_manage_system_setup(request.user),
    }
