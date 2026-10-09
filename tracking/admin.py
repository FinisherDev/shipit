from django.contrib import admin
from .models import Shipment, TrackingHistory

# Register your models here.

class ShipmentAdmin(admin.ModelAdmin):
    pass

admin.site.register(Shipment, ShipmentAdmin)

class TrackingHistoryAdmin(admin.ModelAdmin):
    pass

admin.site.register(TrackingHistory, TrackingHistoryAdmin)
