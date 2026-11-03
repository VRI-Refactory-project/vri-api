from django.urls import path

from apps.realtime import views

urlpatterns = [
    path("ticket/", views.TicketView.as_view(), name="realtime-ticket"),
]
