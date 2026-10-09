import uuid
from django.db import models

# Create your models here.

class Shipment(models.Model):
    tracking_id = models.CharField(max_length=20, unique=True, editable=False)
    sender_name = models.CharField(max_length=255)
    receiver_name = models.CharField(max_length=255)
    origin = models.CharField(max_length=255)
    destination = models.CharField(max_length=255)
    status = models.CharField(
        max_length=50,
        choices = [
            ('Pending', 'Pending'),
            ('In Transit', 'In Transit'),
            ('Delivered', 'Delivered'),
        ],
        default='Pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.tracking_id:
            self.tracking_id = f"SHIP-{uuid.uuid4().hex[:10].upper()}"
            super().save(*args, **kwargs)

    def __str__(self):
        return self.tracking_id
    
class TrackingHistory(models.Model):
    shipment = models.ForeignKey(Shipment, related_name='history', on_delete=models.CASCADE)
    status = models.CharField(max_length=50)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Tracking Histories'

    def __str__(self):
        return f"{self.shipment.tracking_id} - {self.status}"
    