from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.forms import JobTitlePermissionForm
from accounts.models import (
    ApprovalDelegation,
    ApprovalLevel,
    ApprovalWorkflow,
    JobTitle,
    JobTitlePermission,
    Profile,
    Specialty,
)
from hospital_units.models import Building, Department, Floor
from maintenance.models import MaintenanceRequest, SparePartApproval, SparePartRequest
from maintenance.views import can_user_approve_spare_part_request


class ApprovalHierarchySettingsTests(TestCase):
    def setUp(self):
        self.admin_job = JobTitle.objects.create(title_name='System Manager')
        JobTitlePermission.objects.create(job_title=self.admin_job, can_manage_system_setup=True)
        self.admin_user = User.objects.create_user(username='admin', password='password')
        Profile.objects.create(
            user=self.admin_user,
            job_title=self.admin_job,
            phone_number='700000010',
            employee_id='ADMIN-1',
        )
        self.client.force_login(self.admin_user)

    def create_job_permission(self, title, **permission_flags):
        job_title = JobTitle.objects.create(title_name=title)
        permission = JobTitlePermission.objects.create(job_title=job_title, **permission_flags)
        return job_title, permission

    def create_spare_request(self):
        building = Building.objects.create(name='Main')
        floor = Floor.objects.create(building=building, name='Ground')
        department = Department.objects.create(floor=floor, name='Maintenance')
        specialty = Specialty.objects.create(name='Electrical')
        requester = User.objects.create_user(username='requester')
        engineer_user = User.objects.create_user(username='engineer')
        engineer_job = JobTitle.objects.create(title_name='Engineer')
        engineer_profile = Profile.objects.create(
            user=engineer_user,
            job_title=engineer_job,
            specialty=specialty,
            phone_number='700000011',
            employee_id='ENG-11',
        )
        maintenance_request = MaintenanceRequest.objects.create(
            description='Broken pump',
            requester=requester,
            department=department,
            required_specialty=specialty,
            assigned_technician=engineer_profile,
            status='awaiting_parts',
        )
        return SparePartRequest.objects.create(
            maintenance_request=maintenance_request,
            engineer=engineer_profile,
            order_kind='purchase_requisition',
            description='Need parts',
            status='awaiting_for_accepenace',
        )

    def test_role_permission_order_dropdown_receives_configured_levels(self):
        level_one_job, level_one_permission = self.create_job_permission(
            'رئيس قسم الصيانة',
            can_approve_purchase_order=True,
        )
        level_two_job, _ = self.create_job_permission(
            'مدير إدارة الصيانة',
            can_approve_purchase_order=True,
        )
        ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=level_one_job,
        )
        ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=2,
            job_title=level_two_job,
        )

        form = JobTitlePermissionForm(instance=level_one_permission, job_title=level_one_job)
        choices = list(form.fields['purchase_approval_order'].choices)

        self.assertIn((1, 'المستوى 1 - المسمى الحالي'), choices)
        self.assertIn((2, 'المستوى 2 - مدير إدارة الصيانة'), choices)
        self.assertEqual(form.fields['purchase_approval_order'].initial, 1)

    def test_delegation_checkboxes_display_configured_levels(self):
        level_one_job, _ = self.create_job_permission('رئيس القسم', can_approve_purchase_order=True)
        level_two_job, level_two_permission = self.create_job_permission('المدير العام', can_approve_purchase_order=True)
        level_one = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=level_one_job,
        )
        ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=2,
            job_title=level_two_job,
        )
        ApprovalDelegation.objects.create(source_job_title=level_two_job, approval_level=level_one)

        form = JobTitlePermissionForm(instance=level_two_permission, job_title=level_two_job)
        choices = list(form.fields['purchase_delegation_levels'].choices)

        self.assertIn((str(level_one.pk), 'المستوى 1 - رئيس القسم'), choices)
        self.assertEqual(form.fields['purchase_delegation_levels'].initial, [str(level_one.pk)])

    def test_settings_page_can_add_level(self):
        job_title, _ = self.create_job_permission('مدير الشراء')

        response = self.client.post(reverse('approval_hierarchy_settings'), {
            'workflow_action': f'add_level|{ApprovalWorkflow.PURCHASE}',
            f'new_job_title_{ApprovalWorkflow.PURCHASE}': str(job_title.pk),
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(ApprovalLevel.objects.filter(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=job_title,
        ).exists())

    def test_settings_page_can_remove_last_safe_level(self):
        job_title, _ = self.create_job_permission('مدير الشراء')
        ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=job_title,
        )

        response = self.client.post(reverse('approval_hierarchy_settings'), {
            'workflow_action': f'remove_last_level|{ApprovalWorkflow.PURCHASE}',
        })

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ApprovalLevel.objects.filter(workflow_type=ApprovalWorkflow.PURCHASE).exists())

    def test_settings_page_blocks_removing_used_level(self):
        job_title, _ = self.create_job_permission('مدير الشراء')
        approval_level = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=job_title,
        )
        spare_request = self.create_spare_request()
        SparePartApproval.objects.create(
            request=spare_request,
            approver=self.admin_user,
            approval_level=approval_level,
            decision='accepted',
        )

        response = self.client.post(reverse('approval_hierarchy_settings'), {
            'workflow_action': f'remove_last_level|{ApprovalWorkflow.PURCHASE}',
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(ApprovalLevel.objects.filter(pk=approval_level.pk).exists())

    def test_runtime_approval_works_after_adding_level(self):
        first_job, first_permission = self.create_job_permission('رئيس القسم', can_approve_purchase_order=True)
        second_job, second_permission = self.create_job_permission('مدير الشراء', can_approve_purchase_order=True)
        ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=first_job,
        )
        self.client.post(reverse('approval_hierarchy_settings'), {
            'workflow_action': f'add_level|{ApprovalWorkflow.PURCHASE}',
            f'new_job_title_{ApprovalWorkflow.PURCHASE}': str(second_job.pk),
        })

        spare_request = self.create_spare_request()
        self.assertTrue(can_user_approve_spare_part_request(spare_request, first_permission))

        spare_request.acceptance = 1
        spare_request.save(update_fields=['acceptance'])

        second_permission.refresh_from_db()
        self.assertTrue(can_user_approve_spare_part_request(spare_request, second_permission))
