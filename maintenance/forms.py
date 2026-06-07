from django import forms
from django.core.exceptions import ObjectDoesNotExist
from django.forms import inlineformset_factory

from .models import MaintenanceRequest, SparePartRequest, SpareParts


def apply_bootstrap_styles(fields):
    for field in fields.values():
        css_class = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
        current_classes = field.widget.attrs.get('class', '')
        if css_class not in current_classes:
            field.widget.attrs['class'] = f'{current_classes} {css_class}'.strip()


class MaintenanceRequestForm(forms.ModelForm):
    class Meta:
        model = MaintenanceRequest
        fields = ['description', 'department', 'required_specialty', 'priority']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 2, 'placeholder': 'وصف العطل بالتفصيل'}),
            'department': forms.Select(attrs={'id': 'department-field'}),
            'required_specialty': forms.Select(),
            'priority': forms.Select(),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)  # الحصول على المستخدم من kwargs
        
        super().__init__(*args, **kwargs)
        apply_bootstrap_styles(self.fields)

        if user and user.is_authenticated and hasattr(user, 'profile'):
            profile = user.profile
            try:
                can_view_all_departments = profile.job_title.permissions.can_view_all_departments
            except (AttributeError, ObjectDoesNotExist):
                can_view_all_departments = False
            if not can_view_all_departments and profile.managing_department:
                self.fields['department'].disabled = True


class SparePartRequestForm(forms.ModelForm):
    class Meta:
        model = SparePartRequest
        fields = ['order_kind', 'description']
        widgets = {
            'order_kind': forms.Select(),
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'سبب طلب قطع الغيار أو ملاحظات إضافية'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_bootstrap_styles(self.fields)


class SparePartsForm(forms.ModelForm):
    class Meta:
        model = SpareParts
        fields = ['part_name', 'quantity']
        widgets = {
            'part_name': forms.TextInput(attrs={'placeholder': 'اسم القطعة'}),
            'quantity': forms.NumberInput(attrs={'min': 1}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_bootstrap_styles(self.fields)

    def clean_quantity(self):
        quantity = self.cleaned_data['quantity']
        if quantity <= 0:
            raise forms.ValidationError("يجب أن تكون الكمية أكبر من صفر.")
        return quantity


SparePartsFormSet = inlineformset_factory(
    SparePartRequest,
    SpareParts,
    form=SparePartsForm,
    fields=['part_name', 'quantity'],
    extra=1,
    min_num=1,
    validate_min=True,
    can_delete=True,
)
