"""Typed domain exceptions for Sweet Home 3D CLI (PAC-1 A04, R28-P1-05)."""

class SweetHome3DError(Exception):
    """Base exception for all domain errors."""
    pass


class ValidationRejectedError(ValueError, SweetHome3DError):
    """Raised when an architectural input or model fails domain validation rules."""
    pass


class EntityConflictError(ValueError, SweetHome3DError):
    """Raised when an entity ID already exists or a transaction conflict occurs."""
    pass


class UnsupportedFeatureError(ValueError, SweetHome3DError):
    """Raised when an unsupported dialect, format, or architectural feature is requested."""
    pass
