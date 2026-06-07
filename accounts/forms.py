import re

from django import forms
from django.contrib.auth.models import User

from .models import JobTitle, JobTitlePermission, Profile, Specialty, SystemSettings


STORE_AND_PURCHASE_ORDER_CHOICES = [('', '---------')] + [(value, str(value)) for value in range(1, 9)]
ACHIEVEMENT_ORDER_CHOICES = [('', '---------')] + [(value, str(value)) for value in range(1, 6)]


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
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active', 'password', 'confirm_password']
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
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
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
            'employee_id': forms.TextInput(attrs={'class': 'form-control'}),
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
        exclude = ['job_title']
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
            'store_approval_order': forms.Select(
                choices=STORE_AND_PURCHASE_ORDER_CHOICES,
                attrs={'class': 'form-control', 'id': 'id_store_approval_order'},
            ),
            'can_bypass_store_approval': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'max_store_bypass_order': forms.Select(
                choices=STORE_AND_PURCHASE_ORDER_CHOICES,
                attrs={'class': 'form-control', 'id': 'id_max_store_bypass_order'},
            ),
            'can_approve_purchase_order': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'purchase_approval_order': forms.Select(
                choices=STORE_AND_PURCHASE_ORDER_CHOICES,
                attrs={'class': 'form-control', 'id': 'id_purchase_approval_order'},
            ),
            'can_bypass_purchase_approval': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'max_purchase_bypass_order': forms.Select(
                choices=STORE_AND_PURCHASE_ORDER_CHOICES,
                attrs={'class': 'form-control', 'id': 'id_max_purchase_bypass_order'},
            ),
            'can_issue_from_store': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_confirm_issuance': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_add_achievement_report': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_edit_delete_pending_report': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'can_approve_achievement_report': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'achievement_approval_order': forms.Select(
                choices=ACHIEVEMENT_ORDER_CHOICES,
                attrs={'class': 'form-control', 'id': 'id_achievement_approval_order'},
            ),
            'can_bypass_achievement_approval': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'max_achievement_bypass_order': forms.Select(
                choices=ACHIEVEMENT_ORDER_CHOICES,
                attrs={'class': 'form-control', 'id': 'id_max_achievement_bypass_order'},
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['store_approval_order'].choices = STORE_AND_PURCHASE_ORDER_CHOICES
        self.fields['purchase_approval_order'].choices = STORE_AND_PURCHASE_ORDER_CHOICES
        self.fields['achievement_approval_order'].choices = ACHIEVEMENT_ORDER_CHOICES
        self.fields['max_store_bypass_order'].choices = STORE_AND_PURCHASE_ORDER_CHOICES
        self.fields['max_purchase_bypass_order'].choices = STORE_AND_PURCHASE_ORDER_CHOICES
        self.fields['max_achievement_bypass_order'].choices = ACHIEVEMENT_ORDER_CHOICES

    def clean(self):
        cleaned_data = super().clean()
        structures = (
            ('can_approve_store_requisition', 'store_approval_order', 'can_bypass_store_approval', 'max_store_bypass_order'),
            ('can_approve_purchase_order', 'purchase_approval_order', 'can_bypass_purchase_approval', 'max_purchase_bypass_order'),
            ('can_approve_achievement_report', 'achievement_approval_order', 'can_bypass_achievement_approval', 'max_achievement_bypass_order'),
        )
        for approve_field, order_field, bypass_field, max_bypass_field in structures:
            if not cleaned_data.get(approve_field):
                cleaned_data[order_field] = None
                cleaned_data[bypass_field] = False
                cleaned_data[max_bypass_field] = None
            elif not cleaned_data.get(bypass_field):
                cleaned_data[max_bypass_field] = None
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.full_clean()
        if commit:
            instance.save()
            self.save_m2m()
        return instance


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
            'logo': forms.ClearableFileInput(attrs={'class': 'form-control-file', 'accept': 'image/*'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'address': forms.TextInput(attrs={'class': 'form-control'}),
            'footer_text': forms.TextInput(attrs={'class': 'form-control'}),
            'maintenance_policy_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 5}),
        }
