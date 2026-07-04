from types import SimpleNamespace

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, OperationalError, ProgrammingError

from .models import SystemSettings
from .permissions import get_effective_permissions


SYSTEM_SETTINGS_CACHE_KEY = 'hmms_system_settings'


def get_default_system_settings():
    return SimpleNamespace(
        system_name='نظام إدارة الصيانة الطبية',
        organization_name='',
        logo=None,
        phone='',
        email='',
        address='',
        footer_text='',
        maintenance_policy_notes='',
    )


def system_settings(request):
    cached_settings = cache.get(SYSTEM_SETTINGS_CACHE_KEY)
    if cached_settings is None:
        try:
            cached_settings = SystemSettings.get_solo()
        except (DatabaseError, OperationalError, ProgrammingError):
            cached_settings = get_default_system_settings()
        cache.set(SYSTEM_SETTINGS_CACHE_KEY, cached_settings, None)

    context = {
        'system_settings': cached_settings,
        'system_version': getattr(settings, 'SYSTEM_VERSION', '1.0'),
    }
    if request.user.is_authenticated:
        context['effective_permissions'] = get_effective_permissions(request.user)
    return context
