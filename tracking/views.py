import json
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Count, Prefetch, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import payments, pricing
from .labels import build_label_pdf
from .models import InvalidTransition, Payment, Profile, ProofOfDelivery, Shipment, TrackingHistory
from .permissions import IsCustomerOrStaff, IsStaffOrCourier, IsStaffRole, Role, get_role
from .serializers import (
    AssignCourierSerializer, BulkStatusSerializer, PaymentSerializer, ProofOfDeliverySerializer,
    PublicTrackingSerializer, QuoteRequestSerializer, QuoteResponseSerializer, RegisterSerializer,
    ShipmentCreateSerializer, ShipmentSerializer, ShipmentUpdateSerializer, StatusUpdateSerializer,
    TrackingHistorySerializer, UserSerializer,
)

logger = logging.getLogger(__name__)
User = get_user_model()

DETAIL = {'type': 'object', 'properties': {'detail': {'type': 'string'}}}


# ---------------------------------------------------------------- shipments

class ShipmentViewSet(viewsets.ModelViewSet):
    """Create, view and manage shipments.

    What you can see and do depends on your role:
    customers book and see their own shipments; couriers see and update the ones
    assigned to them; staff can do everything.
    """

    serializer_class = ShipmentSerializer
    lookup_field = 'tracking_id'
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'origin', 'destination', 'service_type', 'payment_status', 'courier']
    search_fields = ['tracking_id', 'sender_name', 'receiver_name', 'receiver_phone']
    ordering_fields = ['created_at', 'status', 'price', 'estimated_delivery']

    def get_queryset(self):
        qs = Shipment.objects.select_related('courier', 'customer', 'proof_of_delivery').prefetch_related(
            Prefetch('history', queryset=TrackingHistory.objects.select_related('updated_by'))
        )
        role = get_role(self.request.user)
        if role == Role.STAFF:
            return qs
        if role == Role.COURIER:
            return qs.filter(courier=self.request.user)
        if role == Role.CUSTOMER:
            return qs.filter(customer=self.request.user)
        return qs.none()

    def get_permissions(self):
        if self.action in ('update', 'partial_update', 'destroy', 'assign', 'mark_paid', 'bulk_status'):
            classes = [IsStaffRole]
        elif self.action in ('create', 'pay', 'cancel'):
            classes = [IsCustomerOrStaff]
        elif self.action in ('update_status', 'proof_of_delivery'):
            classes = [IsStaffOrCourier]
        else:
            classes = [IsAuthenticated]
        return [cls() for cls in classes]

    def get_serializer_class(self):
        return {
            'create': ShipmentCreateSerializer,
            'update': ShipmentUpdateSerializer,
            'partial_update': ShipmentUpdateSerializer,
            'update_status': StatusUpdateSerializer,
            'bulk_status': BulkStatusSerializer,
            'assign': AssignCourierSerializer,
            'proof_of_delivery': ProofOfDeliverySerializer,
            'history': TrackingHistorySerializer,
        }.get(self.action, ShipmentSerializer)

    # -- create / update return the full read representation --
    @extend_schema(request=ShipmentCreateSerializer, responses={201: ShipmentSerializer})
    def create(self, request, *args, **kwargs):
        serializer = ShipmentCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        customer = request.user if get_role(request.user) == Role.CUSTOMER else None
        shipment = serializer.save(customer=customer)
        return Response(ShipmentSerializer(shipment, context=self.get_serializer_context()).data,
                        status=status.HTTP_201_CREATED)

    @extend_schema(request=ShipmentUpdateSerializer, responses=ShipmentSerializer)
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        shipment = self.get_object()
        serializer = ShipmentUpdateSerializer(shipment, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ShipmentSerializer(shipment, context=self.get_serializer_context()).data)

    # -- status workflow --
    @extend_schema(request=StatusUpdateSerializer, responses={200: ShipmentSerializer, 400: OpenApiResponse(response=DETAIL)})
    @action(detail=True, methods=['post'], url_path='status')
    def update_status(self, request, tracking_id=None):
        """Move a shipment to its next status, optionally with a location and an internal note."""
        shipment = self.get_object()
        serializer = StatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data['status'] == Shipment.Status.CANCELLED and get_role(request.user) != Role.STAFF:
            return Response({'detail': 'Only staff can cancel a shipment.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            shipment.set_status(data['status'], user=request.user, location=data['location'], note=data['note'])
        except InvalidTransition as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ShipmentSerializer(shipment, context=self.get_serializer_context()).data)

    @extend_schema(request=BulkStatusSerializer, responses={200: OpenApiResponse(response=DETAIL)})
    @action(detail=False, methods=['post'], url_path='bulk-status')
    def bulk_status(self, request):
        """Apply one status update to many shipments (e.g. a whole van arriving at the hub).
        Each shipment succeeds or fails on its own; the response lists the outcome of each."""
        serializer = BulkStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        shipments = {s.tracking_id: s for s in Shipment.objects.filter(tracking_id__in=data['tracking_ids'])}
        results = []
        for tracking_id in data['tracking_ids']:
            shipment = shipments.get(tracking_id)
            if shipment is None:
                results.append({'tracking_id': tracking_id, 'ok': False, 'error': 'Shipment not found.'})
                continue
            try:
                with transaction.atomic():
                    shipment.set_status(data['status'], user=request.user,
                                        location=data['location'], note=data['note'])
                results.append({'tracking_id': tracking_id, 'ok': True, 'status': shipment.status})
            except InvalidTransition as exc:
                results.append({'tracking_id': tracking_id, 'ok': False, 'error': str(exc)})
        succeeded = sum(r['ok'] for r in results)
        return Response({'updated': succeeded, 'failed': len(results) - succeeded, 'results': results})

    @extend_schema(request=None, responses={200: ShipmentSerializer, 400: OpenApiResponse(response=DETAIL)})
    @action(detail=True, methods=['post'])
    def cancel(self, request, tracking_id=None):
        """Cancel a shipment that hasn't been picked up yet. If it was already paid it is flagged for refund."""
        shipment = self.get_object()
        try:
            shipment.set_status(Shipment.Status.CANCELLED, user=request.user, note=str(request.data.get('note', '')))
        except InvalidTransition as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ShipmentSerializer(shipment, context=self.get_serializer_context()).data)

    @extend_schema(request=AssignCourierSerializer, responses=ShipmentSerializer)
    @action(detail=True, methods=['post'])
    def assign(self, request, tracking_id=None):
        """Assign (or unassign, with null) a courier."""
        shipment = self.get_object()
        if not shipment.is_active:
            return Response({'detail': 'This shipment is closed and can no longer be assigned.'},
                            status=status.HTTP_400_BAD_REQUEST)
        serializer = AssignCourierSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shipment.courier = serializer.validated_data['courier']
        shipment.save(update_fields=['courier', 'updated_at'])
        return Response(ShipmentSerializer(shipment, context=self.get_serializer_context()).data)

    @extend_schema(request=ProofOfDeliverySerializer, responses={200: ShipmentSerializer, 400: OpenApiResponse(response=DETAIL)})
    @action(detail=True, methods=['post'], url_path='proof-of-delivery',
            parser_classes=[MultiPartParser, FormParser, JSONParser])
    def proof_of_delivery(self, request, tracking_id=None):
        """Record who received the parcel (and optionally a photo). This also marks the shipment Delivered."""
        shipment = self.get_object()
        serializer = ProofOfDeliverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        location = data.pop('location', '')
        if hasattr(shipment, 'proof_of_delivery'):
            return Response({'detail': 'Proof of delivery has already been recorded.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            with transaction.atomic():  # if the status change is refused, the proof is rolled back too
                ProofOfDelivery.objects.create(shipment=shipment, recorded_by=request.user, **data)
                shipment.set_status(Shipment.Status.DELIVERED, user=request.user, location=location,
                                    note=f"Received by {data['recipient_name']}")
        except InvalidTransition as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(ShipmentSerializer(shipment, context=self.get_serializer_context()).data)

    @extend_schema(responses=TrackingHistorySerializer(many=True))
    @action(detail=True, methods=['get'])
    def history(self, request, tracking_id=None):
        shipment = self.get_object()
        return Response(TrackingHistorySerializer(shipment.history.all(), many=True).data)

    # -- payments --
    @extend_schema(request=None, responses={200: OpenApiResponse(response=DETAIL)})
    @action(detail=True, methods=['post'])
    def pay(self, request, tracking_id=None):
        """Start an online (Paystack) payment. Returns a URL to send the customer to."""
        shipment = self.get_object()
        if shipment.payment_status == Shipment.PaymentStatus.PAID:
            return Response({'detail': 'This shipment has already been paid for.'}, status=status.HTTP_400_BAD_REQUEST)
        if shipment.status == Shipment.Status.CANCELLED:
            return Response({'detail': 'A cancelled shipment cannot be paid for.'}, status=status.HTTP_400_BAD_REQUEST)
        email = request.user.email or shipment.sender_email
        if not email:
            return Response({'detail': 'An email address is required to pay online.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            payment, url = payments.initialize_payment(
                shipment, email, callback_url=settings.PAYMENT_CALLBACK_URL or None
            )
        except payments.PaymentNotConfigured as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except payments.PaymentError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({'authorization_url': url, 'reference': payment.reference, 'amount': payment.amount,
                         'currency': payment.currency})

    @extend_schema(request=None, responses=PaymentSerializer)
    @action(detail=True, methods=['post'], url_path='mark-paid')
    def mark_paid(self, request, tracking_id=None):
        """Record a cash (or other offline) payment taken by staff."""
        shipment = self.get_object()
        if shipment.payment_status == Shipment.PaymentStatus.PAID:
            return Response({'detail': 'This shipment has already been paid for.'}, status=status.HTTP_400_BAD_REQUEST)
        if shipment.status == Shipment.Status.CANCELLED:
            return Response({'detail': 'A cancelled shipment cannot be paid for.'}, status=status.HTTP_400_BAD_REQUEST)
        payment = payments.record_cash_payment(shipment)
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    # -- label --
    @extend_schema(responses={(200, 'application/pdf'): OpenApiResponse(description='A printable 4x6in PDF label')})
    @action(detail=True, methods=['get'])
    def label(self, request, tracking_id=None):
        """Printable shipping label (PDF) with a barcode and a QR code linking to the tracking page."""
        shipment = self.get_object()
        response = HttpResponse(build_label_pdf(shipment), content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{shipment.tracking_id}-label.pdf"'
        return response


# ---------------------------------------------------------------- public endpoints

class PublicTrackView(generics.RetrieveAPIView):
    """Public tracking page data: status and timeline only, with no personal details."""
    serializer_class = PublicTrackingSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'tracking'
    lookup_field = 'tracking_id__iexact'
    lookup_url_kwarg = 'tracking_id'
    queryset = Shipment.objects.prefetch_related('history')


class QuoteView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'quote'

    @extend_schema(request=QuoteRequestSerializer, responses=QuoteResponseSerializer)
    def post(self, request):
        """Get a price and delivery estimate without an account."""
        serializer = QuoteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            quote = pricing.get_quote(**serializer.validated_data)
        except pricing.PricingError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(QuoteResponseSerializer(quote).data)


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(responses={200: OpenApiResponse(response=DETAIL)})
    def get(self, request):
        """Liveness check for hosting platforms and uptime monitors."""
        connection.ensure_connection()
        return Response({'status': 'ok'})


# ---------------------------------------------------------------- accounts

class RegisterView(generics.CreateAPIView):
    """Create a customer account. Staff and courier accounts are created by an admin."""
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    @extend_schema(responses={201: OpenApiResponse(response=DETAIL)})
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        token, _ = Token.objects.get_or_create(user=user)
        return Response({'token': token.key, 'user': UserSerializer(user).data}, status=status.HTTP_201_CREATED)


class LoginView(ObtainAuthToken):
    """Exchange a username and password for an API token."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'

    @extend_schema(responses={200: OpenApiResponse(response=DETAIL)})
    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        token, _ = Token.objects.get_or_create(user=user)
        return Response({'token': token.key, 'user': UserSerializer(user).data})


class MeView(generics.RetrieveAPIView):
    """The signed-in user's profile and role."""
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


# ---------------------------------------------------------------- payments

class PaystackWebhookView(APIView):
    """Receives Paystack's server-to-server payment notifications.

    Requests are authenticated by the HMAC signature header, not by a user token.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=None, responses={200: OpenApiResponse(response=DETAIL)})
    def post(self, request):
        raw_body = request.body
        if not payments.signature_is_valid(raw_body, request.headers.get('x-paystack-signature', '')):
            return Response({'detail': 'Invalid signature.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            event = json.loads(raw_body)
        except ValueError:
            return Response({'detail': 'Invalid payload.'}, status=status.HTTP_400_BAD_REQUEST)
        data = event.get('data') or {}
        if event.get('event') == 'charge.success':
            payment = Payment.objects.filter(reference=data.get('reference')).first()
            if payment is None:
                logger.warning('Webhook for unknown payment reference %s', data.get('reference'))
            else:
                payments.apply_gateway_result(payment, data)
        return Response({'detail': 'ok'})  # always 200 for valid signatures so Paystack doesn't retry


class PaymentVerifyView(APIView):
    """Check a payment with Paystack directly (useful on the page the customer returns to)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=PaymentSerializer)
    def get(self, request, reference):
        qs = Payment.objects.select_related('shipment')
        if get_role(request.user) != Role.STAFF:
            qs = qs.filter(shipment__customer=request.user)
        payment = generics.get_object_or_404(qs, reference=reference)
        if payment.provider == Payment.Provider.PAYSTACK and payment.status == Payment.Status.PENDING:
            try:
                payments.apply_gateway_result(payment, payments.verify_with_gateway(payment))
            except payments.PaymentNotConfigured as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            except payments.PaymentError as exc:
                return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
            payment.refresh_from_db()
        return Response(PaymentSerializer(payment).data)


# ---------------------------------------------------------------- dashboard

class DashboardView(APIView):
    """Operational summary for staff."""
    permission_classes = [IsStaffRole]

    @extend_schema(responses={200: OpenApiResponse(response=DETAIL)})
    def get(self, request):
        today = timezone.localdate()
        shipments = Shipment.objects.all()
        active = shipments.exclude(status__in=Shipment.TERMINAL_STATUSES)

        by_status = {s.value: 0 for s in Shipment.Status}
        by_status.update(dict(shipments.order_by().values_list('status').annotate(n=Count('id'))))

        paid = Payment.objects.filter(status=Payment.Status.SUCCESS)
        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        couriers = (
            User.objects.filter(profile__role=Profile.Role.COURIER)
            .annotate(active=Count(
                'assigned_shipments',
                filter=~Q(assigned_shipments__status__in=Shipment.TERMINAL_STATUSES),
            ))
            .order_by('username').values('id', 'username', 'active')
        )
        return Response({
            'totals': {
                'shipments': shipments.count(),
                'active': active.count(),
                'unassigned': active.filter(courier__isnull=True).count(),
                'overdue': active.filter(estimated_delivery__lt=today).count(),
                'awaiting_payment': active.filter(payment_status=Shipment.PaymentStatus.UNPAID).count(),
                'refunds_pending': shipments.filter(payment_status=Shipment.PaymentStatus.REFUND_PENDING).count(),
            },
            'by_status': by_status,
            'today': {
                'created': shipments.filter(created_at__date=today).count(),
                'delivered': shipments.filter(delivered_at__date=today).count(),
            },
            'revenue': {
                'currency': settings.DEFAULT_CURRENCY,
                'total': str(paid.aggregate(t=Sum('amount'))['t'] or 0),
                'this_month': str(paid.filter(paid_at__gte=month_start).aggregate(t=Sum('amount'))['t'] or 0),
            },
            'couriers': list(couriers),
        })