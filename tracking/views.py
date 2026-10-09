from rest_framework import viewsets, status
from rest_framework.response import Response 
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend

from .models import Shipment, TrackingHistory
from .serializers import ShipmentSerializer, TrackingHistorySerializer

# Create your views here.

class ShipmentViewSet(viewsets.ModelViewSet):
    queryset = Shipment.objects.prefetch_related('history').order_by('-created_at')
    serializer_class = ShipmentSerializer
    lookup_field = 'tracking_id'
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'origin', 'destination']
    search_fields = ['tracking_id', 'sender_name', 'receiver_name']
    ordering_fields = ['created_at', 'status']
    
    @action(detail=True, methods=['get'])
    def history(self, request, tracking_id=None):
        shipment = self.get_object()
        history = shipment.history.all()
        serializer = TrackingHistorySerializer(history, many=True)
        return Response(serializer.data)

