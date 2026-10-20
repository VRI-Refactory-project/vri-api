from django.urls import path

from apps.service_requests import views

# Mounted at /api/v1/interpreters/me/offers/ (V2-010).
urlpatterns = [
    path("", views.InterpreterOfferListView.as_view(), name="interpreter-offer-list"),
    path(
        "<int:pk>/respond/",
        views.InterpreterOfferRespondView.as_view(),
        name="interpreter-offer-respond",
    ),
    path(
        "<int:pk>/state/",
        views.InterpreterOfferStateView.as_view(),
        name="interpreter-offer-state",
    ),
]
