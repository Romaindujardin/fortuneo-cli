"""CLI JSON du sidecar Fortuneo."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import stat
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TextIO

from . import __version__
from .credentials import (
    LOGIN_ENV,
    PASSWORD_ENV,
    Credentials,
    credentials_secrets,
    credentials_summary,
    delete_credentials,
    forget_stored_password,
    inspect_credentials,
    load_credentials,
    mask_login,
    redact,
    redact_value,
    save_login,
)
from .errors import (
    CredentialsError,
    PasswordRequired,
    SidecarError,
    UsageError,
)
from .otp import OTPProvider
from .paths import SidecarPaths

JsonDict = dict[str, Any]


class SidecarArgumentParser(argparse.ArgumentParser):
    """Transforme les erreurs argparse en erreurs JSON."""

    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(message)


class RedactionFilter(logging.Filter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self.secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message, self.secrets)
        if cleaned != message:
            record.msg = cleaned
            record.args = ()
        return True


class RedactionFormatter(logging.Formatter):
    """Redacte aussi les tracebacks, que le filtre ne couvre pas."""

    def __init__(self, fmt: str, secrets: list[str]) -> None:
        super().__init__(fmt)
        self.secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), self.secrets)


def _common_parser(*, defaults: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    default = None if defaults else argparse.SUPPRESS
    parser.add_argument("--data-dir", type=Path, default=default, help="Répertoire de données du sidecar.")
    parser.add_argument(
        "--credentials-file",
        type=Path,
        default=default,
        help="Fichier JSON privé contenant l'identifiant client (0600).",
    )
    parser.add_argument("--otp-file", type=Path, default=default, help="Fichier 0600 contenant le code SMS.")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        default=False if defaults else argparse.SUPPRESS,
        help="Lit le mot de passe sur stdin (une ligne, jamais en argv).",
    )
    parser.add_argument(
        "--otp-stdin",
        action="store_true",
        default=False if defaults else argparse.SUPPRESS,
        help="Lit le code SMS sur stdin (à réserver aux processus non interactifs).",
    )
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        default=False if defaults else argparse.SUPPRESS,
        help="N'ouvre aucune invite interactive (mot de passe et code SMS doivent venir de l'env, d'un fichier ou de stdin).",
    )
    parser.add_argument(
        "--state",
        action="store_true",
        default=False if defaults else argparse.SUPPRESS,
        help="Persiste l'état Woob (cookies de session) dans data/state.yml (opt-in).",
    )
    parser.add_argument(
        "--no-state",
        action="store_true",
        default=False if defaults else argparse.SUPPRESS,
        help="Obsolète: l'état Woob n'est plus persisté par défaut.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        default=False if defaults else argparse.SUPPRESS,
        help="Ajoute les objets Woob bruts dans la sortie JSON.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False if defaults else argparse.SUPPRESS,
        help="Active les logs détaillés (masqués) sur stderr.",
    )
    return parser


def build_parser() -> SidecarArgumentParser:
    common_top = _common_parser(defaults=True)
    common_sub = _common_parser(defaults=False)
    parser = SidecarArgumentParser(
        prog="fortuneo-sidecar",
        description="Sidecar autonome Fortuneo (Woob) : comptes, transactions et positions en JSON.",
        parents=[common_top],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("accounts", parents=[common_sub], help="Liste les comptes Fortuneo.")
    subparsers.add_parser("profile", parents=[common_sub], help="Affiche le profil Fortuneo (test de connexion).")

    transactions = subparsers.add_parser(
        "transactions", parents=[common_sub], help="Historique des transactions d'un compte."
    )
    transactions.add_argument("--account", default=None, help="Id, numéro, IBAN ou libellé du compte.")
    transactions.add_argument("--since", default=None, help="Date minimale (YYYY-MM-DD).")
    transactions.add_argument("--until", default=None, help="Date maximale (YYYY-MM-DD).")
    transactions.add_argument("--limit", type=int, default=None, help="Nombre maximum de transactions.")
    transactions.add_argument("--coming", action="store_true", help="Inclut les opérations à venir.")

    positions = subparsers.add_parser(
        "positions", parents=[common_sub], help="Positions d'un compte-titres / assurance-vie."
    )
    positions.add_argument("--account", default=None, help="Id, numéro, IBAN ou libellé du compte.")

    export = subparsers.add_parser(
        "export", parents=[common_sub], help="Export complet comptes + transactions + positions."
    )
    export.add_argument("--since", default=None, help="Date minimale des transactions (YYYY-MM-DD).")
    export.add_argument("--limit", type=int, default=None, help="Nombre maximum de transactions par compte.")
    export.add_argument("--coming", action="store_true", help="Inclut les opérations à venir.")
    export.add_argument("--no-transactions", action="store_true", help="N'exporte pas les transactions.")
    export.add_argument("--no-positions", action="store_true", help="N'exporte pas les positions.")

    doctor = subparsers.add_parser("doctor", parents=[common_sub], help="Diagnostic d'installation et de configuration.")
    doctor.add_argument("--network", action="store_true", help="Teste l'accès à mabanque.fortuneo.fr/env.json.")

    auth = subparsers.add_parser(
        "auth",
        parents=[common_sub],
        help="Enregistre l'identifiant client (jamais le mot de passe) ou vérifie le fichier.",
    )
    auth_group = auth.add_mutually_exclusive_group()
    auth_group.add_argument("--delete", action="store_true", help="Supprime le fichier d'identifiant.")
    auth_group.add_argument(
        "--from-env",
        action="store_true",
        help="Copie uniquement FORTUNEO_LOGIN (le mot de passe n'est jamais écrit).",
    )
    auth_group.add_argument("--check", action="store_true", help="Vérifie le fichier sans le modifier.")
    auth_group.add_argument(
        "--forget-password",
        action="store_true",
        help="Supprime un mot de passe hérité d'un fichier 0.1.x.",
    )

    return parser


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(stream: TextIO, payload: Any) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    stream.write("\n")
    stream.flush()


def _configure_logging(debug: bool, stream: TextIO, secrets: list[str]) -> None:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        RedactionFormatter("%(levelname)s %(name)s: %(message)s", secrets)
    )
    handler.addFilter(RedactionFilter(secrets))
    setattr(handler, "_fortuneo_sidecar", True)

    for name in ("fortuneo_sidecar", "woob"):
        logger = logging.getLogger(name)
        for existing in list(logger.handlers):
            if getattr(existing, "_fortuneo_sidecar", False):
                logger.removeHandler(existing)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG if debug else logging.WARNING)
        logger.propagate = False


def _build_otp_provider(
    args: argparse.Namespace,
    stdin: TextIO,
    stderr: TextIO,
    secrets: list[str] | None = None,
) -> OTPProvider:
    return OTPProvider(
        otp_file=args.otp_file,
        stdin=stdin,
        stderr=stderr,
        interactive=not args.no_interactive,
        allow_stdin=bool(args.otp_stdin),
        secrets=secrets,
    )


def _make_engine(
    args: argparse.Namespace,
    paths: SidecarPaths,
    credentials: Credentials,
    stdin: TextIO,
    stderr: TextIO,
    engine_factory: Callable[[], Any] | None,
    secrets: list[str] | None = None,
) -> Any:
    if engine_factory is not None:
        return engine_factory()

    from .engine import FortuneoEngine

    return FortuneoEngine(
        credentials,
        paths,
        otp_provider=_build_otp_provider(args, stdin, stderr, secrets),
        include_raw=bool(args.raw),
        persist_state=bool(getattr(args, "state", False)),
    )


def _validate_limit(limit: int | None) -> None:
    if limit is not None and limit < 0:
        raise UsageError("--limit doit être positif ou nul.")


def _cmd_accounts(args: argparse.Namespace, engine: Any, stdout: TextIO) -> int:
    from .serialize import serialize_account

    accounts = engine.accounts()
    _write_json(
        stdout,
        {
            "schema": "fortuneo-sidecar/accounts/v1",
            "generatedAt": _now_iso(),
            "accounts": [serialize_account(account, include_raw=bool(args.raw)) for account in accounts],
        },
    )
    return 0


def _cmd_profile(args: argparse.Namespace, engine: Any, stdout: TextIO) -> int:
    from .serialize import serialize_profile

    profile = engine.profile()
    _write_json(
        stdout,
        {
            "schema": "fortuneo-sidecar/profile/v1",
            "generatedAt": _now_iso(),
            "profile": serialize_profile(profile),
        },
    )
    return 0


def _cmd_transactions(args: argparse.Namespace, engine: Any, stdout: TextIO) -> int:
    from .serialize import serialize_account, serialize_transactions

    _validate_limit(args.limit)
    account, items, warnings = engine.transactions(
        args.account,
        since=args.since,
        until=args.until,
        limit=args.limit,
        include_coming=bool(args.coming),
    )
    account_id = str(account.id)
    _write_json(
        stdout,
        {
            "schema": "fortuneo-sidecar/transactions/v1",
            "generatedAt": _now_iso(),
            "account": serialize_account(account, include_raw=bool(args.raw)),
            "transactions": serialize_transactions(
                items, account_id=account_id, include_raw=bool(args.raw)
            ),
            "warnings": warnings,
        },
    )
    return 0


def _cmd_positions(args: argparse.Namespace, engine: Any, stdout: TextIO) -> int:
    from .serialize import serialize_account, serialize_investment

    account, items = engine.positions(args.account)
    positions = [serialize_investment(item, include_raw=bool(args.raw)) for item in items]
    totals: dict[str, float] = {}
    for position in positions:
        currency = position.get("currency") or "EUR"
        if position.get("valuation") is not None:
            totals[currency] = round(totals.get(currency, 0.0) + float(position["valuation"]), 2)
    _write_json(
        stdout,
        {
            "schema": "fortuneo-sidecar/positions/v1",
            "generatedAt": _now_iso(),
            "account": serialize_account(account, include_raw=bool(args.raw)),
            "positions": positions,
            "totals": totals,
        },
    )
    return 0


def _cmd_export(args: argparse.Namespace, engine: Any, stdout: TextIO) -> int:
    _validate_limit(args.limit)
    payload = engine.export(
        include_transactions=not args.no_transactions,
        include_positions=not args.no_positions,
        include_coming=bool(args.coming),
        since=args.since,
        limit=args.limit,
    )
    _write_json(stdout, payload)
    return 0


def _prompt_line(prompt: str, stdin: TextIO, stderr: TextIO) -> str:
    print(prompt, end="", file=stderr, flush=True)
    line = stdin.readline()
    if not line:
        raise UsageError("Entrée interrompue pendant l'authentification.")
    return line.strip()


def _read_password(prompt: str, stdin: TextIO, stderr: TextIO) -> str:
    if stdin.isatty():
        return getpass.getpass(prompt, stream=stderr)
    print(prompt, end="", file=stderr, flush=True)
    line = stdin.readline()
    if not line:
        raise UsageError("Entrée interrompue pendant la saisie du mot de passe.")
    return line.rstrip("\r\n")


MAX_PASSWORD_CHARS = 4096


def _read_secret_line(stdin: TextIO) -> str:
    """Lit un mot de passe sur stdin sans l'afficher ni le placer en argv."""
    line = stdin.readline(MAX_PASSWORD_CHARS + 1)
    if not line:
        raise PasswordRequired("Aucun mot de passe fourni sur stdin.")
    if len(line) > MAX_PASSWORD_CHARS:
        raise PasswordRequired(f"Mot de passe trop long sur stdin (max {MAX_PASSWORD_CHARS}).")
    password = line.rstrip("\r\n")
    if not password:
        raise PasswordRequired("Mot de passe vide sur stdin.")
    return password


def _resolve_runtime_credentials(
    args: argparse.Namespace,
    paths: SidecarPaths,
    stdin: TextIO,
    stderr: TextIO,
) -> Credentials:
    if getattr(args, "password_stdin", False):
        password = _read_secret_line(stdin)
        return load_credentials(paths, args.credentials_file, password=password)
    try:
        return load_credentials(paths, args.credentials_file)
    except PasswordRequired:
        if getattr(args, "no_interactive", False) or not stdin.isatty():
            raise
        password = _read_password("Mot de passe Fortuneo: ", stdin, stderr)
        return load_credentials(paths, args.credentials_file, password=password)


def _cmd_auth(
    args: argparse.Namespace,
    paths: SidecarPaths,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    path = Path(args.credentials_file).expanduser() if args.credentials_file else paths.credentials_file

    if args.check:
        try:
            info = inspect_credentials(path)
        except CredentialsError as exc:
            _write_json(
                stderr,
                {
                    "schema": "fortuneo-sidecar/auth/v1",
                    "ok": False,
                    "error": {"code": exc.code, "message": str(exc)},
                    "credentials": {
                        "file": str(path),
                        "exists": path.exists(),
                        "passwordStored": False,
                    },
                },
            )
            return exc.exit_code

        summary = credentials_summary(info, source="file")
        if info.password_stored:
            message = (
                f"Le fichier {path} contient encore un mot de passe. "
                "Exécutez `fortuneo-sidecar auth --forget-password`."
            )
            _write_json(
                stderr,
                {
                    "schema": "fortuneo-sidecar/auth/v1",
                    "ok": False,
                    "error": {"code": "PASSWORD_STORED", "message": message},
                    "credentials": summary,
                },
            )
            return 3
        if not info.login:
            message = f"Champ `login` manquant ou invalide dans {path}"
            _write_json(
                stderr,
                {
                    "schema": "fortuneo-sidecar/auth/v1",
                    "ok": False,
                    "error": {"code": "CREDENTIALS_MISSING", "message": message},
                    "credentials": summary,
                },
            )
            return 3
        _write_json(
            stdout,
            {
                "schema": "fortuneo-sidecar/auth/v1",
                "ok": True,
                "credentials": summary,
            },
        )
        return 0

    if args.delete:
        deleted = delete_credentials(path)
        _write_json(
            stdout,
            {
                "schema": "fortuneo-sidecar/auth/v1",
                "ok": True,
                "deleted": deleted,
                "credentialsFile": str(path),
            },
        )
        return 0

    if args.forget_password:
        removed = forget_stored_password(path)
        _write_json(
            stdout,
            {
                "schema": "fortuneo-sidecar/auth/v1",
                "ok": True,
                "credentialsFile": str(path),
                "removedPassword": removed,
                "passwordStored": False,
                "mode": f"{stat.S_IMODE(path.stat().st_mode):o}",
            },
        )
        return 0

    warnings: list[str] = []
    if args.from_env:
        login = (os.environ.get(LOGIN_ENV) or "").strip()
        if not login:
            raise CredentialsError(f"{LOGIN_ENV} doit être définie pour --from-env.")
        if os.environ.get(PASSWORD_ENV):
            warnings.append(
                "Le mot de passe n'est jamais enregistré: il reste uniquement "
                "en mémoire pour les commandes qui en ont besoin."
            )
    else:
        default_login = (os.environ.get(LOGIN_ENV) or "").strip()
        prompt = "Identifiant client Fortuneo"
        if default_login:
            prompt += f" [{mask_login(default_login)}]"
        login = _prompt_line(f"{prompt}: ", stdin, stderr) or default_login
        if not login:
            raise CredentialsError("Identifiant Fortuneo manquant.")

    save_login(path, login)
    payload = {
        "schema": "fortuneo-sidecar/auth/v1",
        "ok": True,
        "credentialsFile": str(path),
        "loginMasked": mask_login(login),
        "passwordStored": False,
        "mode": f"{stat.S_IMODE(path.stat().st_mode):o}",
    }
    if warnings:
        payload["warnings"] = warnings
    _write_json(stdout, payload)
    return 0


def _check_python() -> JsonDict:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    return {
        "name": "python",
        "ok": sys.version_info >= (3, 11),
        "critical": True,
        "detail": version,
    }


def _check_woob() -> JsonDict:
    try:
        import woob

        return {
            "name": "woob",
            "ok": True,
            "critical": True,
            "detail": getattr(woob, "__version__", "unknown"),
        }
    except Exception as exc:  # pragma: no cover - dépend de l'environnement
        return {"name": "woob", "ok": False, "critical": True, "detail": str(exc)}


def _check_module(paths: SidecarPaths) -> JsonDict:
    module_dir = paths.vendor_modules_dir / "fortuneo"
    if not (module_dir / "module.py").exists():
        return {
            "name": "module",
            "ok": False,
            "critical": True,
            "detail": f"Module vendorisé introuvable: {module_dir}",
        }
    try:
        import woob
        from woob.core.modules import ModulesLoader

        loader = ModulesLoader(str(paths.vendor_modules_dir), getattr(woob, "__version__", "3.7"))
        module = loader.get_or_load_module("fortuneo")
        config = getattr(module.klass, "CONFIG", {})
        required = {"login", "password"}
        missing = sorted(required - set(config.keys()))
        return {
            "name": "module",
            "ok": not missing,
            "critical": True,
            "detail": {
                "name": module.name,
                "path": str(module_dir),
                "capabilities": sorted(cap.__name__ for cap in module.iter_caps()),
                "missingConfig": missing,
            },
        }
    except Exception as exc:
        return {"name": "module", "ok": False, "critical": True, "detail": str(exc)}


def _check_storage(paths: SidecarPaths) -> JsonDict:
    writable = os.access(paths.data_dir, os.W_OK)
    detail: JsonDict = {"dataDir": str(paths.data_dir), "writable": writable}
    warnings: list[str] = []
    ok = writable

    for label, directory in (
        ("root", paths.root),
        ("config", paths.config_dir),
        ("data", paths.data_dir),
        ("cache", paths.cache_dir),
        ("home", paths.home_dir),
    ):
        try:
            mode = stat.S_IMODE(directory.stat().st_mode)
        except OSError:
            continue
        if mode & 0o077:
            warnings.append(
                f"Le répertoire {label} {directory} est accessible au groupe ou aux autres "
                f"(mode {mode:o}); envisagez: chmod 700 {directory}"
            )

    try:
        state = paths.state_file.lstat()
    except FileNotFoundError:
        state = None
    except OSError as exc:
        warnings.append(f"Impossible de vérifier le fichier d'état: {exc}")
        ok = False
        state = None

    if state is not None:
        if stat.S_ISLNK(state.st_mode):
            warnings.append(
                f"Le fichier d'état {paths.state_file} est un lien symbolique; "
                "le sidecar refusera de l'utiliser."
            )
            ok = False
        elif not stat.S_ISREG(state.st_mode):
            warnings.append(
                f"Le chemin d'état {paths.state_file} n'est pas un fichier régulier; "
                "le sidecar refusera de l'utiliser."
            )
            ok = False
        else:
            detail["stateMode"] = f"{stat.S_IMODE(state.st_mode):o}"
            if stat.S_IMODE(state.st_mode) & 0o077:
                warnings.append(
                    f"Le fichier d'état {paths.state_file} est accessible au groupe ou aux autres "
                    f"(mode {stat.S_IMODE(state.st_mode):o}); exécutez: chmod 600 {paths.state_file}"
                )

    detail["permissionWarnings"] = warnings
    return {"name": "storage", "ok": ok, "critical": True, "detail": detail}


def _check_credentials(
    paths: SidecarPaths,
    credentials_file: Path | None,
    *,
    stdin: TextIO,
    no_interactive: bool,
) -> tuple[JsonDict, bool]:
    path = Path(credentials_file).expanduser() if credentials_file else paths.credentials_file
    env_login = (os.environ.get(LOGIN_ENV) or "").strip()
    env_password = os.environ.get(PASSWORD_ENV) or ""

    info = None
    try:
        info = inspect_credentials(path)
    except CredentialsError as exc:
        if not env_login:
            return (
                {
                    "name": "credentials",
                    "ok": False,
                    "critical": False,
                    "detail": {"file": str(path), "error": str(exc)},
                },
                False,
            )

    if info is not None and info.password_stored:
        detail = credentials_summary(info, source="file")
        detail["error"] = "Un mot de passe est encore stocké dans le fichier."
        detail["hint"] = "fortuneo-sidecar auth --forget-password"
        return (
            {"name": "credentials", "ok": False, "critical": False, "detail": detail},
            False,
        )

    login = env_login or (info.login if info is not None else None)
    if not login:
        return (
            {
                "name": "credentials",
                "ok": False,
                "critical": False,
                "detail": {
                    "file": str(path),
                    "error": "Aucun identifiant configuré.",
                    "hint": "fortuneo-sidecar auth",
                },
            },
            False,
        )

    detail = (
        credentials_summary(info, source="env" if env_login else "file")
        if info is not None
        else {"file": str(path), "exists": False, "passwordStored": False, "source": "env"}
    )
    if env_password:
        detail["passwordSource"] = "env"
    elif not no_interactive and stdin.isatty():
        detail["passwordSource"] = "prompt"
    else:
        detail["passwordSource"] = "missing"
    ok = detail["passwordSource"] != "missing"
    return (
        {"name": "credentials", "ok": ok, "critical": False, "detail": detail},
        ok,
    )


def _check_otp(args: argparse.Namespace, stdin: TextIO, stderr: TextIO) -> JsonDict:
    provider = _build_otp_provider(args, stdin, stderr)
    mode = provider.mode()
    hints = {
        "env": f"FORTUNEO_OTP est définie.",
        "file": f"Fichier OTP: {args.otp_file}",
        "stdin": "Lecture du code sur stdin.",
        "interactive": "Invite interactive disponible.",
        "unavailable": "Aucun canal OTP disponible; une 2FA Fortuneo échouera proprement.",
    }
    detail: JsonDict = {"mode": mode, "hint": hints[mode]}
    problem = provider.probe()
    if problem:
        detail["error"] = problem
    return {
        "name": "otp",
        "ok": mode != "unavailable" and problem is None,
        "critical": False,
        "detail": detail,
    }


def _check_network() -> JsonDict:
    url = "https://mabanque.fortuneo.fr/env.json"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(8192)
            payload = json.loads(body.decode("utf-8"))
        ok = bool(payload.get("apigeeUrl") and payload.get("apigeeApiKey"))
        return {
            "name": "network",
            "ok": ok,
            "critical": False,
            "detail": {
                "url": url,
                "status": getattr(response, "status", None),
                "apiUrl": payload.get("apigeeUrl"),
                "env": payload.get("env"),
            },
        }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        return {
            "name": "network",
            "ok": False,
            "critical": False,
            "detail": {"url": url, "error": str(exc)},
        }


def _cmd_doctor(
    args: argparse.Namespace,
    paths: SidecarPaths,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    checks = [
        _check_python(),
        _check_woob(),
        _check_module(paths),
        _check_storage(paths),
    ]
    credentials_check, credentials_ok = _check_credentials(
        paths,
        args.credentials_file,
        stdin=stdin,
        no_interactive=bool(args.no_interactive),
    )
    checks.append(credentials_check)
    checks.append(_check_otp(args, stdin, stderr))
    if args.network:
        checks.append(_check_network())

    critical_ok = all(check["ok"] for check in checks if check["critical"])
    ready = critical_ok and credentials_ok
    warnings: list[str] = []
    if not credentials_ok:
        detail = credentials_check["detail"]
        if detail.get("passwordStored"):
            warnings.append(
                "Un mot de passe est encore stocké dans le fichier d'identifiant: "
                "exécutez `fortuneo-sidecar auth --forget-password`."
            )
        elif not detail.get("loginMasked"):
            warnings.append("Aucun identifiant configuré: lancez `fortuneo-sidecar auth`.")
        else:
            warnings.append(
                "Mot de passe non disponible: définissez FORTUNEO_PASSWORD, "
                "utilisez --password-stdin ou lancez la commande dans un terminal."
            )
    otp_check = next(check for check in checks if check["name"] == "otp")
    if not otp_check["ok"]:
        warnings.append(
            "Aucun canal OTP configuré: si Fortuneo demande une validation 2FA, "
            "utilisez --otp-file ou un terminal interactif."
        )
    storage_check = next(check for check in checks if check["name"] == "storage")
    warnings.extend(storage_check["detail"].get("permissionWarnings", []))

    _write_json(
        stdout,
        {
            "schema": "fortuneo-sidecar/doctor/v1",
            "generatedAt": _now_iso(),
            "ok": critical_ok,
            "ready": ready,
            "checks": checks,
            "warnings": warnings,
        },
    )
    return 0 if critical_ok else 1


def _emit_error(stderr: TextIO, exc: BaseException, secrets: list[str], debug: bool) -> int:
    if isinstance(exc, SidecarError):
        exit_code = exc.exit_code
        payload = {
            "schema": "fortuneo-sidecar/error/v1",
            "ok": False,
            "error": {
                "code": exc.code,
                "message": redact(str(exc), secrets),
                "details": redact_value(exc.details, secrets),
            },
        }
    else:
        exit_code = 1
        payload = {
            "schema": "fortuneo-sidecar/error/v1",
            "ok": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": redact(f"Erreur interne: {exc}", secrets),
                "details": {},
            },
        }
    if debug:
        payload["error"]["traceback"] = redact(traceback.format_exc(), secrets)
    try:
        _write_json(stderr, payload)
    except BrokenPipeError:
        pass
    return exit_code


def _absolute_path(value: Path | None) -> Path | None:
    if value is None:
        return None
    return Path(value).expanduser().resolve()


def _prepare_cli_paths(args: argparse.Namespace) -> None:
    """Résout les chemins avant de remplacer HOME par le bac à sable."""
    for attribute in ("data_dir", "credentials_file", "otp_file"):
        value = getattr(args, attribute, None)
        if value is not None:
            setattr(args, attribute, _absolute_path(value))

    modules_override = os.environ.get("FORTUNEO_SIDECAR_MODULES")
    if modules_override:
        os.environ["FORTUNEO_SIDECAR_MODULES"] = str(_absolute_path(Path(modules_override)))


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin: TextIO | None = None,
    engine_factory: Callable[[], Any] | None = None,
) -> int:
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    stdin = sys.stdin if stdin is None else stdin
    secrets: list[str] = []
    debug = False

    try:
        args = build_parser().parse_args(argv)
        debug = bool(getattr(args, "debug", False))
        _configure_logging(debug, stderr, secrets)

        if getattr(args, "password_stdin", False) and getattr(args, "otp_stdin", False):
            raise UsageError("--password-stdin et --otp-stdin sont incompatibles.")
        if getattr(args, "state", False) and getattr(args, "no_state", False):
            raise UsageError("--state et --no-state sont incompatibles.")

        _prepare_cli_paths(args)
        paths = SidecarPaths.from_root(args.data_dir) if getattr(args, "data_dir", None) else SidecarPaths.default()
        paths.ensure()
        previous_env = paths.apply_sandbox_env()
        try:
            if args.command == "auth":
                return _cmd_auth(args, paths, stdin, stdout, stderr)
            if args.command == "doctor":
                return _cmd_doctor(args, paths, stdin, stdout, stderr)

            credentials = _resolve_runtime_credentials(args, paths, stdin, stderr)
            secrets.extend(credentials_secrets(credentials))

            engine = _make_engine(args, paths, credentials, stdin, stderr, engine_factory, secrets)
            with engine:
                if args.command == "accounts":
                    return _cmd_accounts(args, engine, stdout)
                if args.command == "profile":
                    return _cmd_profile(args, engine, stdout)
                if args.command == "transactions":
                    return _cmd_transactions(args, engine, stdout)
                if args.command == "positions":
                    return _cmd_positions(args, engine, stdout)
                if args.command == "export":
                    return _cmd_export(args, engine, stdout)
            raise UsageError(f"Commande inconnue: {args.command}")
        finally:
            paths.restore_env(previous_env)
    except SidecarError as exc:
        return _emit_error(stderr, exc, secrets, debug)
    except KeyboardInterrupt:
        return _emit_error(stderr, UsageError("Interrompu par l'utilisateur."), secrets, debug)
    except BrokenPipeError:
        return 0
    except Exception as exc:  # pragma: no cover - filet de sécurité CLI
        return _emit_error(stderr, exc, secrets, debug)
