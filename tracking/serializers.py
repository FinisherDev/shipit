from rest_framework import serializers
from .models import Shipment, TrackingHistory

class TrackingHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TrackingHistory
        fields = ['status', 'timestamp']

class ShipmentSerializer(serializers.ModelSerializer):
    history = TrackingHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Shipment
        fields = '__all__'