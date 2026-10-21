from django.contrib import admin
from .models import Shipment, ShipmentStatusHistory


class ShipmentStatusHistoryInline(admin.TabularInline):
    model = ShipmentStatusHistory
    extra = 0
    readonly_fields = ["status", "location", "remarks", "raw_payload", "created_at"]
    can_delete = False


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "vendor_order",
        "provider_name",
        "provider_order_id",
        "courier_name",
        "awb_code",
        "status",
        "weight_kg",
        "shipping_cost",
        "created_at",
    ]
    list_filter = ["provider_name", "status", "created_at"]
    search_fields = [
        "vendor_order__vendor_order_number",
        "provider_order_id",
        "provider_shipment_id",
        "awb_code",
        "courier_name",
        "pickup_location_name",
    ]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [ShipmentStatusHistoryInline]


@admin.register(ShipmentStatusHistory)
class ShipmentStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ["id", "shipment", "status", "location", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["shipment__vendor_order__vendor_order_number", "shipment__awb_code", "remarks"]
    readonly_fields = ["shipment", "status", "location", "remarks", "raw_payload", "created_at"]
