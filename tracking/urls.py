from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register('shipments', views.ShipmentViewSet, basename='shipment')

urlpatterns = [
    path('health/', views.HealthView.as_view(), name='health'),
    path('auth/register/', views.RegisterView.as_view(), name='register'),
    path('auth/token/', views.LoginView.as_view(), name='api-token'),
    path('auth/me/', views.MeView.as_view(), name='me'),
    path('quotes/', views.QuoteView.as_view(), name='quote'),
    path('track/<str:tracking_id>/', views.PublicTrackView.as_view(), name='track'),
    path('payments/paystack/webhook/', views.PaystackWebhookView.as_view(), name='paystack-webhook'),
    path('payments/verify/<str:reference>/', views.PaymentVerifyView.as_view(), name='payment-verify'),
    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
    path('', include(router.urls)),
]