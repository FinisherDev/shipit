from rest_framework import viewsets, status
from rest_framework.response import Response 
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend
from django.http import QueryDict
from .models import Shipment, TrackingHistory
from .serializers import ShipmentSerializer, TrackingHistorySerializer

# Create your views here.

class ShipmentViewSet(viewsets.ModelViewSet):
    queryset = Shipment.objects.all()
    serializer_class = ShipmentSerializer
    lookup_field = 'tracking_id'
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'origin', 'destination']
    search_fields = ['tracking_id', 'sender_name', 'receiver_name']
    ordering_fields = ['created_at', 'status']

    def update(self, request, *args, **kwargs):
        shipment = self.get_object()
        old_status = shipment.status
        put = request.body.decode("utf-8")
        put_list = put.split("Content-Disposition: form-data;")
        status_list = put_list[-1].split("\r\n")
        new_status = status_list[2]
        if old_status != new_status:
            Shipment.objects.filter(tracking_id = shipment).update(status = new_status)
            TrackingHistory.objects.create(shipment=shipment, status=new_status)  

        response = super().update(request, *args, **kwargs)
        return response
    
    @action(detail=True, methods=['get'])
    def history(self, request, tracking_id=None):
        shipment = self.get_object()
        history = shipment.history.all()
        serializer = TrackingHistorySerializer(history, many=True)
        return Response(serializer.data)

