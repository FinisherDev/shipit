import uuid
from django.db import models

# Create your models here.

class Shipment(models.Model):
    class Status(models.TextChoices):
            PENDING = 'Pending', 'Pending'
            IN_TRANSIT = 'In Transit', 'In Transit'
            DELIVERED = 'Delivered', 'Delivered'

    tracking_id = models.CharField(max_length=20, unique=True, editable=False)
    sender_name = models.CharField(max_length=255)
    receiver_name = models.CharField(max_length=255)
    origin = models.CharField(max_length=255)
    destination = models.CharField(max_length=255)
    status = models.CharField(
            max_length=50,
            choices=Status.choices,
            default=Status.PENDING,
        )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Remember the status we loaded so save() can tell if it changed.
            self._original_status = self.status
    
    @staticmethod
    def _generate_tracking_id():
        return f"SHIP-{uuid.uuid4().hex[:10].upper()}"

    def save(self, *args, **kwargs):
        if not self.tracking_id:
            tracking_id = self._generate_tracking_id()
            while Shipment.objects.filter(tracking_id=tracking_id).exists():
                tracking_id = self._generate_tracking_id()
            self.tracking_id = tracking_id

        is_new = self._state.adding
        super().save(*args, **kwargs)

        # One place records history, so it works the same whether the change
        # comes from the API, the admin, or a script.
        if is_new or self.status != self._original_status:
            TrackingHistory.objects.create(shipment=self, status=self.status)
        self._original_status = self.status
        

    def __str__(self):
        return self.tracking_id
    
class TrackingHistory(models.Model):
    shipment = models.ForeignKey(Shipment, related_name='history', on_delete=models.CASCADE)
    status = models.CharField(max_length=50)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Tracking Histories'
        ordering = ['timestamp', 'id']

    def __str__(self):
        return f"{self.shipment.tracking_id} - {self.status}"
    