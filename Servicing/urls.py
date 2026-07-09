"""
URL configuration for Servicing project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path,include
from maintenance import views as maintenance_views
from . import error_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('notifications/', maintenance_views.notifications_list, name='notifications_list'),
    path('notifications/<int:pk>/read/', maintenance_views.mark_notification_as_read, name='mark_notification_as_read'),
    path('notifications/read-all/', maintenance_views.mark_all_notifications_as_read, name='mark_all_notifications_as_read'),
    path('notifications/<int:pk>/go/', maintenance_views.notification_redirect, name='notification_redirect'),
    path('', include('accounts.urls')),
    path('hospital_units/', include('hospital_units.urls')),
    path('maintenance/', include('maintenance.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler400 = error_views.bad_request_view
handler403 = error_views.permission_denied_view
handler404 = error_views.page_not_found_view
handler500 = error_views.server_error_view
