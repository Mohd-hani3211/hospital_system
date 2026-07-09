from decimal import Decimal
from pathlib import Path

from django.forms.models import model_to_dict
from django.utils import timezone

from .models import AuditLog


def get_client_ip(request):
    if request is None:
        return None
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def serialize_value(value):
    if isinstance(value, (list, tuple)):
        return [serialize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_value(item) for key, item in value.items()}
    if hasattr(value, 'pk'):
        return value.pk
    if hasattr(value, 'name') and hasattr(value, 'field'):
        return value.name or ''
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return value


def serialize_instance(instance, fields=None):
    if instance is None:
        return {}
    data = model_to_dict(instance, fields=fields)
    return {key: serialize_value(value) for key, value in data.items()}


def create_audit_log(user, action_type, instance, old_data=None, new_data=None, description=None, request=None):
    if user is not None and not getattr(user, 'is_authenticated', False):
        user = None

    model_name = instance.__class__.__name__ if instance is not None else 'System'
    object_id = str(getattr(instance, 'pk', '') or '')
    object_repr = str(instance)[:255] if instance is not None else ''

    try:
        return AuditLog.objects.create(
            user=user,
            action_type=action_type,
            model_name=model_name,
            object_id=object_id,
            object_repr=object_repr,
            old_data=old_data or None,
            new_data=new_data or None,
            description=description or '',
            ip_address=get_client_ip(request),
        )
    except Exception:
        return None


def status_change_data(old_status, new_status):
    return {'status': old_status}, {'status': new_status}


def now_iso():
    return timezone.now().isoformat()
