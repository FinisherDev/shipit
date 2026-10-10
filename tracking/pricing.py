from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

#Price quotes: base fee + per-kg charge, looked up from admin-editable Rate rows.

VOLUMETRIC_DIVISOR = Decimal('5000')  # industry-standard cm^3 -> kg divisor


class PricingError(Exception):
    """Raised when no rate exists for the requested service and route."""

def _normalise(place):
        return ' '.join((place or '').lower().split())


def route_scope(origin, destination):
    """'local' when origin and destination are the same place, else 'domestic'."""
    return 'local' if _normalise(origin) == _normalise(destination) else 'domestic'


def volumetric_weight(length_cm, width_cm, height_cm):
    """Weight (kg) a parcel 'takes up' based on its size; 0 if dimensions are missing."""
    if not (length_cm and width_cm and height_cm):
        return Decimal('0')
    volume = Decimal(length_cm) * Decimal(width_cm) * Decimal(height_cm)
    return (volume / VOLUMETRIC_DIVISOR).quantize(Decimal('0.01'))


def get_quote(*, origin, destination, service_type, weight_kg, length_cm=None, width_cm=None, height_cm=None):
    from .models import Rate  # imported lazily to avoid a circular import

    scope = route_scope(origin, destination)
    try:
        rate = Rate.objects.get(service_type=service_type, scope=scope)
    except Rate.DoesNotExist:
        raise PricingError(f"The '{service_type}' service is not available for this route.")

    chargeable = max(Decimal(weight_kg), volumetric_weight(length_cm, width_cm, height_cm))
    weight_charge = (rate.price_per_kg * chargeable).quantize(Decimal('0.01'))
    total = (rate.base_price + weight_charge).quantize(Decimal('0.01'))
    return {
        'service_type': service_type,
        'scope': scope,
        'chargeable_weight_kg': chargeable,
        'base_price': rate.base_price,
        'weight_charge': weight_charge,
        'total': total,
        'currency': rate.currency,
        'estimated_days': rate.estimated_days,
        'estimated_delivery': timezone.localdate() + timedelta(days=rate.estimated_days),
    }