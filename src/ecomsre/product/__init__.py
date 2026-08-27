"""Self-hosted read-only EcomSRE Product surface."""

from ecomsre.product.app import create_app
from ecomsre.product.settings import ProductSettingsV1

__all__ = ("ProductSettingsV1", "create_app")
