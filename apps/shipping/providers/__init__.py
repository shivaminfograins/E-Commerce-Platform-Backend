from apps.shipping.exceptions import ShippingProviderNotFoundError
from apps.shipping.providers.base import BaseShippingProvider
from apps.shipping.providers.shiprocket import ShiprocketProvider

__all__ = ["BaseShippingProvider", "ShiprocketProvider", "get_shipping_provider"]


def get_shipping_provider(provider_name="shiprocket", **kwargs):
    """
    Factory function to retrieve a shipping provider client instance.
    """
    if not provider_name or provider_name.lower() == "shiprocket":
        return ShiprocketProvider(**kwargs)

    raise ShippingProviderNotFoundError(
        f"Unsupported shipping provider: '{provider_name}'"
    )
