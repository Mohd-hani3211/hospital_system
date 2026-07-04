
from django.shortcuts import render, redirect
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from .models import Building, Department, Floor
from .forms import BuildingForm, DepartmentForm, DepartmentForm, FloorForm
from accounts.audit_log_service import create_audit_log, serialize_instance
from accounts.models import AuditLog, Profile
from accounts.permissions import user_has_any_role_permission, user_has_role_permission


# Create your views here.

def _get_filter_value(params, key):
    return (params.get(key) or '').strip()


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


NAME_ORDERING_FILTER_LABELS = {
    'name': 'الاسم تصاعدياً',
    '-name': 'الاسم تنازلياً',
    'newest': 'الأحدث أولاً',
    'oldest': 'الأقدم أولاً',
}

FLOOR_ORDERING_FILTER_LABELS = {
    **NAME_ORDERING_FILTER_LABELS,
    'building': 'حسب المبنى',
}

DEPARTMENT_ORDERING_FILTER_LABELS = {
    **NAME_ORDERING_FILTER_LABELS,
    'building': 'حسب المبنى',
    'floor': 'حسب الطابق',
    'manager': 'حسب رئيس القسم',
}


class SystemSetupRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    login_url = 'login'
    required_role_permissions = ('can_view_organization_structure',)

    def test_func(self):
        return user_has_any_role_permission(self.request.user, *self.required_role_permissions)

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        raise PermissionDenied("ليس لديك صلاحية إدارة تهيئة النظام.")


class AuditSetupChangeMixin:
    def form_valid(self, form):
        old_data = None
        if getattr(self, 'object', None) and self.object:
            old_data = serialize_instance(self.object)
        response = super().form_valid(form)
        action_type = AuditLog.ACTION_UPDATE if old_data else AuditLog.ACTION_CREATE
        create_audit_log(
            self.request.user,
            action_type,
            self.object,
            old_data=old_data,
            new_data=serialize_instance(self.object),
            description=f"تم {'تعديل' if old_data else 'إنشاء'} {self.object._meta.verbose_name}: {self.object}",
            request=self.request,
        )
        return response


class AuditSetupDeleteMixin:
    def audit_delete(self, request, obj):
        create_audit_log(
            request.user,
            AuditLog.ACTION_DELETE,
            obj,
            old_data=serialize_instance(obj),
            description=f"تم حذف {obj._meta.verbose_name}: {obj}",
            request=request,
        )


class BuildingsList(SystemSetupRequiredMixin, ListView):
    required_role_permissions = ('can_view_organization_structure', 'can_manage_buildings')
    model = Building
    template_name = 'buildings.html'
    context_object_name = 'buildings'

    def get_queryset(self):
        queryset = Building.objects.all()
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
        context['can_manage_buildings'] = user_has_role_permission(self.request.user, 'can_manage_buildings')
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
        return context

class AddBuilding(AuditSetupChangeMixin, SystemSetupRequiredMixin, CreateView):
    required_role_permissions = ('can_manage_buildings',)
    model = Building
    form_class = BuildingForm
    template_name = 'add.html'
    success_url = '/hospital_units/buildings'

class EditBuilding(AuditSetupChangeMixin, SystemSetupRequiredMixin, UpdateView):
    required_role_permissions = ('can_manage_buildings',)
    model = Building
    form_class = BuildingForm
    template_name = 'edit.html'
    success_url = '/hospital_units/buildings'

class DeleteBuilding(AuditSetupDeleteMixin, SystemSetupRequiredMixin, DeleteView):
    required_role_permissions = ('can_manage_buildings',)
    model = Building
    success_url = '/hospital_units/buildings'


    def post(self, request, *args, **kwargs):
        building = self.get_object()
        if building.floors.exists():
            print(f'Building {building.name} has {building.floors.count()} floors and cannot be deleted.')
            messages.error(request, "لا يمكن حذف هذا المبنى لأنه مرتبط بأقسام في المستشفى.")
            return redirect('buildings')
        self.audit_delete(request, building)
        return super().post(request, *args, **kwargs)
    


class FloorsList(SystemSetupRequiredMixin, ListView):
    required_role_permissions = ('can_view_organization_structure', 'can_manage_floors')
    model = Floor
    template_name = 'floors.html'
    context_object_name = 'floors'

    def get_queryset(self):
        queryset = Floor.objects.select_related('building')
        query = _get_filter_value(self.request.GET, 'q')
        if query:
            queryset = queryset.filter(Q(name__icontains=query) | Q(description__icontains=query))

        building = _get_filter_value(self.request.GET, 'building')
        if building.isdigit():
            queryset = queryset.filter(building_id=int(building))

        ordering = _get_filter_value(self.request.GET, 'ordering')
        ordering_map = {
            'name': 'name',
            '-name': '-name',
            'building': 'building__name',
            'newest': '-id',
            'oldest': 'id',
        }
        return queryset.order_by(ordering_map.get(ordering, 'building__name'), 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        building_options = list(Building.objects.order_by('name'))
        context['can_manage_floors'] = user_has_role_permission(self.request.user, 'can_manage_floors')
        context['filters'] = {
            'q': _get_filter_value(self.request.GET, 'q'),
            'building': _get_filter_value(self.request.GET, 'building'),
            'ordering': _get_filter_value(self.request.GET, 'ordering'),
        }
        context['has_active_filters'] = _has_admin_filters(self.request.GET)
        context['filter_query_string'] = _query_string_without_page(self.request.GET)
        applied_filters = []
        _append_applied_filter(applied_filters, self.request, 'q', 'بحث', context['filters']['q'])
        _append_applied_filter(
            applied_filters,
            self.request,
            'building',
            'المبنى',
            _label_from_queryset(building_options, context['filters']['building'], attr_name='name'),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'ordering',
            'الترتيب',
            FLOOR_ORDERING_FILTER_LABELS.get(context['filters']['ordering'], context['filters']['ordering']),
        )
        context['applied_filters'] = applied_filters
        context['applied_filters_count'] = len(applied_filters)
        context['building_options'] = building_options
        return context

class AddFloor(AuditSetupChangeMixin, SystemSetupRequiredMixin, CreateView):
    required_role_permissions = ('can_manage_floors',)
    model = Floor
    form_class = FloorForm
    template_name = 'add.html'
    success_url = '/hospital_units/floors'


class EditFloor(AuditSetupChangeMixin, SystemSetupRequiredMixin, UpdateView):
    required_role_permissions = ('can_manage_floors',)
    model = Floor
    form_class = FloorForm
    template_name = 'edit.html'
    success_url = '/hospital_units/floors'


class DeleteFloor(AuditSetupDeleteMixin, SystemSetupRequiredMixin, DeleteView):
    required_role_permissions = ('can_manage_floors',)
    model = Floor
    success_url = '/hospital_units/floors/'

    def post(self, request, *args, **kwargs):
        floor = self.get_object()
        if floor.departments.exists():
            print(f'Floor {floor.name} has {floor.departments.count()} departments and cannot be deleted.')
            messages.error(request, "لا يمكن حذف هذا الطابق لأنه مرتبط بأقسام في المستشفى.")
            return redirect('floors')
        self.audit_delete(request, floor)
        return super().post(request, *args, **kwargs)       
    


class DepartmentsList(SystemSetupRequiredMixin, ListView):
    required_role_permissions = ('can_view_organization_structure', 'can_manage_departments')
    model = Department
    template_name = 'departments.html'
    context_object_name = 'departments'

    def get_queryset(self):
        queryset = Department.objects.select_related(
            'floor',
            'floor__building',
            'managed_by',
            'managed_by__user',
        )
        query = _get_filter_value(self.request.GET, 'q')
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(floor__name__icontains=query)
                | Q(floor__building__name__icontains=query)
            )

        building = _get_filter_value(self.request.GET, 'building')
        if building.isdigit():
            queryset = queryset.filter(floor__building_id=int(building))

        floor = _get_filter_value(self.request.GET, 'floor')
        if floor.isdigit():
            queryset = queryset.filter(floor_id=int(floor))

        manager = _get_filter_value(self.request.GET, 'manager')
        if manager.isdigit():
            queryset = queryset.filter(managed_by__id=int(manager))

        ordering = _get_filter_value(self.request.GET, 'ordering')
        ordering_map = {
            'name': 'name',
            '-name': '-name',
            'building': 'floor__building__name',
            'floor': 'floor__name',
            'manager': 'managed_by__user__first_name',
            'newest': '-id',
            'oldest': 'id',
        }
        return queryset.distinct().order_by(ordering_map.get(ordering, 'name'), 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        building_options = list(Building.objects.order_by('name'))
        floor_options = list(Floor.objects.select_related('building').order_by('building__name', 'name'))
        manager_options = list(Profile.objects.filter(
            managing_department__isnull=False,
        ).select_related('user', 'managing_department').order_by(
            'user__first_name',
            'user__last_name',
            'user__username',
        ))
        context['can_manage_departments'] = user_has_role_permission(self.request.user, 'can_manage_departments')
        context['filters'] = {
            'q': _get_filter_value(self.request.GET, 'q'),
            'building': _get_filter_value(self.request.GET, 'building'),
            'floor': _get_filter_value(self.request.GET, 'floor'),
            'manager': _get_filter_value(self.request.GET, 'manager'),
            'ordering': _get_filter_value(self.request.GET, 'ordering'),
        }
        context['has_active_filters'] = _has_admin_filters(self.request.GET)
        context['filter_query_string'] = _query_string_without_page(self.request.GET)
        applied_filters = []
        _append_applied_filter(applied_filters, self.request, 'q', 'بحث', context['filters']['q'])
        _append_applied_filter(
            applied_filters,
            self.request,
            'building',
            'المبنى',
            _label_from_queryset(building_options, context['filters']['building'], attr_name='name'),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'floor',
            'الطابق',
            _label_from_queryset(floor_options, context['filters']['floor']),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'manager',
            'رئيس القسم',
            _label_from_queryset(manager_options, context['filters']['manager']),
        )
        _append_applied_filter(
            applied_filters,
            self.request,
            'ordering',
            'الترتيب',
            DEPARTMENT_ORDERING_FILTER_LABELS.get(context['filters']['ordering'], context['filters']['ordering']),
        )
        context['applied_filters'] = applied_filters
        context['applied_filters_count'] = len(applied_filters)
        context['building_options'] = building_options
        context['floor_options'] = floor_options
        context['manager_options'] = manager_options
        return context

class AddDepartment(AuditSetupChangeMixin, SystemSetupRequiredMixin, CreateView):
    required_role_permissions = ('can_manage_departments',)
    model = Department
    form_class = DepartmentForm
    template_name = 'add.html'
    success_url = '/hospital_units/departments/'


class EditDepartment(AuditSetupChangeMixin, SystemSetupRequiredMixin, UpdateView):
    required_role_permissions = ('can_manage_departments',)
    model = Department
    form_class = DepartmentForm
    template_name = 'edit.html'
    success_url = '/hospital_units/departments/'


class DeleteDepartment(AuditSetupDeleteMixin, SystemSetupRequiredMixin, DeleteView):
    required_role_permissions = ('can_manage_departments',)
    model = Department
    success_url = '/hospital_units/departments/'

    def post(self, request, *args, **kwargs):
        department = self.get_object()
        if department.managed_by.exists():
            print(f'Department {department.name} has {department.managed_by.count()} profiles and cannot be deleted.')
            messages.error(request, "لا يمكن حذف هذا القسم لأنه مرتبط بملفات تعريف المستخدمين.")
            return redirect('departments')
        self.audit_delete(request, department)
        return super().post(request, *args, **kwargs)
