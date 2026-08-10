"""Domain errors, mapped to HTTP status codes at the API edge."""

from __future__ import annotations


class DomainError(Exception):
    """Base for expected, caller-facing failures."""


class Conflict(DomainError):
    """A uniqueness or state conflict (maps to 409)."""


class NotFound(DomainError):
    """A resource the caller may not see or that does not exist (maps to 404)."""


class Forbidden(DomainError):
    """Authenticated but not allowed (maps to 403)."""


class AuthError(DomainError):
    """Bad or missing credentials (maps to 401)."""


class QuotaExceeded(DomainError):
    """A tenant limit would be exceeded by this request (maps to 413)."""
