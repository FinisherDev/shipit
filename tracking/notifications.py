import logging

from django.conf import settings
from django.core.mail import send_mail
from django.utils.module_loading import import_string

#Status-change notifications over email and SMS.

#Triggered from Shipment.save() via transaction.on_commit, so a notification is only
#sent once the change is safely stored, and a failing email never breaks the request.
#Every attempt is written to NotificationLog. For production, run this in a task queue
#(e.g. Celery) instead of inline; the entry point is the same function.

logger = logging.getLogger(__name__)


HEADLINES = {
    'Pending': 'Your shipment has been booked',
    'Picked Up': 'Your shipment has been picked up',
    'At Sorting Hub': 'Your shipment has arrived at a sorting hub',
    'In Transit': 'Your shipment is on its way',
    'Out for Delivery': 'Your shipment is out for delivery',
    'Delivery Attempted': 'We tried to deliver your shipment',
    'Delivered': 'Your shipment has been delivered',
    'Returned': 'Your shipment is being returned to the sender',
    'Cancelled': 'Your shipment has been cancelled',
}


class ConsoleSMSBackend:
    """Development SMS backend: just logs. Swap for a real provider in production."""

    def send(self, to, message):
        logger.info('SMS to %s: %s', to, message)


def _sms_backend():
    return import_string(settings.SMS_BACKEND)()


def build_message(entry):
    shipment = entry.shipment
    url = settings.TRACKING_URL_TEMPLATE.format(tracking_id=shipment.tracking_id)
    headline = HEADLINES.get(entry.status, f"Shipment status: {entry.status}")
    lines = [f"{headline} ({shipment.tracking_id}).", f"From {shipment.origin} to {shipment.destination}."]
    if entry.location:
        lines.append(f"Latest location: {entry.location}.")
    lines.append(f"Track it: {url}")
    return headline, ' '.join(lines)


def notify_status_change(history_id):
    from .models import NotificationLog, TrackingHistory

    if not getattr(settings, 'NOTIFICATIONS_ENABLED', True):
        return
    try:
        entry = TrackingHistory.objects.select_related('shipment').get(pk=history_id)
    except TrackingHistory.DoesNotExist:
        return
    shipment = entry.shipment
    subject, message = build_message(entry)

    emails = {e for e in (shipment.receiver_email, shipment.sender_email) if e}
    phones = {p for p in (shipment.receiver_phone, shipment.sender_phone) if p}

    for email in sorted(emails):
        try:
            send_mail(f"ShipIt: {subject}", message, settings.DEFAULT_FROM_EMAIL, [email])
            NotificationLog.objects.create(shipment=shipment, channel='email', recipient=email, message=message)
        except Exception as exc:  # never let a notification break a status update
            logger.exception('Email notification failed')
            NotificationLog.objects.create(
                shipment=shipment, channel='email', recipient=email, message=message, success=False, error=str(exc)
            )

    if phones:
        try:
            backend = _sms_backend()
        except Exception as exc:
            logger.exception('SMS backend could not be loaded')
            backend, backend_error = None, str(exc)
        for phone in sorted(phones):
            try:
                if backend is None:
                    raise RuntimeError(backend_error)
                backend.send(phone, message)
                NotificationLog.objects.create(shipment=shipment, channel='sms', recipient=phone, message=message)
            except Exception as exc:
                logger.exception('SMS notification failed')
                NotificationLog.objects.create(
                    shipment=shipment, channel='sms', recipient=phone, message=message, success=False, error=str(exc)
                )