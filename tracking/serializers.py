from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from . import pricing
from .models import Payment, Profile, ProofOfDelivery, Shipment, TrackingHistory

User = get_user_model()

CONTACT_FIELDS = [
    'sender_name', 'sender_phone', 'sender_email', 'sender_address',
    'receiver_name', 'receiver_phone', 'receiver_email', 'receiver_address',
]
PARCEL_FIELDS = [
    'origin', 'destination', 'description', 'weight_kg',
    'length_cm', 'width_cm', 'height_cm', 'declared_value', 'service_type',
]


# ---------- shipments ----------

class TrackingHistorySerializer(serializers.ModelSerializer):
    updated_by = serializers.CharField(source='updated_by.username', default=None, read_only=True)

    class Meta:
        model = TrackingHistory
        fields = ['status', 'location', 'note', 'updated_by', 'timestamp']


class ProofOfDeliverySerializer(serializers.ModelSerializer):
    location = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=255)

    class Meta:
        model = ProofOfDelivery
        fields = ['recipient_name', 'note', 'photo', 'location', 'created_at']
        read_only_fields = ['created_at']


class ShipmentSerializer(serializers.ModelSerializer):
    """Full read view for authenticated users."""
    history = TrackingHistorySerializer(many=True, read_only=True)
    proof_of_delivery = ProofOfDeliverySerializer(read_only=True)
    courier_username = serializers.CharField(source='courier.username', default=None, read_only=True)
    chargeable_weight = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True)

    class Meta:
        model = Shipment
        fields = [
            'tracking_id', *CONTACT_FIELDS, *PARCEL_FIELDS, 'chargeable_weight',
            'price', 'currency', 'payment_status', 'status', 'estimated_delivery',
            'delivered_at', 'courier', 'courier_username', 'customer',
            'proof_of_delivery', 'history', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ShipmentCreateSerializer(serializers.ModelSerializer):
    """Booking input. The price is always calculated on the server, never taken from the client."""

    class Meta:
        model = Shipment
        fields = [*CONTACT_FIELDS, *PARCEL_FIELDS]

    def validate(self, attrs):
        dims = [attrs.get(k) for k in ('length_cm', 'width_cm', 'height_cm')]
        if any(dims) and not all(dims):
            raise serializers.ValidationError('Provide length, width and height together, or none of them.')
        try:
            attrs['_quote'] = pricing.get_quote(
                origin=attrs['origin'], destination=attrs['destination'],
                service_type=attrs.get('service_type', Shipment.ServiceType.STANDARD),
                weight_kg=attrs.get('weight_kg', 1),
                length_cm=attrs.get('length_cm'), width_cm=attrs.get('width_cm'), height_cm=attrs.get('height_cm'),
            )
        except pricing.PricingError as exc:
            raise serializers.ValidationError({'service_type': str(exc)})
        return attrs

    def create(self, validated_data):
        quote = validated_data.pop('_quote')
        return Shipment.objects.create(
            **validated_data,
            price=quote['total'],
            currency=quote['currency'],
            estimated_delivery=quote['estimated_delivery'],
        )


class ShipmentUpdateSerializer(serializers.ModelSerializer):
    """What staff may edit after booking. Weight, route and price are locked because they set the price."""

    class Meta:
        model = Shipment
        fields = [*CONTACT_FIELDS, 'description', 'estimated_delivery']


class StatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Shipment.Status.choices)
    location = serializers.CharField(required=False, allow_blank=True, max_length=255, default='')
    note = serializers.CharField(required=False, allow_blank=True, default='')


class BulkStatusSerializer(StatusUpdateSerializer):
    tracking_ids = serializers.ListField(
        child=serializers.CharField(max_length=20), allow_empty=False, max_length=100,
    )


class AssignCourierSerializer(serializers.Serializer):
    courier = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(profile__role=Profile.Role.COURIER, is_active=True),
        allow_null=True,
        help_text='ID of a courier user, or null to unassign.',
    )


# ---------- public tracking ----------

class PublicHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TrackingHistory
        fields = ['status', 'location', 'timestamp']  # no internal notes or staff names


class PublicTrackingSerializer(serializers.ModelSerializer):
    """The only shipment data an anonymous visitor can see: no names, phones, addresses or prices."""
    history = PublicHistorySerializer(many=True, read_only=True)
    last_updated = serializers.DateTimeField(source='updated_at', read_only=True)

    class Meta:
        model = Shipment
        fields = [
            'tracking_id', 'status', 'origin', 'destination', 'service_type',
            'estimated_delivery', 'delivered_at', 'last_updated', 'history',
        ]
        read_only_fields = fields


# ---------- quotes ----------

class QuoteRequestSerializer(serializers.Serializer):
    origin = serializers.CharField(max_length=255)
    destination = serializers.CharField(max_length=255)
    service_type = serializers.ChoiceField(choices=Shipment.ServiceType.choices, default='standard')
    weight_kg = serializers.DecimalField(max_digits=8, decimal_places=2, min_value=0.01)
    length_cm = serializers.IntegerField(required=False, min_value=1, allow_null=True)
    width_cm = serializers.IntegerField(required=False, min_value=1, allow_null=True)
    height_cm = serializers.IntegerField(required=False, min_value=1, allow_null=True)

    def validate(self, attrs):
        dims = [attrs.get(k) for k in ('length_cm', 'width_cm', 'height_cm')]
        if any(dims) and not all(dims):
            raise serializers.ValidationError('Provide length, width and height together, or none of them.')
        return attrs


class QuoteResponseSerializer(serializers.Serializer):
    service_type = serializers.CharField()
    scope = serializers.CharField()
    chargeable_weight_kg = serializers.DecimalField(max_digits=8, decimal_places=2)
    base_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    weight_charge = serializers.DecimalField(max_digits=10, decimal_places=2)
    total = serializers.DecimalField(max_digits=12, decimal_places=2)
    currency = serializers.CharField()
    estimated_days = serializers.IntegerField()
    estimated_delivery = serializers.DateField()


# ---------- accounts ----------

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})
    phone = serializers.CharField(required=False, allow_blank=True, max_length=30)

    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'phone']
        extra_kwargs = {'email': {'required': True}}

    def validate(self, attrs):
        candidate = User(username=attrs.get('username', ''), email=attrs.get('email', ''))
        validate_password(attrs['password'], user=candidate)
        return attrs

    def create(self, validated_data):
        phone = validated_data.pop('phone', '')
        user = User.objects.create_user(**validated_data)  # always a customer, never staff
        if phone:
            user.profile.phone = phone
            user.profile.save(update_fields=['phone'])
        return user


class UserSerializer(serializers.ModelSerializer):
    role = serializers.SerializerMethodField()
    phone = serializers.CharField(source='profile.phone', read_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'role', 'phone']

    def get_role(self, user):
        from .permissions import get_role
        return get_role(user)


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ['reference', 'provider', 'amount', 'currency', 'status', 'paid_at', 'created_at']
        read_only_fields = fields