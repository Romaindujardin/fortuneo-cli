"""Erreurs typées du sidecar et codes de sortie CLI."""

from __future__ import annotations

from typing import Any


class SidecarError(Exception):
    """Erreur attendue, sérialisable en JSON."""

    code = "SIDECAR_ERROR"
    exit_code = 1

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class UsageError(SidecarError):
    code = "USAGE_ERROR"
    exit_code = 2


class CredentialsError(SidecarError):
    code = "CREDENTIALS_MISSING"
    exit_code = 3


class PasswordRequired(CredentialsError):
    code = "PASSWORD_REQUIRED"


class AuthenticationError(SidecarError):
    code = "AUTH_FAILED"
    exit_code = 4


class OTPRequired(AuthenticationError):
    code = "OTP_REQUIRED"


class AccountLocked(AuthenticationError):
    code = "ACCOUNT_LOCKED"


class ActionRequired(AuthenticationError):
    code = "ACTION_REQUIRED"


class UnsupportedAuth(AuthenticationError):
    code = "UNSUPPORTED_AUTH"


class UpstreamError(SidecarError):
    code = "UPSTREAM_ERROR"
    exit_code = 5


class ModuleMissing(SidecarError):
    code = "MODULE_MISSING"
    exit_code = 6


class NotFoundError(SidecarError):
    code = "NOT_FOUND"
    exit_code = 7


class AmbiguousAccount(NotFoundError):
    code = "AMBIGUOUS_ACCOUNT"
