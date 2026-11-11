
"""URL routes for realtime sessions."""

from django.urls import path

from apps.realtime import views

urlpatterns = [
    path("ticket/", views.TicketView.as_view(), name="realtime-ticket"),
    path(
        "connection-quality/test/",
        views.ConnectionQualityTestView.as_view(),
        name="connection-quality-test",
    ),
    path(
        "connection-quality/guest/<str:token>/test/",
        views.GuestConnectionQualityTestView.as_view(),
        name="guest-connection-quality-test",
    ),
    path(
        "sessions/<int:session_id>/feedback/",
        views.SessionFeedbackView.as_view(),
        name="session-feedback",
    ),
    path(
        "requests/<int:request_id>/feedback/",
        views.SessionFeedbackView.as_view(),
        name="request-feedback",
    ),
    path(
        "livekit/webhook/",
        views.LiveKitWebhookView.as_view(),
        name="livekit-webhook",
    ),
]
