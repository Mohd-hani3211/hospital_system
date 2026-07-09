from django import forms
from .models import Building, Department, Department, Floor    



class BuildingForm(forms.ModelForm):
    class Meta:
        model = Building
        fields = ['name','description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'id': 'id_name'}),
            'description': forms.Textarea(attrs={ 'rows':3,'class': 'form-control', 'id': 'id_description'}),

        }   


class FloorForm(forms.ModelForm):
    class Meta:
        model = Floor
        fields = ['building', 'name','description']
        widgets = {
            'building': forms.Select(attrs={'class': 'form-control ', 'id': 'id_building'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'id': 'id_name'}),
            'description': forms.Textarea(attrs={ 'rows':3,'class': 'form-control', 'id': 'id_description'}),
        }


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ['floor', 'name', 'rooms_count', 'beds_count', 'bathrooms_count']
        widgets = {
            'floor': forms.Select(attrs={'class': 'form-control', 'id': 'id_floor'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'id': 'id_name'}),
            'rooms_count': forms.NumberInput(attrs={'class': 'form-control', 'id': 'id_rooms_count','required': False}),
            'beds_count': forms.NumberInput(attrs={'class': 'form-control', 'id': 'id_beds_count', 'required': False}),
            'bathrooms_count': forms.NumberInput(attrs={'class': 'form-control', 'id': 'id_bathrooms_count', 'required': False}),
        }
        