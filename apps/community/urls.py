from django.urls import path

from . import views

app_name = "community"

urlpatterns = [
    path("people/", views.PeopleView.as_view(), name="people"),
    path("requests/", views.RequestListView.as_view(), name="requests"),
    path(
        "requests/<int:pk>/", views.RequestDetailView.as_view(), name="request-detail"
    ),
    path(
        "requests/<int:pk>/accept/",
        views.RequestAcceptView.as_view(),
        name="request-accept",
    ),
    path(
        "requests/<int:pk>/decline/",
        views.RequestDeclineView.as_view(),
        name="request-decline",
    ),
    path("connections/", views.ConnectionListView.as_view(), name="connections"),
    path(
        "connections/<int:pk>/",
        views.ConnectionDetailView.as_view(),
        name="connection-detail",
    ),
]
