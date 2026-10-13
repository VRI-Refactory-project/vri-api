from rest_framework.throttling import BaseThrottle

from .models import AuditLog


def client_ip(request):
    # Same logic as the rate limiter: respects NUM_PROXIES (X-Forwarded-For) behind a proxy.
    return BaseThrottle().get_ident(request) or None


def record(event, user, request=None):
    """Write one audit entry. `request` is optional; when given, the caller's IP address is stored."""
    return AuditLog.objects.create(user=user, event=event, ip_address=client_ip(request) if request else None)
