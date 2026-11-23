from django.urls import path

from . import views

app_name = "wallet"

urlpatterns = [
    path("", views.WalletView.as_view(), name="wallet"),
    path("topups/", views.TopUpCreateView.as_view(), name="topup-create"),
    path(
        "topups/<str:reference>/", views.TopUpDetailView.as_view(), name="topup-detail"
    ),
    path("payouts/", views.PayoutCreateView.as_view(), name="payout-create"),
    path(
        "webhooks/flutterwave/",
        views.FlutterwaveWebhookView.as_view(),
        name="flutterwave-webhook",
    ),
]
