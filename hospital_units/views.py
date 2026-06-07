
from django.shortcuts import render, redirect
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from .models import Building, Department, Floor
from .forms import BuildingForm, DepartmentForm, DepartmentForm, FloorForm
from accounts.audit_log_service import create_audit_log, serialize_instance
from accounts.models import AuditLog
from accounts.permissions import user_can_manage_system_setup


# Create your views here.

class SystemSetupRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    login_url = 'login'

    def test_func(self):
        return user_can_manage_system_setup(self.request.user)

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            return super().handle_no_permission()
        from django.core.exceptions import PermissionDenied
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
    model = Building
    template_name = 'buildings.html'
    context_object_name = 'buildings'

class AddBuilding(AuditSetupChangeMixin, SystemSetupRequiredMixin, CreateView):
    model = Building
    form_class = BuildingForm
    template_name = 'add.html'
    success_url = '/hospital_units/buildings'

class EditBuilding(AuditSetupChangeMixin, SystemSetupRequiredMixin, UpdateView):
    model = Building
    form_class = BuildingForm
    template_name = 'edit.html'
    success_url = '/hospital_units/buildings'

class DeleteBuilding(AuditSetupDeleteMixin, SystemSetupRequiredMixin, DeleteView):
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
    model = Floor
    template_name = 'floors.html'
    context_object_name = 'floors'

class AddFloor(AuditSetupChangeMixin, SystemSetupRequiredMixin, CreateView):
    model = Floor
    form_class = FloorForm
    template_name = 'add.html'
    success_url = '/hospital_units/floors'


class EditFloor(AuditSetupChangeMixin, SystemSetupRequiredMixin, UpdateView):
    model = Floor
    form_class = FloorForm
    template_name = 'edit.html'
    success_url = '/hospital_units/floors'


class DeleteFloor(AuditSetupDeleteMixin, SystemSetupRequiredMixin, DeleteView):
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
    model = Department
    template_name = 'departments.html'
    context_object_name = 'departments'

class AddDepartment(AuditSetupChangeMixin, SystemSetupRequiredMixin, CreateView):
    model = Department
    form_class = DepartmentForm
    template_name = 'add.html'
    success_url = '/hospital_units/departments/'


class EditDepartment(AuditSetupChangeMixin, SystemSetupRequiredMixin, UpdateView):
    model = Department
    form_class = DepartmentForm
    template_name = 'edit.html'
    success_url = '/hospital_units/departments/'


class DeleteDepartment(AuditSetupDeleteMixin, SystemSetupRequiredMixin, DeleteView):
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
