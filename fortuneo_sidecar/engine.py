"""Moteur d'accès Fortuneo via Woob.

Le moteur encapsule :
- le chargement du module Woob vendorisé ;
- la persistance de l'état (cookies + formulaire 2FA) dans un fichier privé ;
- la gestion du BrowserQuestion Woob pour le code SMS ;
- la conversion des objets Woob en charges JSON.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Any, Callable

import requests
from woob.capabilities.base import NotAvailable, NotLoaded
from woob.exceptions import (
    ActionNeeded,
    AuthMethodNotImplemented,
    BrowserIncorrectPassword,
    BrowserQuestion,
    BrowserUnavailable,
    BrowserUserBanned,
    ModuleLoadError,
    NeedInteractiveFor2FA,
)

from .credentials import Credentials, credentials_secrets, redact
from .errors import (
    AccountLocked,
    ActionRequired,
    AmbiguousAccount,
    AuthenticationError,
    ModuleMissing,
    NotFoundError,
    OTPRequired,
    SidecarError,
    UnsupportedAuth,
    UpstreamError,
    UsageError,
)
from .otp import OTPProvider
from .paths import SidecarPaths
from .serialize import (
    POSITION_KINDS,
    TRANSACTION_KINDS,
    account_kind,
    clean_value,
    filter_transactions,
    serialize_account,
    serialize_investment,
    serialize_transactions,
)

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return list(value)


def _has_link(account: Any, attribute: str) -> bool:
    value = getattr(account, attribute, None)
    return value is not None and value is not NotAvailable and value is not NotLoaded


def _parse_date(value: str | None, field_name: str) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise UsageError(f"{field_name} doit être au format YYYY-MM-DD (reçu: {value!r}).") from exc


class FortuneoEngine:
    """Session Fortuneo réutilisable sur plusieurs comptes."""

    def __init__(
        self,
        credentials: Credentials,
        paths: SidecarPaths,
        *,
        otp_provider: OTPProvider | None = None,
        backend_factory: Callable[[], Any] | None = None,
        include_raw: bool = False,
        max_otp_attempts: int = 3,
        persist_state: bool = False,
    ) -> None:
        self.credentials = credentials
        self.paths = paths
        self.otp_provider = otp_provider
        self.backend_factory = backend_factory
        self.include_raw = include_raw
        self.max_otp_attempts = max(1, max_otp_attempts)
        self.persist_state = persist_state
        self._secrets = credentials_secrets(credentials)
        self._backend: Any | None = None
        self._woob: Any | None = None
        self._accounts: list[Any] | None = None
        self._otp_attempted = False

    # ------------------------------------------------------------------ session
    def __enter__(self) -> "FortuneoEngine":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _load_default_backend(self) -> Any:
        modules_path = self.paths.vendor_modules_dir
        module_file = modules_path / "fortuneo" / "module.py"
        if not module_file.exists():
            raise ModuleMissing(
                f"Module Woob Fortuneo introuvable dans {modules_path}. "
                "Vérifiez le répertoire `fortuneo_sidecar/vendor/woob_modules` "
                "ou FORTUNEO_SIDECAR_MODULES."
            )

        try:
            from woob.core.woob import WoobBase
            from woob.tools.storage import StandardStorage
        except ImportError as exc:
            raise ModuleMissing("Woob n'est pas installé dans l'environnement du sidecar.") from exc

        self.paths.ensure()
        if self.persist_state:
            self.paths.check_state_file()
            storage = StandardStorage(str(self.paths.state_file))
        else:
            storage = None
        woob = WoobBase(modules_path=str(modules_path), storage=storage)
        params = {
            "login": self.credentials.login,
            "password": self.credentials.password,
            # Non-None => Woob autorise les challenges 2FA et lève BrowserQuestion.
            "request_information": {},
        }
        backend = woob.load_backend("fortuneo", "fortuneo", params=params, storage=storage)
        self._woob = woob
        return backend

    def _get_backend(self) -> Any:
        if self._backend is None:
            try:
                if self.backend_factory is not None:
                    self._backend = self.backend_factory()
                else:
                    self._backend = self._load_default_backend()
            except ModuleLoadError as exc:
                raise ModuleMissing(f"Impossible de charger le module Woob Fortuneo: {exc}") from exc
        return self._backend

    def close(self) -> None:
        backend = self._backend
        self._backend = None
        try:
            if backend is not None:
                # Nettoie les secrets transitoires *avant* deinit(), car Woob
                # sérialise l'état du navigateur (dont sms_form) à ce moment.
                self._scrub_transient_auth(backend)
                try:
                    backend.deinit()
                except Exception as exc:  # pragma: no cover - dépend de Woob
                    logger.warning("Impossible de sauvegarder l'état Woob: %s", redact(str(exc), self._secrets))
        finally:
            self._woob = None
            self._accounts = None
            if self.persist_state:
                self._secure_state_file()
            else:
                self._remove_state_file()

    @staticmethod
    def _scrub_transient_auth(backend: Any) -> None:
        """Efface les codes d'authentification et formulaires 2FA en mémoire."""
        browser = getattr(backend, "_browser", None)
        if browser is not None:
            for attribute in ("sms_form", "add_recipient_form", "execute_transfer_form"):
                if hasattr(browser, attribute):
                    setattr(browser, attribute, None)
            if hasattr(browser, "need_reload_state"):
                browser.need_reload_state = False

        config = getattr(backend, "config", None)
        getter = getattr(config, "get", None)
        if not callable(getter):
            return
        for field_id in ("code", "sca_code"):
            holder = getter(field_id)
            setter = getattr(holder, "set", None)
            if callable(setter):
                try:
                    setter(None)
                except (TypeError, ValueError):  # pragma: no cover - Value requis
                    pass

    def _secure_state_file(self) -> None:
        path = self.paths.state_file
        if not path.exists() and not path.is_symlink():
            return
        try:
            if path.is_symlink():
                path.unlink()
                logger.warning(
                    "Le fichier d'état était un lien symbolique; il a été supprimé: %s", path
                )
                return
            if not path.is_file():
                logger.warning("Le chemin d'état n'est pas un fichier régulier: %s", path)
                return
            os.chmod(path, 0o600)
        except OSError as exc:  # pragma: no cover
            logger.warning("Impossible de restreindre les permissions de l'état: %s", exc)

    def _remove_state_file(self) -> None:
        path = self.paths.state_file
        if not path.exists() and not path.is_symlink():
            return
        try:
            path.unlink()
        except OSError as exc:  # pragma: no cover
            logger.warning("Impossible de supprimer le fichier d'état: %s", exc)

    def _answer_question(self, backend: Any, question: BrowserQuestion) -> None:
        if self.otp_provider is None:
            raise OTPRequired(
                "Fortuneo demande une validation 2FA. "
                "Fournissez FORTUNEO_OTP, --otp-file, ou relancez dans un terminal."
            )
        for field in question.fields:
            field_id = getattr(field, "id", None) or "code"
            if field_id not in {"code", "sca_code"}:
                raise UnsupportedAuth(
                    f"Woob demande une information interactive non gérée: {field_id!r}. "
                    "Le sidecar ne prend en charge que le code SMS Fortuneo."
                )
            holder = backend.config.get(field_id) if hasattr(backend.config, "get") else None
            if holder is None:
                raise OTPRequired(
                    f"Woob demande le champ {field_id!r}, absent de la configuration du backend."
                )
            code = self.otp_provider.get_code(
                field_id=field_id,
                label=getattr(field, "label", None) or getattr(field, "description", None),
            )
            holder.set(code)
            self._otp_attempted = True

    def _after_success(self, backend: Any) -> None:
        # Évite de conserver un formulaire 2FA ou un code SCA obsolète.
        self._scrub_transient_auth(backend)

    def _redact(self, text: Any) -> str:
        return redact(str(text), self._secrets)

    def _call(self, operation: str, function: Callable[[Any], Any]) -> Any:
        backend = self._get_backend()
        last_question: BrowserQuestion | None = None
        self._otp_attempted = False

        for attempt in range(self.max_otp_attempts):
            try:
                result = function(backend)
                self._after_success(backend)
                return result
            except BrowserQuestion as question:
                last_question = question
                if attempt >= self.max_otp_attempts - 1:
                    raise OTPRequired(
                        "Le code de validation n'a pas été accepté après plusieurs tentatives."
                    ) from question
                self._answer_question(backend, question)
            except NeedInteractiveFor2FA as exc:
                raise OTPRequired(
                    "Fortuneo demande une validation 2FA. "
                    "Relancez le sidecar en mode interactif ou fournissez un code SMS."
                ) from exc
            except BrowserUserBanned as exc:
                raise AccountLocked(
                    "Compte Fortuneo temporairement bloqué après plusieurs codes erronés. "
                    "Consultez le site Fortuneo avant de réessayer."
                ) from exc
            except BrowserIncorrectPassword as exc:
                if self._otp_attempted:
                    raise AuthenticationError(
                        "Code SMS incorrect ou expiré. Demandez un nouveau code et réessayez."
                    ) from exc
                detail = self._redact(str(exc).strip())
                message = "Identifiant ou mot de passe Fortuneo invalide, ou compte bloqué."
                if detail:
                    message = f"{message} Détail: {detail}"
                raise AuthenticationError(message) from exc
            except ActionNeeded as exc:
                raise ActionRequired(
                    f"Action requise sur le site Fortuneo: {self._redact(exc)}"
                ) from exc
            except AuthMethodNotImplemented as exc:
                raise UnsupportedAuth(
                    f"Mode d'authentification Fortuneo non pris en charge: {self._redact(exc)}"
                ) from exc
            except BrowserUnavailable as exc:
                raise UpstreamError(
                    f"Le site Fortuneo est indisponible ou a renvoyé une erreur: {self._redact(exc)}"
                ) from exc
            except ModuleLoadError as exc:
                raise ModuleMissing(
                    f"Impossible de charger le module Woob Fortuneo: {self._redact(exc)}"
                ) from exc
            except requests.exceptions.RequestException as exc:
                raise UpstreamError(
                    f"Erreur réseau pendant l'opération {operation!r}: {self._redact(exc)}"
                ) from exc

        raise OTPRequired("Validation 2FA impossible.") from last_question

    # ------------------------------------------------------------------ données
    def accounts(self) -> list[Any]:
        if self._accounts is None:
            backend = self._get_backend()
            iterator = getattr(backend, "iter_accounts", None)
            if iterator is None:
                raise ModuleMissing("Le backend Fortuneo ne fournit pas iter_accounts().")
            self._accounts = self._call("accounts", lambda b: _as_list(b.iter_accounts()))
        return list(self._accounts)

    def find_account(self, identifier: str | None = None) -> Any:
        accounts = self.accounts()
        if identifier is None or str(identifier).strip() == "":
            if not accounts:
                raise NotFoundError("Aucun compte Fortuneo n'a été retourné.")
            if len(accounts) == 1:
                return accounts[0]
            raise UsageError(
                "Plusieurs comptes sont disponibles: précisez --account avec l'id, le numéro, l'IBAN ou le libellé."
            )

        needle = str(identifier).strip()
        needle_cf = needle.casefold()

        def matches(account: Any) -> bool:
            id_value = clean_value(getattr(account, "id", None))
            number = clean_value(getattr(account, "number", None))
            iban = clean_value(getattr(account, "iban", None))
            label = clean_value(getattr(account, "label", None))
            return (
                str(id_value) == needle
                or (number is not None and str(number) == needle)
                or (iban is not None and str(iban).replace(" ", "") == needle.replace(" ", ""))
                or (label is not None and str(label).casefold() == needle_cf)
            )

        found = [account for account in accounts if matches(account)]
        if not found:
            available = ", ".join(str(clean_value(getattr(a, "id", ""))) for a in accounts) or "aucun"
            raise NotFoundError(f"Compte Fortuneo introuvable: {identifier!r} (id disponibles: {available}).")
        if len(found) > 1:
            ids = ", ".join(str(clean_value(getattr(a, "id", ""))) for a in found)
            raise AmbiguousAccount(
                f"Plusieurs comptes correspondent à {identifier!r}: {ids}. Utilisez un id de compte."
            )
        return found[0]

    def transactions(
        self,
        account_id: str | None = None,
        *,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
        include_coming: bool = False,
    ) -> tuple[Any, list[Any], list[str]]:
        account = self.find_account(account_id)
        history = getattr(self._get_backend(), "iter_history", None)
        if history is None:
            raise ModuleMissing("Le backend Fortuneo ne fournit pas iter_history().")

        items = self._call(
            "transactions",
            lambda backend: _as_list(backend.iter_history(account)),
        )

        warnings: list[str] = []
        if include_coming:
            coming = getattr(self._get_backend(), "iter_coming", None)
            if coming is None:
                warnings.append("Le backend ne fournit pas iter_coming(); les opérations à venir sont ignorées.")
            else:
                try:
                    items.extend(
                        self._call("coming", lambda backend: _as_list(backend.iter_coming(account)))
                    )
                except (SidecarError, NotImplementedError) as exc:
                    warnings.append(f"Opérations à venir indisponibles: {self._redact(exc)}")

        selected = filter_transactions(
            items,
            since=_parse_date(since, "--since"),
            until=_parse_date(until, "--until"),
            limit=limit,
        )
        return account, selected, warnings

    def positions(self, account_id: str | None = None) -> tuple[Any, list[Any]]:
        account = self.find_account(account_id)
        backend = self._get_backend()
        investment = getattr(backend, "iter_investment", None) or getattr(backend, "iter_investments", None)
        if investment is None:
            raise ModuleMissing("Le backend Fortuneo ne fournit pas iter_investment().")
        items = self._call(
            "positions",
            lambda b: _as_list(b.iter_investment(account)),
        )
        return account, items

    def profile(self) -> Any:
        backend = self._get_backend()
        get_profile = getattr(backend, "get_profile", None)
        if get_profile is None:
            raise ModuleMissing("Le backend Fortuneo ne fournit pas get_profile().")
        return self._call("profile", lambda b: b.get_profile())

    def export(
        self,
        *,
        include_transactions: bool = True,
        include_positions: bool = True,
        include_coming: bool = False,
        since: str | None = None,
        limit: int | None = None,
    ) -> JsonDict:
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        accounts = self.accounts()
        exported: list[JsonDict] = []
        errors: list[JsonDict] = []
        warnings: list[str] = []

        def record_error(account_id: str, step: str, exc: Exception) -> None:
            if isinstance(exc, AuthenticationError):
                raise exc
            if isinstance(exc, ModuleMissing):
                raise exc
            if isinstance(exc, SidecarError):
                code = exc.code
                message = str(exc)
            else:
                code = "ACCOUNT_ERROR"
                message = f"{type(exc).__name__}: {exc}"
            errors.append(
                {
                    "accountId": account_id,
                    "step": step,
                    "code": code,
                    "message": self._redact(message),
                }
            )

        for account in accounts:
            account_id = str(clean_value(getattr(account, "id", "")) or "")
            payload = serialize_account(account, include_raw=self.include_raw)
            payload["transactions"] = []
            payload["positions"] = []

            if include_transactions and (
                account_kind(account) in TRANSACTION_KINDS or _has_link(account, "_history_link")
            ):
                try:
                    _, items, item_warnings = self.transactions(
                        account_id,
                        since=since,
                        limit=limit,
                        include_coming=include_coming,
                    )
                    payload["transactions"] = serialize_transactions(
                        items, account_id=account_id, include_raw=self.include_raw
                    )
                    warnings.extend(item_warnings)
                except Exception as exc:
                    record_error(account_id, "transactions", exc)

            if include_positions and (
                account_kind(account) in POSITION_KINDS or _has_link(account, "_investment_link")
            ):
                try:
                    _, items = self.positions(account_id)
                    payload["positions"] = [
                        serialize_investment(item, include_raw=self.include_raw) for item in items
                    ]
                except Exception as exc:
                    record_error(account_id, "positions", exc)

            exported.append(payload)

        balances: dict[str, float] = {}
        position_values: dict[str, float] = {}
        for account in exported:
            currency = account.get("currency") or "EUR"
            if account.get("balance") is not None:
                balances[currency] = round(balances.get(currency, 0.0) + float(account["balance"]), 2)
            for position in account.get("positions", []):
                pos_currency = position.get("currency") or currency
                if position.get("valuation") is not None:
                    position_values[pos_currency] = round(
                        position_values.get(pos_currency, 0.0) + float(position["valuation"]), 2
                    )

        return {
            "schema": "fortuneo-sidecar/export/v1",
            "generatedAt": generated_at,
            "status": "partial" if errors else "ok",
            "accounts": exported,
            "totals": {
                "balances": balances,
                "positionValues": position_values,
            },
            "errors": errors,
            "warnings": [self._redact(warning) for warning in warnings],
        }
