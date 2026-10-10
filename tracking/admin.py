from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin

from .models import NotificationLog, Payment, Profile, ProofOfDelivery, Rate, Shipment, TrackingHistory

User = get_user_model()


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False


class UserAdminWithProfile(UserAdmin):
    inlines = [ProfileInline]
    list_display = ['username', 'email', 'is_staff', 'get_role']
    list_select_related = ['profile']

    @admin.display(description='Role')
    def get_role(self, user):
        return getattr(getattr(user, 'profile', None), 'role', '-')


admin.site.unregister(User)
admin.site.register(User, UserAdminWithProfile)


class TrackingHistoryInline(admin.TabularInline):
    model = TrackingHistory
    extra = 0
    can_delete = False
    readonly_fields = ['status', 'location', 'note', 'updated_by', 'timestamp']

    def has_add_permission(self, request, obj=None):
        return False


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    readonly_fields = ['reference', 'provider', 'amount', 'currency', 'status', 'paid_at']

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ['tracking_id', 'sender_name', 'receiver_name', 'origin', 'destination',
                    'status', 'payment_status', 'courier', 'price', 'created_at']
    list_filter = ['status', 'payment_status', 'service_type', 'courier']
    search_fields = ['tracking_id', 'sender_name', 'receiver_name', 'receiver_phone']
    readonly_fields = ['tracking_id', 'created_at', 'updated_at']
    list_select_related = ['courier']
    inlines = [TrackingHistoryInline, PaymentInline]
    # Note: editing the status here is a manual override; it bypasses the transition rules
    # that the API enforces (but is still recorded in the history).


@admin.register(Rate)
class RateAdmin(admin.ModelAdmin):
    list_display = ['service_type', 'scope', 'base_price', 'price_per_kg', 'estimated_days', 'currency']
    list_editable = ['base_price', 'price_per_kg', 'estimated_days']


@admin.register(ProofOfDelivery)
class ProofOfDeliveryAdmin(admin.ModelAdmin):
    list_display = ['shipment', 'recipient_name', 'recorded_by', 'created_at']


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['reference', 'shipment', 'provider', 'amount', 'currency', 'status', 'paid_at']
    list_filter = ['provider', 'status']
    search_fields = ['reference', 'shipment__tracking_id']


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'shipment', 'channel', 'recipient', 'success']
    list_filter = ['channel', 'success']


admin.site.register(TrackingHistory)