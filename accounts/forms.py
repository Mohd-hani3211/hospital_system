import re

from django import forms
from django.contrib.auth.models import User

from .models import (
    ApprovalDelegation,
    ApprovalLevel,
    ApprovalWorkflow,
    JobTitle,
    JobTitlePermission,
    Profile,
    Specialty,
    SystemSettings,
    UserPermissionOverride,
)
from .permissions import get_permission_field_metadata


APPROVAL_FORM_WORKFLOWS = (
    {
        'workflow_type': ApprovalWorkflow.STORE_ISSUE,
        'approve_field': 'can_approve_store_requisition',
        'order_field': 'store_approval_order',
        'delegation_field': 'store_delegation_levels',
        'legacy_bypass_field': 'can_bypass_store_approval',
        'legacy_max_field': 'max_store_bypass_order',
        'delegation_label': 'مراحل تعميد الصرف المخزني المسموح بالنيابة عنها',
        'has_levels_attr': 'has_store_approval_levels',
    },
    {
        'workflow_type': ApprovalWorkflow.PURCHASE,
        'approve_field': 'can_approve_purchase_order',
        'order_field': 'purchase_approval_order',
        'delegation_field': 'purchase_delegation_levels',
        'legacy_bypass_field': 'can_bypass_purchase_approval',
        'legacy_max_field': 'max_purchase_bypass_order',
        'delegation_label': 'مراحل تعميد الشراء المسموح بالنيابة عنها',
        'has_levels_attr': 'has_purchase_approval_levels',
    },
    {
        'workflow_type': ApprovalWorkflow.ACHIEVEMENT_REPORT,
        'approve_field': 'can_approve_achievement_report',
        'order_field': 'achievement_approval_order',
        'delegation_field': 'achievement_delegation_levels',
        'legacy_bypass_field': 'can_bypass_achievement_approval',
        'legacy_max_field': 'max_achievement_bypass_order',
        'delegation_label': 'مراحل تعميد تقرير الإنجاز المسموح بالنيابة عنها',
        'has_levels_attr': 'has_achievement_approval_levels',
    },
)


def coerce_optional_int(value):
    if value in ('', None):
        return None
    return int(value)


class UserForm(forms.ModelForm):
    password = forms.CharField(
        label="كلمة المرور",
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Password'}),
    )
    confirm_password = forms.CharField(
        label="تأكيد كلمة المرور",
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
    )

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'password', 'confirm_password']
        labels = {
            'username': 'اسم المستخدم',
            'first_name': 'الاسم الأول',
            'last_name': 'اسم العائلة',
            'email': 'البريد الإلكتروني',
            'is_active': 'الحساب نشط',
        }
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Username'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First Name'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last Name'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email'}),
        }

    def __init__(self, *args, **kwargs):
        self.require_password = kwargs.pop('require_password', True)
        super().__init__(*args, **kwargs)
        self._original_password = self.instance.password if self.instance and self.instance.pk else ''

        self.fields['password'].required = self.require_password
        self.fields['confirm_password'].required = self.require_password
        if not self.require_password:
            self.fields['password'].initial = ''
            self.fields['password'].help_text = "اتركه فارغاً إذا لا تريد تغيير كلمة المرور."
            self.fields['confirm_password'].help_text = "اتركه فارغاً إذا لا تريد تغيير كلمة المرور."

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')

        if self.require_password and not password:
            self.add_error('password', "كلمة المرور مطلوبة")
        if password or confirm_password:
            if password != confirm_password:
                self.add_error('confirm_password', "كلمتا المرور غير متطابقتين")
            elif len(password) < 4:
                self.add_error('password', "كلمة المرور يجب أن تكون 4 أحرف على الأقل")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        if not self.cleaned_data.get('password') and self._original_password:
            user.password = self._original_password
        if commit:
            user.save()
            self.save_m2m()
        return user


class SimplePasswordChangeForm(forms.Form):
    old_password = forms.CharField(
        label="كلمة المرور القديمة",
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'current-password'}),
    )
    new_password = forms.CharField(
        label="كلمة المرور الجديدة",
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
        min_length=4,
        error_messages={'min_length': "كلمة المرور الجديدة يجب أن تكون 4 أحرف على الأقل."},
    )
    confirm_password = forms.CharField(
        label="تأكيد كلمة المرور الجديدة",
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_old_password(self):
        old_password = self.cleaned_data.get('old_password')
        if old_password and not self.user.check_password(old_password):
            raise forms.ValidationError("كلمة المرور القديمة غير صحيحة.")
        return old_password

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get('new_password')
        confirm_password = cleaned_data.get('confirm_password')
        if new_password and confirm_password and new_password != confirm_password:
            self.add_error('confirm_password', "كلمتا المرور غير متطابقتين")
        return cleaned_data


# class ProfileForm(forms.ModelForm):
#     class Meta:
#         model = Profile
#         fields = ['job_title', 'specialty', 'managing_department', 'employee_id', 'phone_number']
#         widgets = {
#             'job_title': forms.Select(attrs={'class': 'form-control'}),
#             'specialty': forms.Select(attrs={'class': 'form-control'}),
#             'managing_department': forms.Select(attrs={'class': 'form-control'}),
#             'employee_id': forms.TextInput(attrs={'class': 'form-control'}),
#             'phone_number': forms.TextInput(attrs={'class': 'form-control'}),
#         }


class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['job_title', 'specialty', 'managing_department', 'employee_id', 'phone_number']
        widgets = {
            'job_title': forms.Select(attrs={'class': 'form-control'}),
            'specialty': forms.Select(attrs={'class': 'form-control'}),
            'managing_department': forms.Select(attrs={'class': 'form-control'}),
            'employee_id': forms.TextInput(attrs={'class': 'form-control','required': 'false'}),
            'phone_number': forms.TextInput(attrs={
                'class': 'form-control',
                'pattern': '^7[0-9]{8}$',
                'placeholder': '777123456',
            }),
        }

    def __init__(self, *args, **kwargs):
        super(ProfileForm, self).__init__(*args, **kwargs)
        from .models import JobTitle
        
        # كود قياسي سليم يقبله Django 5.0 بدون أي اعتراض
        choices = [('', 'الرجاء الاختيار')]
        for job in JobTitle.objects.all():
            choices.append((job.id, job.title_name))
            
        self.fields['job_title'].choices = choices

    def clean_phone_number(self):
        phone = self.cleaned_data.get('phone_number')
        if phone and not re.match(r"^7\d{8}$", phone):
            raise forms.ValidationError("رقم الهاتف يجب أن يبدأ بالرقم 7 ويتكون من 9 أرقام.")
        return phone


class JobTitleForm(forms.ModelForm):
    class Meta:
        model = JobTitle
        fields = ['title_name']
        widgets = {
            'title_name': forms.TextInput(attrs={'class': 'form-control'}),
        }


class JobTitlePermissionForm(forms.ModelForm):
    class Meta:
        model = JobTitlePermission
        exclude = [
            'job_title',
            'can_manage_user_permissions',
            'can_bypass_store_approval',
            'max_store_bypass_order',
            'can_bypass_purchase_approval',
            'max_purchase_bypass_order',
            'can_bypass_achievement_approval',
            'max_achievement_bypass_order',
        ]
        widgets = {
            'can_manage_system_setup': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_view_audit_log': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_department_manager': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_engineer': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_add_maintenance': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_approve_maintenance': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_edit_pending_maintenance': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_view_all_departments': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_view_dashboard': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_add_spare_parts': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_edit_delete_pending_parts': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_approve_store_requisition': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'store_approval_order': forms.Select(attrs={'class': 'form-control', 'id': 'id_store_approval_order'}),
            'can_approve_purchase_order': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'purchase_approval_order': forms.Select(attrs={'class': 'form-control', 'id': 'id_purchase_approval_order'}),
            'can_issue_from_store': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_confirm_issuance': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_add_achievement_report': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_edit_delete_pending_report': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_approve_achievement_report': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'achievement_approval_order': forms.Select(attrs={'class': 'form-control', 'id': 'id_achievement_approval_order'}),
        }

    def __init__(self, *args, **kwargs):
        self.job_title = kwargs.pop('job_title', None)
        super().__init__(*args, **kwargs)
        if self.job_title is None and self.instance and self.instance.pk:
            self.job_title = self.instance.job_title

        for config in APPROVAL_FORM_WORKFLOWS:
            self._configure_workflow_fields(config)

    def _workflow_levels(self, workflow_type):
        return list(
            ApprovalLevel.objects.filter(workflow_type=workflow_type)
            .select_related('job_title')
            .order_by('order_number')
        )

    def _configure_workflow_fields(self, config):
        workflow_type = config['workflow_type']
        levels = self._workflow_levels(workflow_type)
        setattr(self, config['has_levels_attr'], bool(levels))
        current_level = None
        if self.job_title:
            current_level = next((level for level in levels if level.job_title_id == self.job_title.id), None)

        order_choices = [('', '---------')]
        for level in levels:
            if self.job_title and level.job_title_id == self.job_title.id:
                owner_label = 'المسمى الحالي'
            else:
                owner_label = level.job_title.title_name
            order_choices.append((level.order_number, f"المستوى {level.order_number} - {owner_label}"))

        order_field_name = config['order_field']
        existing_order_field = self.fields[order_field_name]
        self.fields[order_field_name] = forms.TypedChoiceField(
            choices=order_choices,
            coerce=coerce_optional_int,
            required=False,
            label=existing_order_field.label,
            widget=forms.Select(attrs={
                'class': 'form-control',
                'id': f'id_{order_field_name}',
            }),
        )
        if current_level is not None:
            self.fields[order_field_name].initial = current_level.order_number

        delegation_choices = []
        for level in levels:
            if self.job_title and level.job_title_id == self.job_title.id:
                continue
            delegation_choices.append((str(level.pk), f"المستوى {level.order_number} - {level.job_title.title_name}"))

        delegation_field = forms.MultipleChoiceField(
            choices=delegation_choices,
            required=False,
            widget=forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
            label=config['delegation_label'],
        )
        if self.job_title:
            delegation_field.initial = [
                str(level_id)
                for level_id in ApprovalDelegation.objects.filter(
                    source_job_title=self.job_title,
                    approval_level__workflow_type=workflow_type,
                ).values_list('approval_level_id', flat=True)
            ]
        self.fields[config['delegation_field']] = delegation_field

    def clean(self):
        cleaned_data = super().clean()
        for config in APPROVAL_FORM_WORKFLOWS:
            approve_field = config['approve_field']
            order_field = config['order_field']
            delegation_field = config['delegation_field']
            workflow_type = config['workflow_type']
            current_level = None
            if self.job_title:
                current_level = ApprovalLevel.objects.filter(
                    workflow_type=workflow_type,
                    job_title=self.job_title,
                ).first()

            selected_delegations = cleaned_data.get(delegation_field) or []
            has_approval_permission = cleaned_data.get(approve_field) or cleaned_data.get('is_department_manager')
            if has_approval_permission and cleaned_data.get('is_department_manager'):
                cleaned_data[approve_field] = True

            if not has_approval_permission:
                if current_level is not None:
                    self.add_error(
                        approve_field,
                        "لا يمكن تعطيل صلاحية التعميد لهذا المسمى لأنه مسؤول عن مستوى تعميد. يرجى تعديل إعدادات التعميدات أولاً.",
                    )
                cleaned_data[order_field] = None
                cleaned_data[delegation_field] = []
                continue

            order = cleaned_data.get(order_field)
            if order in ('', None):
                if current_level is not None:
                    self.add_error(
                        order_field,
                        "لا يمكن إزالة مستوى التعميد من هذه الصفحة. يرجى تعديل إعدادات التعميدات أولاً.",
                    )
                cleaned_data[order_field] = None
            else:
                order = int(order)
                cleaned_data[order_field] = order
                duplicate = ApprovalLevel.objects.filter(
                    workflow_type=workflow_type,
                    order_number=order,
                )
                if self.job_title:
                    duplicate = duplicate.exclude(job_title=self.job_title)
                duplicate = duplicate.select_related('job_title').first()
                if duplicate:
                    self.add_error(order_field, f"المستوى {order} مخصص مسبقاً للمسمى الوظيفي {duplicate.job_title}.")

            valid_level_ids = set(
                ApprovalLevel.objects.filter(workflow_type=workflow_type)
                .values_list('pk', flat=True)
            )
            normalized_delegations = []
            for selected_level_id in selected_delegations:
                level_id = int(selected_level_id)
                if level_id not in valid_level_ids:
                    self.add_error(delegation_field, "مرحلة التفويض المحددة غير موجودة.")
                    continue
                normalized_delegations.append(level_id)
            cleaned_data[delegation_field] = normalized_delegations
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        for config in APPROVAL_FORM_WORKFLOWS:
            delegation_ids = self.cleaned_data.get(config['delegation_field']) or []
            setattr(instance, config['legacy_bypass_field'], bool(delegation_ids))
            setattr(instance, config['legacy_max_field'], None)
        instance.full_clean()
        if commit:
            instance.save()
            self.save_m2m()
            self.save_approval_configuration(instance)
        return instance

    def save_approval_configuration(self, instance=None):
        instance = instance or self.instance
        job_title = instance.job_title
        for config in APPROVAL_FORM_WORKFLOWS:
            workflow_type = config['workflow_type']
            order = self.cleaned_data.get(config['order_field'])

            if order:
                selected_level = ApprovalLevel.objects.filter(
                    workflow_type=workflow_type,
                    order_number=order,
                ).first()
                if selected_level is not None and selected_level.job_title_id == job_title.id:
                    setattr(instance, config['order_field'], selected_level.order_number)

            delegation_ids = self.cleaned_data.get(config['delegation_field']) or []
            ApprovalDelegation.objects.filter(
                source_job_title=job_title,
                approval_level__workflow_type=workflow_type,
            ).delete()
            ApprovalDelegation.objects.bulk_create([
                ApprovalDelegation(source_job_title=job_title, approval_level_id=level_id)
                for level_id in delegation_ids
            ])

        self.instance = instance
        return instance


class UserPermissionOverrideForm(forms.Form):
    grant_permissions = forms.MultipleChoiceField(
        label="صلاحيات مضافة لهذا المستخدم",
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    deny_permissions = forms.MultipleChoiceField(
        label="صلاحيات مسحوبة من هذا المستخدم",
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user')
        grant_choices = kwargs.pop('grant_choices', None)
        deny_choices = kwargs.pop('deny_choices', None)
        manageable_permission_names = kwargs.pop('manageable_permission_names', None)
        super().__init__(*args, **kwargs)
        default_permission_choices = [
            (permission['name'], permission['label'])
            for permission in get_permission_field_metadata()
        ]
        self.fields['grant_permissions'].choices = grant_choices or default_permission_choices
        self.fields['deny_permissions'].choices = deny_choices or default_permission_choices
        if manageable_permission_names is None:
            manageable_permission_names = {
                value
                for value, _label in [
                    *self.fields['grant_permissions'].choices,
                    *self.fields['deny_permissions'].choices,
                ]
            }
        self.manageable_permission_names = set(manageable_permission_names)

        if not self.is_bound:
            self.fields['grant_permissions'].initial = list(
                UserPermissionOverride.objects.filter(
                    user=self.user,
                    action=UserPermissionOverride.ACTION_GRANT,
                    permission_name__in=self.manageable_permission_names,
                ).values_list('permission_name', flat=True)
            )
            self.fields['deny_permissions'].initial = list(
                UserPermissionOverride.objects.filter(
                    user=self.user,
                    action=UserPermissionOverride.ACTION_DENY,
                    permission_name__in=self.manageable_permission_names,
                ).values_list('permission_name', flat=True)
            )

    def clean(self):
        cleaned_data = super().clean()
        grants = set(cleaned_data.get('grant_permissions') or [])
        denies = set(cleaned_data.get('deny_permissions') or [])
        duplicated_permissions = grants & denies
        if duplicated_permissions:
            raise forms.ValidationError("لا يمكن منح وسحب نفس الصلاحية لنفس المستخدم في نفس الوقت.")
        return cleaned_data

    def save(self):
        grant_permissions = self.cleaned_data.get('grant_permissions') or []
        deny_permissions = self.cleaned_data.get('deny_permissions') or []
        UserPermissionOverride.objects.filter(
            user=self.user,
            permission_name__in=self.manageable_permission_names,
        ).delete()
        UserPermissionOverride.objects.bulk_create([
            *[
                UserPermissionOverride(
                    user=self.user,
                    permission_name=permission_name,
                    action=UserPermissionOverride.ACTION_GRANT,
                )
                for permission_name in grant_permissions
            ],
            *[
                UserPermissionOverride(
                    user=self.user,
                    permission_name=permission_name,
                    action=UserPermissionOverride.ACTION_DENY,
                )
                for permission_name in deny_permissions
            ],
        ])


class SpecialtyForm(forms.ModelForm):
    class Meta:
        model = Specialty
        fields = ['name', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}),
        }


class SystemSettingsForm(forms.ModelForm):
    class Meta:
        model = SystemSettings
        fields = [
            'system_name',
            'organization_name',
            'logo',
            'phone',
            'email',
            'address',
            'footer_text',
            'maintenance_policy_notes',
        ]
        widgets = {
            'system_name': forms.TextInput(attrs={'class': 'form-control'}),
            'organization_name': forms.TextInput(attrs={'class': 'form-control'}),
            'logo': forms.FileInput(attrs={'class': 'hospital-logo-input', 'accept': 'image/*'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'footer_text': forms.TextInput(attrs={'class': 'form-control'}),
            'maintenance_policy_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }
