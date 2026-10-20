
from django.urls import path

from apps.service_requests.views import ServiceRequestListCreateView

urlpatterns = [
    path("", ServiceRequestListCreateView.as_view(), name="service-request-list-create"),
]
