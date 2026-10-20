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
]
