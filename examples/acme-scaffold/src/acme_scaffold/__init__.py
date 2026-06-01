"""ACME Scaffold — example white-label dialect on data-product-forge-custom-scaffold."""

from .dialect import ACME_DIALECT, get_extension_schema, register, validate

__all__ = ["ACME_DIALECT", "register", "validate", "get_extension_schema"]
