from django.db.models import Q

from accounts.models import JobTitlePermission
from accounts.permissions import get_effective_permissions
from .models import MaintenanceRequest, SparePartRequest, complete_report as CompleteReport


FINAL_MAINTENANCE_STATUSES = ('completed', 'rejected')
FINAL_SPARE_PART_STATUSES = ('avaliable_parts_issued', 'rejected')


def get_profile(user):
    return getattr(user, 'profile', None) if user and user.is_authenticated else None


def get_permissions(user):
    if not user or not user.is_authenticated:
        return JobTitlePermission()
    return get_effective_permissions(user)


def user_has_all_departments_access(user):
    if not user or not user.is_authenticated:
        return False
    permissions = get_permissions(user)
    return bool(permissions.can_view_all_departments)


def user_can_view_department(user, department):
    profile = get_profile(user)
    if not profile or not department:
        return False
    if user_has_all_departments_access(user):
        return True
    return bool(profile.managing_department_id and profile.managing_department_id == department.pk)


def user_can_view_maintenance_request(user, maintenance_request):
    profile = get_profile(user)
    if not profile or not maintenance_request:
        return False
    if user_has_all_departments_access(user):
        return True
    if user_can_view_department(user, maintenance_request.department):
        return True
    return bool(maintenance_request.assigned_technician_id and maintenance_request.assigned_technician_id == profile.pk)


def user_can_view_spare_part_request(user, spare_part_request):
    profile = get_profile(user)
    if not profile or not spare_part_request:
        return False
    if user_has_all_departments_access(user):
        return True
    maintenance_request = spare_part_request.maintenance_request
    if user_can_view_department(user, maintenance_request.department):
        return True
    if maintenance_request.assigned_technician_id and maintenance_request.assigned_technician_id == profile.pk:
        return True
    return False


def user_can_view_complete_report(user, complete_report):
    if not complete_report:
        return False
    return user_can_view_maintenance_request(user, complete_report.maintenance_request)


def maintenance_access_filter(user):
    profile = get_profile(user)
    if not profile:
        return Q(pk__in=[])
    if user_has_all_departments_access(user):
        return Q()

    query = Q()
    if profile.managing_department_id:
        query |= Q(department_id=profile.managing_department_id)
    query |= Q(assigned_technician=profile)
    return query


def spare_part_access_filter(user):
    profile = get_profile(user)
    if not profile:
        return Q(pk__in=[])
    if user_has_all_departments_access(user):
        return Q()

    query = Q()
    if profile.managing_department_id:
        query |= Q(maintenance_request__department_id=profile.managing_department_id)
    query |= Q(maintenance_request__assigned_technician=profile)
    return query


def complete_report_access_filter(user):
    profile = get_profile(user)
    if not profile:
        return Q(pk__in=[])
    if user_has_all_departments_access(user):
        return Q()

    query = Q()
    if profile.managing_department_id:
        query |= Q(maintenance_request__department_id=profile.managing_department_id)
    query |= Q(maintenance_request__assigned_technician=profile)
    return query


def get_visible_maintenance_requests(user, queryset=None):
    queryset = queryset if queryset is not None else MaintenanceRequest.objects.all()
    return queryset.filter(maintenance_access_filter(user)).distinct()


def get_visible_spare_part_requests(user, queryset=None):
    queryset = queryset if queryset is not None else SparePartRequest.objects.all()
    return queryset.filter(spare_part_access_filter(user)).distinct()


def get_visible_complete_reports(user, queryset=None):
    queryset = queryset if queryset is not None else CompleteReport.objects.all()
    return queryset.filter(complete_report_access_filter(user)).distinct()


def get_active_maintenance_requests(user, queryset=None):
    return get_visible_maintenance_requests(user, queryset).exclude(status__in=FINAL_MAINTENANCE_STATUSES)


def get_final_maintenance_requests(user, queryset=None):
    return get_visible_maintenance_requests(user, queryset).filter(status__in=FINAL_MAINTENANCE_STATUSES)


def get_active_spare_part_requests(user, queryset=None):
    return get_visible_spare_part_requests(user, queryset).exclude(status__in=FINAL_SPARE_PART_STATUSES)


def get_final_spare_part_requests(user, queryset=None):
    return get_visible_spare_part_requests(user, queryset).filter(status__in=FINAL_SPARE_PART_STATUSES)


def user_can_modify_maintenance_request(user, maintenance_request, permission_name=None):
    if not user_can_view_maintenance_request(user, maintenance_request):
        return False
    if not permission_name:
        return True
    return bool(getattr(get_permissions(user), permission_name, False))


def user_can_modify_spare_part_request(user, spare_part_request, permission_name=None):
    if not user_can_view_spare_part_request(user, spare_part_request):
        return False
    if not permission_name:
        return True
    return bool(getattr(get_permissions(user), permission_name, False))


def user_can_modify_complete_report(user, complete_report, permission_name=None):
    if not user_can_view_complete_report(user, complete_report):
        return False
    if not permission_name:
        return True
    return bool(getattr(get_permissions(user), permission_name, False))
