from rest_framework import serializers
from apps.shipping.models import Shipment, ShipmentStatusHistory


class ShipmentStatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ShipmentStatusHistory
        fields = [
            "id",
            "status",
            "location",
            "remarks",
            "created_at",
        ]


class ShipmentSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    status_history = ShipmentStatusHistorySerializer(many=True, read_only=True)
    vendor_order_number = serializers.CharField(source="vendor_order.vendor_order_number", read_only=True)

    class Meta:
        model = Shipment
        fields = [
            "id",
            "vendor_order_number",
            "provider_name",
            "provider_order_id",
            "provider_shipment_id",
            "courier_id",
            "courier_name",
            "awb_code",
            "status",
            "status_display",
            "weight_kg",
            "length_cm",
            "width_cm",
            "height_cm",
            "shipping_cost",
            "pickup_location_name",
            "label_url",
            "manifest_url",
            "tracking_url",
            "pickup_scheduled_at",
            "shipped_at",
            "delivered_at",
            "metadata",
            "created_at",
            "updated_at",
            "status_history",
        ]
        read_only_fields = fields
