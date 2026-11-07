from django.urls import path

from apps.realtime import views

urlpatterns = [
    path("ticket/", views.TicketView.as_view(), name="realtime-ticket"),
    path(
        "sessions/<int:session_id>/guest-invitations/",
        views.GuestInvitationCreateView.as_view(),
        name="guest-invitation-create",
    ),
    path(
        "guest-invitations/join/",
        views.GuestJoinView.as_view(),
        name="guest-invitation-join",
    ),
]
