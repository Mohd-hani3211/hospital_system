from django.urls import path
from . import views

# app_name="maintenance"

urlpatterns = [
    path('dashboard/', views.MaintenanceDashboardView.as_view(), name='maintenance_dashboard'),
    path('requests/', views.maintenanceRequestListView.as_view(), name='request_list'),
    path('requests/history/', views.maintenanceRequestHistoryView.as_view(), name='request_history'),
    path('requests/new/', views.maintenanceRequestCreateView.as_view(), name='request_create'),
    path('requests/edit/<int:pk>/', views.maintenanceRequestEditView.as_view(), name='request_edit'),
    path('requests/delete/<int:pk>/', views.maintenanceRequestDeleteView.as_view(), name='request_delete'),

    path('requests/<int:pk>/approve/', views.MaintenanceRequestApproveView.as_view(), name='request_approve'),
    path('requests/<int:pk>/reject/', views.MaintenanceRequestRejectedView.as_view(), name='request_reject'),
    path('requests/<int:pk>/excute/', views.MaintenanceRequestExcuteView.as_view(), name='request_maintenance_excute'),
    path('requests/<int:pk>/details/', views.maintenanceRequestDetails.as_view(), name='request_maintenance_details'),
    path('requests/<int:pk>/completed/', views.MaintenanceRequestCompleteView.as_view(), name='request_maintenance_completed'),
    path('requests/<int:pk>/completed/', views.MaintenanceRequestCompleteView.as_view(), name='maintenance_complete_report_acceptance'),



    path('requests/<int:pk>/spare_part/create',views.SparePartRequestCreatView , name='spare_part_create'),
    path('requests/spare_part/list', views.SparePartRequestListView.as_view() , name='spare_part_list'),
    path('requests/spare_part/history/', views.SparePartRequestHistoryView.as_view(), name='spare_part_history'),
    path('requests/spare_part/<int:pk>/details/', views.SparePartRequestDetails.as_view(), name='spare_part_details'),
    path('requests/spare_part/<int:pk>/edit/', views.SparePartRequestUpdateView.as_view(), name='spare_part_update'),
    path('requests/spare_part/<int:pk>/acceptance/', views.SparePartRequestAcceptance , name='spare_part_acceptance'),
    path('requests/spare_part/<int:pk>/reject/', views.SparePartRequestRejected , name='spare_part_rejected'),
    path('spare-parts/<int:pk>/approve-from-card/', views.SparePartRequestApproveFromCard, name='spare_part_approve_from_card'),
    path('spare-parts/<int:pk>/reject-from-card/', views.SparePartRequestRejectFromCard, name='spare_part_reject_from_card'),
    path('spare-parts/<int:pk>/approve/', views.SparePartRequestApproveFromDetail, name='spare_part_approve_from_detail'),
    path('spare-parts/<int:pk>/reject/', views.SparePartRequestRejectFromDetail, name='spare_part_reject_from_detail'),
    path('spare-parts/<int:pk>/mark-purchase-available/', views.SparePartRequestMarkPurchaseAvailableFromDetail, name='spare_part_mark_purchase_available_from_detail'),
    path('spare-parts/<int:pk>/issue-from-store/', views.SparePartRequestIssueFromStoreFromDetail, name='spare_part_issue_from_store_from_detail'),
    path('spare-parts/<int:pk>/confirm-issuance/', views.SparePartRequestConfirmIssuanceFromDetail, name='spare_part_confirm_issuance_from_detail'),
    path('complete-reports/<int:pk>/approve/', views.CompleteReportApproveFromDetail, name='complete_report_approve_from_detail'),
    path('complete-reports/<int:pk>/reject/', views.CompleteReportRejectFromDetail, name='complete_report_reject_from_detail'),

    # يمكنك إضافة المزيد من المسارات هنا لعرض التفاصيل، التحديث، الحذف، إلخ.
]
