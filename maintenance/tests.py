from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

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
from maintenance.models import (
    CompleteReportApproval,
    MaintenanceRequest,
    Notification,
    SparePartApproval,
    SparePartRequest,
    complete_report as CompleteReport,
)
from maintenance.notifications import notify_delegated_spare_part_approval
from maintenance.views import (
    approval_record_metadata,
    build_spare_part_card_summary,
    build_maintenance_approval_tracking_sections,
    can_user_approve_complete_report,
    can_user_approve_spare_part_request,
    get_next_approval_level,
    is_workflow_complete,
    resolve_approval_level_authorization,
)


class DynamicApprovalHierarchyTests(TestCase):
    def setUp(self):
        self.building = Building.objects.create(name='Main')
        self.floor = Floor.objects.create(building=self.building, name='Ground')
        self.department = Department.objects.create(floor=self.floor, name='ICU')
        self.specialty = Specialty.objects.create(name='Electrical')
        self.requester = User.objects.create_user(username='requester')
        self.engineer_user = User.objects.create_user(username='engineer')
        self.engineer_job = JobTitle.objects.create(title_name='Engineer')
        self.engineer_profile = Profile.objects.create(
            user=self.engineer_user,
            job_title=self.engineer_job,
            specialty=self.specialty,
            phone_number='700000001',
            employee_id='ENG-1',
        )
        self.maintenance_request = MaintenanceRequest.objects.create(
            description='Broken pump',
            requester=self.requester,
            department=self.department,
            required_specialty=self.specialty,
            assigned_technician=self.engineer_profile,
            status='awaiting_parts',
        )
        self.profile_counter = 100

    def make_permission(self, title, **permissions):
        job_title = JobTitle.objects.create(title_name=title)
        permission = JobTitlePermission.objects.create(job_title=job_title, **permissions)
        return job_title, permission

    def make_user_for_job(self, username, job_title):
        self.profile_counter += 1
        user = User.objects.create_user(username=username)
        Profile.objects.create(
            user=user,
            job_title=job_title,
            phone_number=f"700000{self.profile_counter}",
            employee_id=f"EMP-{self.profile_counter}",
        )
        return user

    def make_spare_request(self, order_kind='purchase_requisition', acceptance=0):
        return SparePartRequest.objects.create(
            maintenance_request=self.maintenance_request,
            engineer=self.engineer_profile,
            order_kind=order_kind,
            description='Need spare parts',
            status='awaiting_for_accepenace',
            acceptance=acceptance,
        )

    def test_normal_purchase_approver_can_approve_only_current_level(self):
        level_one_job, level_one_permission = self.make_permission(
            'Department Manager',
            can_approve_purchase_order=True,
        )
        level_two_job, level_two_permission = self.make_permission(
            'Finance Manager',
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
        spare_request = self.make_spare_request()

        self.assertTrue(can_user_approve_spare_part_request(spare_request, level_one_permission))
        self.assertFalse(can_user_approve_spare_part_request(spare_request, level_two_permission))

        spare_request.acceptance = 1
        spare_request.save(update_fields=['acceptance'])

        self.assertFalse(can_user_approve_spare_part_request(spare_request, level_one_permission))
        self.assertTrue(can_user_approve_spare_part_request(spare_request, level_two_permission))

    def test_delegation_is_exact_not_range_based(self):
        level_one_job, _ = self.make_permission('Department Manager', can_approve_purchase_order=True)
        level_two_job, _ = self.make_permission('Technical Manager', can_approve_purchase_order=True)
        level_three_job, _ = self.make_permission('Finance Manager', can_approve_purchase_order=True)
        delegate_job, delegate_permission = self.make_permission('General Manager', can_approve_purchase_order=True)

        level_one = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=level_one_job,
        )
        level_two = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=2,
            job_title=level_two_job,
        )
        ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=3,
            job_title=level_three_job,
        )
        ApprovalDelegation.objects.create(source_job_title=delegate_job, approval_level=level_one)

        spare_request = self.make_spare_request()
        self.assertTrue(can_user_approve_spare_part_request(spare_request, delegate_permission))

        spare_request.acceptance = 1
        spare_request.save(update_fields=['acceptance'])
        self.assertFalse(can_user_approve_spare_part_request(spare_request, delegate_permission))

        ApprovalDelegation.objects.create(source_job_title=delegate_job, approval_level=level_two)
        self.assertTrue(can_user_approve_spare_part_request(spare_request, delegate_permission))

        spare_request.acceptance = 2
        spare_request.save(update_fields=['acceptance'])
        self.assertFalse(can_user_approve_spare_part_request(spare_request, delegate_permission))

    def test_authorization_mode_is_normal_for_required_job_title(self):
        required_job, required_permission = self.make_permission(
            'Required Approver',
            can_approve_purchase_order=True,
        )
        level = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=required_job,
        )

        authorization = resolve_approval_level_authorization(required_permission, level)

        self.assertTrue(authorization['allowed'])
        self.assertEqual(authorization['approval_mode'], 'normal')
        self.assertEqual(authorization['required_job_title'], required_job)
        self.assertIsNone(authorization['delegated_for_job_title'])

    def test_authorization_mode_is_delegated_only_for_exact_level(self):
        required_job, _ = self.make_permission('Required Level 1', can_approve_purchase_order=True)
        next_required_job, _ = self.make_permission('Required Level 2', can_approve_purchase_order=True)
        delegate_job, delegate_permission = self.make_permission('Delegate', can_approve_purchase_order=True)
        level_one = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=required_job,
        )
        level_two = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=2,
            job_title=next_required_job,
        )
        ApprovalDelegation.objects.create(source_job_title=delegate_job, approval_level=level_one)

        delegated_authorization = resolve_approval_level_authorization(delegate_permission, level_one)
        non_delegated_authorization = resolve_approval_level_authorization(delegate_permission, level_two)

        self.assertTrue(delegated_authorization['allowed'])
        self.assertEqual(delegated_authorization['approval_mode'], 'delegated')
        self.assertEqual(delegated_authorization['delegated_for_job_title'], required_job)
        self.assertFalse(non_delegated_authorization['allowed'])

    def test_requested_approval_mode_must_match_allowed_mode(self):
        required_job, required_permission = self.make_permission(
            'Required Direct Manager',
            can_approve_purchase_order=True,
        )
        delegate_job, delegate_permission = self.make_permission('Exact Delegate', can_approve_purchase_order=True)
        level = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=required_job,
        )
        ApprovalDelegation.objects.create(source_job_title=delegate_job, approval_level=level)

        normal_as_delegate = resolve_approval_level_authorization(delegate_permission, level, 'normal')
        delegated_as_required = resolve_approval_level_authorization(required_permission, level, 'delegated')
        delegated_as_delegate = resolve_approval_level_authorization(delegate_permission, level, 'delegated')

        self.assertFalse(normal_as_delegate['allowed'])
        self.assertFalse(delegated_as_required['allowed'])
        self.assertTrue(delegated_as_delegate['allowed'])
        self.assertEqual(delegated_as_delegate['approval_mode'], 'delegated')

    def test_delegation_still_requires_workflow_approval_permission(self):
        required_job, _ = self.make_permission('Required Manager', can_approve_purchase_order=True)
        delegate_job, delegate_permission = self.make_permission('Delegate Without Purchase Permission')
        level = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=required_job,
        )
        ApprovalDelegation.objects.create(source_job_title=delegate_job, approval_level=level)
        spare_request = self.make_spare_request()

        authorization = resolve_approval_level_authorization(delegate_permission, level)

        self.assertTrue(authorization['allowed'])
        self.assertEqual(authorization['approval_mode'], 'delegated')
        self.assertFalse(can_user_approve_spare_part_request(spare_request, delegate_permission))

    def test_spare_part_approval_record_stores_delegation_metadata(self):
        required_job, _ = self.make_permission('Required Manager', can_approve_purchase_order=True)
        delegate_job, delegate_permission = self.make_permission('General Manager', can_approve_purchase_order=True)
        delegate_user = self.make_user_for_job('delegate-approver', delegate_job)
        level = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=required_job,
        )
        ApprovalDelegation.objects.create(source_job_title=delegate_job, approval_level=level)
        spare_request = self.make_spare_request()
        authorization = resolve_approval_level_authorization(delegate_permission, level)

        approval = SparePartApproval.objects.create(
            request=spare_request,
            approver=delegate_user,
            approval_level=level,
            **approval_record_metadata(authorization),
            decision='accepted',
        )

        self.assertEqual(approval.approval_mode, 'delegated')
        self.assertEqual(approval.required_job_title, required_job)
        self.assertEqual(approval.delegated_for_job_title, required_job)

    def test_delegated_spare_part_approval_notifies_visible_required_job_users(self):
        required_job, _ = self.make_permission(
            'Visible Required Manager',
            can_approve_purchase_order=True,
            can_view_all_departments=True,
        )
        required_user = self.make_user_for_job('required-visible-user', required_job)
        delegate_job, delegate_permission = self.make_permission('Delegating Manager', can_approve_purchase_order=True)
        delegate_user = self.make_user_for_job('delegated-notifier', delegate_job)
        level = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=required_job,
        )
        ApprovalDelegation.objects.create(source_job_title=delegate_job, approval_level=level)
        spare_request = self.make_spare_request()
        authorization = resolve_approval_level_authorization(delegate_permission, level, 'delegated')
        approval = SparePartApproval.objects.create(
            request=spare_request,
            approver=delegate_user,
            approval_level=level,
            **approval_record_metadata(authorization),
            decision='accepted',
        )

        notifications = notify_delegated_spare_part_approval(spare_request, approval)

        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].recipient, required_user)
        self.assertIn('نيابة عنك', notifications[0].message)
        self.assertEqual(Notification.objects.filter(recipient=required_user).count(), 1)

    def test_complete_report_approval_record_stores_normal_metadata(self):
        required_job, required_permission = self.make_permission(
            'Report Manager',
            can_approve_achievement_report=True,
        )
        approver = self.make_user_for_job('report-approver', required_job)
        level = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.ACHIEVEMENT_REPORT,
            order_number=1,
            job_title=required_job,
        )
        report = CompleteReport.objects.create(
            maintenance_request=self.maintenance_request,
            complete_report='Done',
            status='awaiting_acceptenace',
        )
        authorization = resolve_approval_level_authorization(required_permission, level)

        approval = CompleteReportApproval.objects.create(
            report=report,
            approver=approver,
            approval_level=level,
            **approval_record_metadata(authorization),
            decision='accepted',
        )

        self.assertEqual(approval.approval_mode, 'normal')
        self.assertEqual(approval.required_job_title, required_job)
        self.assertIsNone(approval.delegated_for_job_title)

    def test_dynamic_workflow_length_can_increase_and_decrease(self):
        level_one_job, _ = self.make_permission('Level 1', can_approve_purchase_order=True)
        level_two_job, _ = self.make_permission('Level 2', can_approve_purchase_order=True)
        level_three_job, level_three_permission = self.make_permission('Level 3', can_approve_purchase_order=True)
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

        self.assertTrue(is_workflow_complete(2, ApprovalWorkflow.PURCHASE))

        level_three = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=3,
            job_title=level_three_job,
        )
        self.assertFalse(is_workflow_complete(2, ApprovalWorkflow.PURCHASE))
        self.assertEqual(get_next_approval_level(ApprovalWorkflow.PURCHASE, 2), level_three)
        self.assertTrue(can_user_approve_spare_part_request(self.make_spare_request(acceptance=2), level_three_permission))

        level_three.delete()
        self.assertTrue(is_workflow_complete(2, ApprovalWorkflow.PURCHASE))

    def test_store_issue_and_achievement_report_use_dynamic_levels(self):
        store_job, store_permission = self.make_permission('Store Manager', can_approve_store_requisition=True)
        report_job, report_permission = self.make_permission('Report Manager', can_approve_achievement_report=True)
        ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.STORE_ISSUE,
            order_number=1,
            job_title=store_job,
        )
        ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.ACHIEVEMENT_REPORT,
            order_number=1,
            job_title=report_job,
        )

        store_request = self.make_spare_request(order_kind='store_requisition')
        report = CompleteReport.objects.create(
            maintenance_request=self.maintenance_request,
            complete_report='Done',
            status='awaiting_acceptenace',
        )

        self.assertTrue(can_user_approve_spare_part_request(store_request, store_permission))
        self.assertTrue(can_user_approve_complete_report(report, report_permission))

    def test_approval_level_order_is_unique_per_workflow(self):
        first_job, _ = self.make_permission('First', can_approve_purchase_order=True)
        second_job, _ = self.make_permission('Second', can_approve_purchase_order=True)
        ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=first_job,
        )

        duplicate = ApprovalLevel(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=second_job,
        )

        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_purchase_approval_tracking_marks_approved_and_pending_levels(self):
        level_one_job, _ = self.make_permission('Department Manager', can_approve_purchase_order=True)
        level_two_job, _ = self.make_permission('Finance Manager', can_approve_purchase_order=True)
        approver = self.make_user_for_job('department-manager', level_one_job)
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
        spare_request = self.make_spare_request(acceptance=1)
        SparePartApproval.objects.create(
            request=spare_request,
            approver=approver,
            approval_level=level_one,
            decision='accepted',
        )

        sections = build_maintenance_approval_tracking_sections(
            self.maintenance_request,
            [spare_request],
            None,
        )

        self.assertEqual(sections[0]['workflow_label'], 'تعميدات طلب الشراء')
        self.assertEqual(len(sections[0]['levels']), 2)
        self.assertTrue(sections[0]['levels'][0]['is_approved'])
        self.assertEqual(sections[0]['levels'][0]['approved_by_job_title'], 'Department Manager')
        self.assertFalse(sections[0]['levels'][0]['approved_by_delegation'])
        self.assertTrue(sections[0]['levels'][1]['is_current_pending_level'])

    def test_store_issue_approval_tracking_uses_dynamic_levels(self):
        store_job, _ = self.make_permission('Store Manager', can_approve_store_requisition=True)
        ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.STORE_ISSUE,
            order_number=1,
            job_title=store_job,
        )
        store_request = self.make_spare_request(order_kind='store_requisition')

        sections = build_maintenance_approval_tracking_sections(
            self.maintenance_request,
            [store_request],
            None,
        )

        self.assertEqual(sections[0]['workflow_label'], 'تعميدات طلب الصرف من المخازن')
        self.assertEqual(sections[0]['levels'][0]['required_job_title'], 'Store Manager')
        self.assertTrue(sections[0]['levels'][0]['is_current_pending_level'])

    def test_achievement_report_approval_tracking_uses_dynamic_levels(self):
        report_job, _ = self.make_permission('Report Manager', can_approve_achievement_report=True)
        ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.ACHIEVEMENT_REPORT,
            order_number=1,
            job_title=report_job,
        )
        report = CompleteReport.objects.create(
            maintenance_request=self.maintenance_request,
            complete_report='Done',
            status='awaiting_acceptenace',
        )

        sections = build_maintenance_approval_tracking_sections(
            self.maintenance_request,
            [],
            report,
        )

        self.assertEqual(sections[0]['workflow_label'], 'تعميدات تقرير الإنجاز')
        self.assertEqual(sections[0]['levels'][0]['required_job_title'], 'Report Manager')
        self.assertTrue(sections[0]['levels'][0]['is_current_pending_level'])

    def test_approval_tracking_marks_delegated_approval(self):
        required_job, _ = self.make_permission('Department Manager', can_approve_purchase_order=True)
        delegate_job, _ = self.make_permission('General Manager', can_approve_purchase_order=True)
        delegate_user = self.make_user_for_job('general-manager', delegate_job)
        required_level = ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=required_job,
        )
        spare_request = self.make_spare_request(acceptance=1)
        SparePartApproval.objects.create(
            request=spare_request,
            approver=delegate_user,
            approval_level=required_level,
            decision='accepted',
        )

        sections = build_maintenance_approval_tracking_sections(
            self.maintenance_request,
            [spare_request],
            None,
        )

        self.assertTrue(sections[0]['levels'][0]['is_approved'])
        self.assertTrue(sections[0]['levels'][0]['approved_by_delegation'])
        self.assertEqual(sections[0]['levels'][0]['approved_by_job_title'], 'General Manager')

    def test_approval_tracking_omits_missing_workflows(self):
        sections = build_maintenance_approval_tracking_sections(
            self.maintenance_request,
            [],
            None,
        )

        self.assertEqual(sections, [])

    def test_existing_workflow_without_configured_levels_returns_empty_section(self):
        spare_request = self.make_spare_request()

        sections = build_maintenance_approval_tracking_sections(
            self.maintenance_request,
            [spare_request],
            None,
        )

        self.assertEqual(sections[0]['workflow_label'], 'تعميدات طلب الشراء')
        self.assertFalse(sections[0]['has_configured_levels'])
        self.assertEqual(sections[0]['levels'], [])

    def test_card_summary_shows_current_responsible_job_title_and_employees(self):
        manager_job, _ = self.make_permission('Maintenance Manager', can_approve_purchase_order=True)
        first_user = self.make_user_for_job('ahmed', manager_job)
        second_user = self.make_user_for_job('ali', manager_job)
        ApprovalLevel.objects.create(
            workflow_type=ApprovalWorkflow.PURCHASE,
            order_number=1,
            job_title=manager_job,
        )
        spare_request = self.make_spare_request()

        summary = build_spare_part_card_summary(spare_request)
        approval = summary['approval']

        self.assertEqual(approval['progress_label'], 'تقدم تعميد الشراء')
        self.assertEqual(approval['progress_current'], 0)
        self.assertEqual(approval['progress_total'], 1)
        self.assertEqual(approval['current_level_order'], 1)
        self.assertEqual(approval['current_required_job_title'], 'Maintenance Manager')
        self.assertEqual(approval['employee_label'], 'الموظفون')
        self.assertIn(first_user.username, approval['current_required_employees'])
        self.assertIn(second_user.username, approval['current_required_employees'])
