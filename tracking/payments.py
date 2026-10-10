import hashlib
import hmac
import logging
import uuid
from decimal import Decimal

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Payment, Shipment

logger = logging.getLogger(__name__)

PAYSTACK_BASE_URL = 'https://api.paystack.co'


class PaymentError(Exception):
    """The gateway failed or rejected the request."""


class PaymentNotConfigured(PaymentError):
    """No Paystack key has been set."""


def to_minor_units(amount):
    """Naira -> kobo (Paystack works in the smallest currency unit)."""
    return int((Decimal(amount) * 100).to_integral_value())


def _headers():
    key = settings.PAYSTACK_SECRET_KEY
    if not key:
        raise PaymentNotConfigured('Online payments are not configured on this server.')
    return {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}


def initialize_payment(shipment, email, callback_url=None):
    """Start a Paystack transaction. Returns (payment, authorization_url)."""
    headers = _headers()  # fail early if not configured, before creating a record
    payment = Payment.objects.create(
        shipment=shipment,
        reference=f"{shipment.tracking_id}-{uuid.uuid4().hex[:8]}",
        provider=Payment.Provider.PAYSTACK,
        amount=shipment.price,
        currency=shipment.currency,
    )
    payload = {
        'email': email,
        'amount': to_minor_units(payment.amount),
        'currency': payment.currency,
        'reference': payment.reference,
        'metadata': {'tracking_id': shipment.tracking_id},
    }
    if callback_url:
        payload['callback_url'] = callback_url
    try:
        response = requests.post(
            f'{PAYSTACK_BASE_URL}/transaction/initialize', json=payload, headers=headers, timeout=15
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        _fail(payment)
        raise PaymentError('Could not reach the payment provider. Please try again.') from exc
    if not response.ok or not body.get('status'):
        _fail(payment)
        raise PaymentError(body.get('message') or 'The payment provider rejected the request.')
    return payment, body['data']['authorization_url']


def verify_with_gateway(payment):
    """Ask Paystack for the transaction's real status. Returns Paystack's `data` dict."""
    headers = _headers()
    try:
        response = requests.get(
            f'{PAYSTACK_BASE_URL}/transaction/verify/{payment.reference}', headers=headers, timeout=15
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise PaymentError('Could not reach the payment provider. Please try again.') from exc
    if not response.ok or not body.get('status'):
        raise PaymentError(body.get('message') or 'Verification failed.')
    return body['data']


def apply_gateway_result(payment, data):
    """Update our records from a Paystack transaction `data` dict. Returns True if paid.

    Never trusts the status alone: the amount and currency must match what we charged.
    """
    gateway_status = data.get('status')
    if gateway_status == 'success':
        if (data.get('amount') == to_minor_units(payment.amount)
                and (data.get('currency') or payment.currency) == payment.currency):
            mark_paid(payment)
            return True
        logger.warning('Amount/currency mismatch for payment %s; not marking as paid.', payment.reference)
        return False
    if gateway_status in ('failed', 'abandoned', 'reversed') and payment.status == Payment.Status.PENDING:
        _fail(payment)
    return False


def _fail(payment):
    Payment.objects.filter(pk=payment.pk, status=Payment.Status.PENDING).update(status=Payment.Status.FAILED)
    payment.status = Payment.Status.FAILED


@transaction.atomic
def mark_paid(payment):
    """Idempotently record a successful payment and mark its shipment as paid."""
    payment = Payment.objects.select_for_update().select_related('shipment').get(pk=payment.pk)
    if payment.status == Payment.Status.SUCCESS:
        return payment
    payment.status = Payment.Status.SUCCESS
    payment.paid_at = timezone.now()
    payment.save(update_fields=['status', 'paid_at'])
    shipment = payment.shipment
    if shipment.payment_status != Shipment.PaymentStatus.PAID:
        shipment.payment_status = Shipment.PaymentStatus.PAID
        shipment.save(update_fields=['payment_status', 'updated_at'])
    return payment


def record_cash_payment(shipment):
    payment = Payment.objects.create(
        shipment=shipment,
        reference=f"CASH-{uuid.uuid4().hex[:12].upper()}",
        provider=Payment.Provider.CASH,
        amount=shipment.price,
        currency=shipment.currency,
    )
    return mark_paid(payment)


def signature_is_valid(raw_body, signature):
    """Check Paystack's x-paystack-signature header (HMAC-SHA512 of the raw body)."""
    secret = settings.PAYSTACK_SECRET_KEY
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)