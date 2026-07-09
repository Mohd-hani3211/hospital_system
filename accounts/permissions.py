import copy

from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db.models import Q
from django.db.utils import DatabaseError, OperationalError, ProgrammingError

from .models import JobTitlePermission, UserPermissionOverride


SYSTEM_SETUP_PERMISSION = 'accounts.can_manage_system_setup'
AUTH_PERMISSION_FIELD_MAP = {
    'can_manage_system_setup': SYSTEM_SETUP_PERMISSION,
    'can_view_dashboard': 'maintenance.can_view_dashboard',
    'can_view_audit_log': 'accounts.can_view_audit_log',
}


def get_permission_field_names():
    return tuple(
        field.name
        for field in JobTitlePermission._meta.fields
        if field.get_internal_type() == 'BooleanField'
    )


def get_permission_field_metadata():
    return [
        {
            'name': field.name,
            'label': field.verbose_name,
        }
        for field in JobTitlePermission._meta.fields
        if field.get_internal_type() == 'BooleanField'
    ]


def get_user_role_permissions(user):
    if not user or not user.is_authenticated:
        return None
    try:
        return user.profile.job_title.permissions
    except (AttributeError, ObjectDoesNotExist):
        return None


def get_user_permission_override_sets(user):
    if not user or not user.is_authenticated:
        return set(), set()
    allowed_permissions = set(get_permission_field_names())
    grants = set()
    denies = set()
    try:
        overrides = UserPermissionOverride.objects.filter(user=user).values_list('permission_name', 'action')
    except (DatabaseError, OperationalError, ProgrammingError):
        return grants, denies
    for permission_name, action in overrides:
        if permission_name not in allowed_permissions:
            continue
        if action == UserPermissionOverride.ACTION_GRANT:
            grants.add(permission_name)
        elif action == UserPermissionOverride.ACTION_DENY:
            denies.add(permission_name)
    return grants, denies


def user_permission_is_denied(user, permission_name):
    _, denied_permissions = get_user_permission_override_sets(user)
    return permission_name in denied_permissions


def user_permission_override_matches(user, permission_name, action):
    if not user or not user.is_authenticated:
        return False
    try:
        return UserPermissionOverride.objects.filter(
            user=user,
            permission_name=permission_name,
            action=action,
        ).exists()
    except (DatabaseError, OperationalError, ProgrammingError):
        return False


def get_effective_permissions(user):
    role_permissions = get_user_role_permissions(user)
    permissions = copy.copy(role_permissions) if role_permissions else JobTitlePermission()
    permissions._effective_user = user
    allowed_permissions = set(get_permission_field_names())

    granted_permissions, denied_permissions = get_user_permission_override_sets(user)
    if user and user.is_authenticated:
        for field_name, auth_permission in AUTH_PERMISSION_FIELD_MAP.items():
            if field_name not in denied_permissions and user.has_perm(auth_permission):
                setattr(permissions, field_name, True)

    for permission_name in granted_permissions:
        if permission_name in allowed_permissions:
            setattr(permissions, permission_name, True)
    for permission_name in denied_permissions:
        if permission_name in allowed_permissions:
            setattr(permissions, permission_name, False)
    return permissions


def user_can_manage_system_setup(user):
    if not user or not user.is_authenticated:
        return False
    return bool(get_effective_permissions(user).can_manage_system_setup)


def user_has_role_permission(user, role_field, auth_permission=None):
    if not user or not user.is_authenticated:
        return False
    permissions = get_effective_permissions(user)
    if permissions.can_manage_system_setup:
        return True
    if permissions and getattr(permissions, role_field, False):
        return True
    return bool(
        auth_permission
        and not user_permission_is_denied(user, role_field)
        and user.has_perm(auth_permission)
    )


def user_has_any_role_permission(user, *role_fields):
    return any(user_has_role_permission(user, role_field) for role_field in role_fields)


def get_system_setup_permissions():
    return {
        'permission': SYSTEM_SETUP_PERMISSION,
        'role_field': 'can_manage_system_setup',
        'display_name': 'Superuser / مسؤول تهيئة النظام',
    }


def get_system_setup_admin_users(exclude_user=None, exclude_job_title=None):
    queryset = User.objects.filter(is_active=True).filter(
        Q(profile__job_title__permissions__can_manage_system_setup=True)
        | Q(permission_overrides__permission_name='can_manage_system_setup', permission_overrides__action=UserPermissionOverride.ACTION_GRANT)
        | Q(user_permissions__codename='can_manage_system_setup')
        | Q(groups__permissions__codename='can_manage_system_setup')
        | Q(is_superuser=True)
    ).select_related('profile', 'profile__job_title', 'profile__job_title__permissions').prefetch_related('permission_overrides')
    if exclude_user is not None:
        queryset = queryset.exclude(pk=exclude_user.pk)
    if exclude_job_title is not None:
        queryset = queryset.exclude(profile__job_title=exclude_job_title)
    return [
        candidate
        for candidate in queryset.distinct()
        if user_can_manage_system_setup(candidate)
    ]


def ensure_at_least_one_system_admin_exists(exclude_user=None, exclude_job_title=None):
    if not get_system_setup_admin_users(exclude_user=exclude_user, exclude_job_title=exclude_job_title):
        raise PermissionDenied("لا يمكن إزالة آخر مسؤول تهيئة للنظام.")


def require_system_setup_permission(request):
    if not user_can_manage_system_setup(request.user):
        raise PermissionDenied("ليس لديك صلاحية إدارة تهيئة النظام.")


def require_role_permission(request, role_field, message=None, auth_permission=None):
    if not user_has_role_permission(request.user, role_field, auth_permission=auth_permission):
        raise PermissionDenied(message or "ليس لديك صلاحية الوصول إلى هذه الصفحة.")


def require_any_role_permission(request, *role_fields, message=None):
    if not user_has_any_role_permission(request.user, *role_fields):
        raise PermissionDenied(message or "ليس لديك صلاحية الوصول إلى هذه الصفحة.")
