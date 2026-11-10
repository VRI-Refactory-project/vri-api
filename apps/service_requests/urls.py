from django.urls import path

from apps.service_requests import views

urlpatterns = [
    path(
        "",
        views.ServiceRequestListCreateView.as_view(),
        name="service-request-list-create",
    ),
    path("mine/", views.MyOpenRequestView.as_view(), name="service-request-mine"),
    path(
        "<int:pk>/",
        views.ServiceRequestDetailView.as_view(),
        name="service-request-detail",
    ),
    path(
        "<int:pk>/cancel/",
        views.ServiceRequestCancelView.as_view(),
        name="service-request-cancel",
    ),
    path(
        "<int:pk>/end/",
        views.ServiceRequestEndView.as_view(),
        name="service-request-end",
    ),
    path(
        "<int:pk>/call-token/",
        views.ServiceRequestCallTokenView.as_view(),
        name="service-request-call-token",
    ),
    # V2-012: hearing guests invited by the Deaf user
    path(
        "<int:pk>/invitations/",
        views.SessionInvitationListCreateView.as_view(),
        name="session-invitations",
    ),
    path(
        "<int:pk>/invitations/<int:invitation_id>/remove/",
        views.SessionInvitationRemoveView.as_view(),
        name="session-invitation-remove",
    ),
    # The guest's own endpoints: no login, the token in the emailed link is the key.
    path("guest/<str:token>/", views.GuestLobbyView.as_view(), name="guest-lobby"),
    path(
        "guest/<str:token>/join/",
        views.GuestJoinView.as_view(),
        name="guest-join",
    ),
]
