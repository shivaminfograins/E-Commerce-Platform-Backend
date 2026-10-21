from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError
from django.http import Http404
from .models import VendorProduct, VendorProductVariant, VendorInventoryMovement

def record_inventory_movement(
    user,
    vendor_product_id,
    vendor_product_variant_id=None,
    movement_type=None,
    quantity=0,
    reason=None,
    note="",
    reference_type=None,
    reference_id=None
):
    if movement_type not in ["IN", "OUT", "ADJUSTMENT"]:
        raise ValidationError({"movement_type": "Invalid movement type."})

    # Check sensible movement_type/reason combinations
    allowed_reasons = {
        "IN": ["RESTOCK", "INITIAL_STOCK", "CUSTOMER_RETURN", "OTHER"],
        "OUT": ["DAMAGED", "LOST", "OTHER"],
        "ADJUSTMENT": ["STOCK_COUNT_CORRECTION", "MANUAL_ADJUSTMENT", "OTHER"]
    }
    if reason not in allowed_reasons[movement_type]:
        raise ValidationError({"reason": f"Invalid reason '{reason}' for movement type '{movement_type}'."})

    # Validate quantities (API inputs must always be positive for IN/OUT)
    if movement_type in ["IN", "OUT"]:
        if quantity <= 0:
            raise ValidationError({"quantity": "Quantity must be greater than zero for IN or OUT movements."})
    elif movement_type == "ADJUSTMENT":
        if quantity == 0:
            raise ValidationError({"quantity": "Quantity cannot be zero for ADJUSTMENT."})

    # Determine internal delta
    if movement_type == "IN":
        delta = quantity
    elif movement_type == "OUT":
        delta = -quantity
    else:  # ADJUSTMENT
        delta = quantity

    vendor_profile = user.vendor_profile

    with transaction.atomic():
        # Load and lock simple product
        # Return 404 if product is not owned by requesting vendor
        try:
            vendor_product = VendorProduct.objects.select_for_update().get(pk=vendor_product_id, vendor=vendor_profile)
        except VendorProduct.DoesNotExist:
            raise Http404("No VendorProduct matches the given query.")

        # Verify suspended status
        if vendor_product.status == "SUSPENDED":
            raise ValidationError({"detail": "Inventory mutations are blocked for suspended listings."})

        if vendor_product_variant_id:
            # Variant product flow
            try:
                variant = VendorProductVariant.objects.select_for_update().get(pk=vendor_product_variant_id, vendor_product=vendor_product)
            except VendorProductVariant.DoesNotExist:
                raise Http404("No VendorProductVariant matches the given query.")

            previous_stock = variant.stock or 0
            new_stock = previous_stock + delta
            if new_stock < 0:
                raise ValidationError({"detail": f"Insufficient stock. Available: {previous_stock}. Requested: {abs(delta)}."})

            # Mathematical check
            if previous_stock + delta != new_stock:
                raise ValidationError({"detail": "Mathematical consistency check failed: previous_stock + quantity == new_stock does not hold."})

            variant.stock = new_stock
            variant.save()

            # Snapshots
            product_name_snapshot = vendor_product.product.name
            variant_name_snapshot = variant.display_name if variant else ''

            movement = VendorInventoryMovement.objects.create(
                vendor=vendor_profile,
                vendor_product=vendor_product,
                vendor_product_variant=variant,
                product_name_snapshot=product_name_snapshot,
                variant_name_snapshot=variant_name_snapshot,
                movement_type=movement_type,
                quantity=delta,
                previous_stock=previous_stock,
                new_stock=new_stock,
                reason=reason,
                note=note,
                reference_type=reference_type,
                reference_id=reference_id,
                created_by=user
            )
            return movement
        else:
            # Simple product flow
            if vendor_product.product.variants.exists():
                raise ValidationError({"detail": "Price and stock must be set at the variant level, not the product level."})

            previous_stock = vendor_product.stock or 0
            new_stock = previous_stock + delta
            if new_stock < 0:
                raise ValidationError({"detail": f"Insufficient stock. Available: {previous_stock}. Requested: {abs(delta)}."})

            # Mathematical check
            if previous_stock + delta != new_stock:
                raise ValidationError({"detail": "Mathematical consistency check failed: previous_stock + quantity == new_stock does not hold."})

            vendor_product.stock = new_stock
            vendor_product.save()

            # Snapshots
            product_name_snapshot = vendor_product.product.name
            variant_name_snapshot = ""

            movement = VendorInventoryMovement.objects.create(
                vendor=vendor_profile,
                vendor_product=vendor_product,
                vendor_product_variant=None,
                product_name_snapshot=product_name_snapshot,
                variant_name_snapshot=variant_name_snapshot,
                movement_type=movement_type,
                quantity=delta,
                previous_stock=previous_stock,
                new_stock=new_stock,
                reason=reason,
                note=note,
                reference_type=reference_type,
                reference_id=reference_id,
                created_by=user
            )
            return movement
