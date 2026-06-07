from django.urls import path
from . import views

urlpatterns = [
    path('buildings/', views.BuildingsList.as_view(), name='buildings'),
    path('buildings/add/', views.AddBuilding.as_view(), name='add_building'),
    path('buildings/edit/<int:pk>/', views.EditBuilding.as_view(), name='edit_building'),
    path('buildings/delete/<int:pk>/', views.DeleteBuilding.as_view(), name='delete_building'),
    path('floors/', views.FloorsList.as_view(), name='floors'),
    path('floors/add/', views.AddFloor.as_view(), name='add_floor'),
    path('floors/edit/<int:pk>/', views.EditFloor.as_view(), name='edit_floor'),
    path('floors/delete/<int:pk>/', views.DeleteFloor.as_view(), name='delete_floor'),

    path('departments/', views.DepartmentsList.as_view(), name='departments'),
    path('departments/add/', views.AddDepartment.as_view(), name='add_department'),
    path('departments/edit/<int:pk>/', views.EditDepartment.as_view(), name='edit_department'),
    path('departments/delete/<int:pk>/', views.DeleteDepartment.as_view(), name='delete_department'),
]   