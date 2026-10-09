from django.dispatch import receiver
from django.db.models.signals import post_save
from .models import TrackingHistory

@receiver(post_save, sender='tracking.Shipment')
def add_history(sender, instance, **kwargs):
    print("Working")
    TrackingHistory.objects.create(shipment = instance, status = instance.status, timestamp = instance.updated_at )