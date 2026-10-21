class ShippingProviderError(Exception):
    """Base exception for all shipping provider errors."""
    pass


class ShippingConfigurationError(ShippingProviderError):
    """Raised when provider configuration or credentials are missing or invalid."""
    pass


class ShippingValidationError(ShippingProviderError):
    """Raised when request inputs (pincodes, package dimensions, weights) are invalid."""
    pass


class ShippingConcurrencyError(ShippingProviderError):
    """Raised when a concurrent shipment creation attempt is already in progress for an order."""
    pass


class ShippingProviderNotFoundError(ShippingProviderError):
    """Raised when an unknown or unsupported provider is requested."""
    pass


class ShiprocketAPIError(ShippingProviderError):
    """Base exception for Shiprocket API response or connection failures."""

    def __init__(self, message="Shiprocket API error occurred.", status_code=None, raw_response=None):
        super().__init__(message)
        self.status_code = status_code
        self.raw_response = raw_response


class ShiprocketAuthenticationError(ShiprocketAPIError):
    """Raised when Shiprocket authentication fails."""

    def __init__(self, message="Shiprocket authentication failed.", status_code=None, raw_response=None):
        super().__init__(message, status_code=status_code, raw_response=raw_response)


class ShiprocketServiceabilityError(ShiprocketAPIError):
    """Raised when Shiprocket serviceability API returns an unexpected error or malformed structure."""

    def __init__(self, message="Shiprocket serviceability check failed.", status_code=None, raw_response=None):
        super().__init__(message, status_code=status_code, raw_response=raw_response)


class ShiprocketTimeoutError(ShiprocketAPIError):
    """Raised when a Shiprocket API HTTP request times out."""

    def __init__(self, message="Shiprocket API request timed out.", status_code=None, raw_response=None):
        super().__init__(message, status_code=status_code, raw_response=raw_response)
