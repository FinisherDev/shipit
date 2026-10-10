import uuid
from decimal import Decimal
from functools import partial

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from .pricing import volumetric_weight


class InvalidTransition(Exception):
    """Raised when a shipment is moved to a status it isn't allowed to reach."""


class Profile(models.Model):
    """Extra data for a user. `role` drives what the API lets them do."""

    class Role(models.TextChoices):
        CUSTOMER = 'customer', 'Customer'
        COURIER = 'courier', 'Courier'
        STAFF = 'staff', 'Staff'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)
    phone = models.CharField(max_length=30, blank=True)

    def __str__(self):
        return f"{self.user} ({self.role})"


class Rate(models.Model):
    """Admin-editable pricing for one service on one kind of route."""

    class Scope(models.TextChoices):
        LOCAL = 'local', 'Local (same city)'
        DOMESTIC = 'domestic', 'Domestic (different cities)'

    service_type = models.CharField(max_length=20, choices=[
        ('economy', 'Economy'), ('standard', 'Standard'), ('express', 'Express'),
    ])
    scope = models.CharField(max_length=20, choices=Scope.choices)
    base_price = models.DecimalField(max_digits=10, decimal_places=2)
    price_per_kg = models.DecimalField(max_digits=10, decimal_places=2)
    estimated_days = models.PositiveSmallIntegerField()
    currency = models.CharField(max_length=3, default='NGN')

    class Meta:
        unique_together = [('service_type', 'scope')]
        ordering = ['scope', 'service_type']

    def __str__(self):
        return f"{self.service_type} / {self.scope}"


class Shipment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'Pending', 'Pending'
        PICKED_UP = 'Picked Up', 'Picked Up'
        AT_HUB = 'At Sorting Hub', 'At Sorting Hub'
        IN_TRANSIT = 'In Transit', 'In Transit'
        OUT_FOR_DELIVERY = 'Out for Delivery', 'Out for Delivery'
        DELIVERY_ATTEMPTED = 'Delivery Attempted', 'Delivery Attempted'
        DELIVERED = 'Delivered', 'Delivered'
        RETURNED = 'Returned', 'Returned'
        CANCELLED = 'Cancelled', 'Cancelled'

    class ServiceType(models.TextChoices):
        ECONOMY = 'economy', 'Economy'
        STANDARD = 'standard', 'Standard'
        EXPRESS = 'express', 'Express'

    class PaymentStatus(models.TextChoices):
        UNPAID = 'unpaid', 'Unpaid'
        PAID = 'paid', 'Paid'
        REFUND_PENDING = 'refund_pending', 'Refund pending'

    # Which statuses a shipment may move to from each status.
    TRANSITIONS = {
        Status.PENDING: {Status.PICKED_UP, Status.CANCELLED},
        Status.PICKED_UP: {Status.AT_HUB, Status.IN_TRANSIT},
        Status.AT_HUB: {Status.IN_TRANSIT, Status.OUT_FOR_DELIVERY},
        Status.IN_TRANSIT: {Status.AT_HUB, Status.OUT_FOR_DELIVERY},
        Status.OUT_FOR_DELIVERY: {Status.DELIVERED, Status.DELIVERY_ATTEMPTED},
        Status.DELIVERY_ATTEMPTED: {Status.OUT_FOR_DELIVERY, Status.RETURNED},
        Status.DELIVERED: set(),
        Status.RETURNED: set(),
        Status.CANCELLED: set(),
    }
    TERMINAL_STATUSES = (Status.DELIVERED, Status.RETURNED, Status.CANCELLED)

    tracking_id = models.CharField(max_length=20, unique=True, editable=False)
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='shipments',
    )
    courier = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='assigned_shipments',
    )

    sender_name = models.CharField(max_length=255)
    sender_phone = models.CharField(max_length=30, blank=True)
    sender_email = models.EmailField(blank=True)
    sender_address = models.TextField(blank=True)

    receiver_name = models.CharField(max_length=255)
    receiver_phone = models.CharField(max_length=30, blank=True)
    receiver_email = models.EmailField(blank=True)
    receiver_address = models.TextField(blank=True)

    origin = models.CharField(max_length=255)
    destination = models.CharField(max_length=255)

    description = models.CharField(max_length=255, blank=True)
    weight_kg = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal('1.00'),
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    length_cm = models.PositiveIntegerField(null=True, blank=True)
    width_cm = models.PositiveIntegerField(null=True, blank=True)
    height_cm = models.PositiveIntegerField(null=True, blank=True)
    declared_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    service_type = models.CharField(max_length=20, choices=ServiceType.choices, default=ServiceType.STANDARD)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='NGN')
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)

    status = models.CharField(max_length=50, choices=Status.choices, default=Status.PENDING)
    estimated_delivery = models.DateField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Remember the status we loaded so save() can tell if it changed.
        # (Read from __dict__ so deferred fields don't trigger extra queries.)
        self._original_status = self.__dict__.get('status')

    # ---- derived values ----
    @property
    def volumetric_weight(self):
        return volumetric_weight(self.length_cm, self.width_cm, self.height_cm)

    @property
    def chargeable_weight(self):
        return max(self.weight_kg, self.volumetric_weight)

    @property
    def is_active(self):
        return self.status not in self.TERMINAL_STATUSES

    # ---- behaviour ----
    @staticmethod
    def _generate_tracking_id():
        return f"SHIP-{uuid.uuid4().hex[:10].upper()}"

    def set_status(self, new_status, *, user=None, location='', note=''):
        """Move the shipment to `new_status`, enforcing the business rules.

        This is the one door every status change should go through. It raises
        InvalidTransition if the move isn't allowed. The admin can still edit
        the status field directly as a manual override.
        """
        new_status = self.Status(new_status)
        if new_status == self.status:
            raise InvalidTransition(f"Shipment is already '{new_status.value}'.")
        if new_status not in self.TRANSITIONS.get(self.status, set()):
            raise InvalidTransition(
                f"A shipment that is '{self.status}' can't be changed to '{new_status.value}'."
            )
        if new_status == self.Status.PICKED_UP and self.payment_status != self.PaymentStatus.PAID:
            raise InvalidTransition("Payment is required before a shipment can be picked up.")
        if new_status == self.Status.DELIVERED and not hasattr(self, 'proof_of_delivery'):
            raise InvalidTransition("Record proof of delivery to mark a shipment as delivered.")

        if new_status == self.Status.DELIVERED:
            self.delivered_at = timezone.now()
        if new_status == self.Status.CANCELLED and self.payment_status == self.PaymentStatus.PAID:
            self.payment_status = self.PaymentStatus.REFUND_PENDING

        self._history_extra = {'location': location, 'note': note, 'updated_by': user}
        self.status = new_status.value
        self.save()

    def save(self, *args, **kwargs):
        if not self.tracking_id:
            tracking_id = self._generate_tracking_id()
            while Shipment.objects.filter(tracking_id=tracking_id).exists():
                tracking_id = self._generate_tracking_id()
            self.tracking_id = tracking_id

        is_new = self._state.adding
        super().save(*args, **kwargs)

        # The one place history is recorded, so it works the same whether a
        # change comes from the API, the admin, or a script.
        if is_new or self.status != self._original_status:
            extra = self.__dict__.pop('_history_extra', {})
            entry = TrackingHistory.objects.create(shipment=self, status=self.status, **extra)
            from .notifications import notify_status_change
            transaction.on_commit(partial(notify_status_change, entry.pk))
        self._original_status = self.status

    def __str__(self):
        return self.tracking_id


class TrackingHistory(models.Model):
    shipment = models.ForeignKey(Shipment, related_name='history', on_delete=models.CASCADE)
    status = models.CharField(max_length=50)
    location = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True)  # internal; never shown on the public tracking page
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Tracking Histories'
        ordering = ['timestamp', 'id']

    def __str__(self):
        return f"{self.shipment.tracking_id} - {self.status}"


class ProofOfDelivery(models.Model):
    shipment = models.OneToOneField(Shipment, on_delete=models.CASCADE, related_name='proof_of_delivery')
    recipient_name = models.CharField(max_length=255)
    note = models.TextField(blank=True)
    photo = models.ImageField(upload_to='proof_of_delivery/%Y/%m/', blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Proofs of delivery'

    def __str__(self):
        return f"POD for {self.shipment.tracking_id}"


class Payment(models.Model):
    class Provider(models.TextChoices):
        PAYSTACK = 'paystack', 'Paystack'
        CASH = 'cash', 'Cash'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name='payments')
    reference = models.CharField(max_length=100, unique=True)
    provider = models.CharField(max_length=20, choices=Provider.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='NGN')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.reference} ({self.status})"


class NotificationLog(models.Model):
    """Audit trail of every notification attempt, successful or not."""

    class Channel(models.TextChoices):
        EMAIL = 'email', 'Email'
        SMS = 'sms', 'SMS'

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name='notifications')
    channel = models.CharField(max_length=10, choices=Channel.choices)
    recipient = models.CharField(max_length=255)
    message = models.TextField()
    success = models.BooleanField(default=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.channel} to {self.recipient} ({'sent' if self.success else 'failed'})"