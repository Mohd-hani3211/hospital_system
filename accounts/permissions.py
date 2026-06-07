from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import Q


SYSTEM_SETUP_PERMISSION = 'accounts.can_manage_system_setup'


def user_can_manage_system_setup(user):
    if not user or not user.is_authenticated:
        return False
    role_permission = False
    try:
        role_permission = bool(user.profile.job_title.permissions.can_manage_system_setup)
    except AttributeError:
        role_permission = False
    return role_permission or user.has_perm(SYSTEM_SETUP_PERMISSION)

 
def get_system_setup_permissions():
    return {
        'permission': SYSTEM_SETUP_PERMISSION,
        'role_field': 'can_manage_system_setup',
        'display_name': 'Superuser / مسؤول تهيئة النظام',
    }


def get_system_setup_admin_users(exclude_user=None, exclude_job_title=None):
    queryset = User.objects.filter(is_active=True).filter(
        Q(profile__job_title__permissions__can_manage_system_setup=True)
        | Q(user_permissions__codename='can_manage_system_setup')
        | Q(groups__permissions__codename='can_manage_system_setup')
        | Q(is_superuser=True)
    )
    if exclude_user is not None:
        queryset = queryset.exclude(pk=exclude_user.pk)
    if exclude_job_title is not None:
        queryset = queryset.exclude(profile__job_title=exclude_job_title)
    return queryset.distinct()


def ensure_at_least_one_system_admin_exists(exclude_user=None, exclude_job_title=None):
    if not get_system_setup_admin_users(exclude_user=exclude_user, exclude_job_title=exclude_job_title).exists():
        raise PermissionDenied("لا يمكن إزالة آخر مسؤول تهيئة للنظام.")


def require_system_setup_permission(request):
    if not user_can_manage_system_setup(request.user):
        raise PermissionDenied("ليس لديك صلاحية إدارة تهيئة النظام.")
