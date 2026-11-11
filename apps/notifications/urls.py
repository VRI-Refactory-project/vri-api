from django.urls import path

from apps.notifications.views import (
    NotificationListView,
    NotificationMarkReadView,
    PushDeviceRegistrationView,
)
from apps.notifications.web_push import WebPushKeyView, WebPushSubscriptionView

app_name = "notifications"

urlpatterns = [
    path("", NotificationListView.as_view(), name="list"),
    path(
        "<int:pk>/read/",
        NotificationMarkReadView.as_view(),
        name="mark-read",
    ),
    path(
        "devices/",
        PushDeviceRegistrationView.as_view(),
        name="devices",
    ),
    path(
        "web-push/",
        WebPushSubscriptionView.as_view(),
        name="web-push",
    ),
    path(
        "web-push/key/",
        WebPushKeyView.as_view(),
        name="web-push-key",
    ),
]
