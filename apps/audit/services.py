from rest_framework.throttling import BaseThrottle

from .models import AuditLog


def client_ip(request):
    # Same logic as the rate limiter: respects NUM_PROXIES (X-Forwarded-For) behind a proxy.
    return BaseThrottle().get_ident(request) or None


def record(event, user, request=None, target_user=None):
    """Write one audit entry. `request` is optional; when given, the caller's IP address is stored.

    `target_user` is the account the action was done to, when that is someone other than `user`.
    """
    return AuditLog.objects.create(
        user=user,
        event=event,
        target_user=target_user,
        ip_address=client_ip(request) if request else None,
    )
