#!/usr/bin/env python3
"""Smoke test réel Fortuneo, en une seule session Woob.

Ce script est réservé aux essais manuels avec un vrai compte. Il enchaîne :

1. le profil (preuve de login) ;
2. la liste des comptes ;
3. un historique de transactions ciblé ;
4. des positions ciblées ;
5. éventuellement l'export complet (`--export`).

Il n'affiche par défaut ni soldes, ni IBAN, ni montants : seulement des
compteurs, des types et les erreurs. Utilisez `--show-data` pour voir un
échantillon des données sérialisées.

Aucun mot de passe n'est accepté en argument. Le fichier créé par
`fortuneo-sidecar auth` ne contient que l'identifiant client : le mot de passe
est demandé sur le terminal ou fourni via FORTUNEO_PASSWORD. Aucun cookie de
session n'est persisté, sauf avec `--state`.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from fortuneo_sidecar import __version__
from fortuneo_sidecar.credentials import (
    credentials_secrets,
    load_credentials,
    mask_login,
    redact,
)
from fortuneo_sidecar.errors import PasswordRequired, SidecarError
from fortuneo_sidecar.otp import OTPProvider
from fortuneo_sidecar.paths import SidecarPaths


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Smoke test réel du sidecar Fortuneo (une seule session).",
    )
    parser.add_argument("--data-dir", type=Path, default=None, help="Répertoire de données du sidecar.")
    parser.add_argument(
        "--credentials-file", type=Path, default=None, help="Fichier JSON contenant l'identifiant client (0600)."
    )
    parser.add_argument("--otp-file", type=Path, default=None, help="Fichier 0600 contenant le code SMS.")
    parser.add_argument("--otp-stdin", action="store_true", help="Lit le code SMS sur stdin.")
    parser.add_argument(
        "--no-interactive", action="store_true", help="N'ouvre aucune invite interactive."
    )
    parser.add_argument(
        "--account", default=None, help="Compte ciblé (id, numéro, IBAN ou libellé) pour les tests ciblés."
    )
    parser.add_argument("--since", default=None, help="Date minimale des transactions (YYYY-MM-DD).")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Transactions par compte pour le test ciblé (défaut: 5). "
        "L'export n'est limité que si cette option est fournie.",
    )
    parser.add_argument(
        "--export", action="store_true", help="Exécute aussi l'export complet après les tests ciblés."
    )
    parser.add_argument(
        "--show-data", action="store_true", help="Affiche un échantillon des données sérialisées."
    )
    parser.add_argument("--debug", action="store_true", help="Active les logs détaillés (masqués).")
    parser.add_argument(
        "--state",
        action="store_true",
        help="Persiste l'état Woob (cookies de session) dans data/state.yml (opt-in).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def write_json(stream: TextIO, payload: Any) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    stream.write("\n")
    stream.flush()


def _summary_account(account: Any) -> dict:
    from fortuneo_sidecar.serialize import account_kind, account_type_info, clean_value

    type_key, type_label = account_type_info(account)
    return {
        "id": clean_value(account.id),
        "label": clean_value(account.label),
        "kind": account_kind(account),
        "type": type_key,
        "typeLabel": type_label,
        "currency": clean_value(account.currency),
    }


def _pick_account(accounts: list[Any], preferred_kinds: set[str]) -> Any | None:
    from fortuneo_sidecar.serialize import account_kind

    for account in accounts:
        if account_kind(account) in preferred_kinds:
            return account
    return None


def run(args: argparse.Namespace, stdout: TextIO, stderr: TextIO) -> int:
    from fortuneo_sidecar.cli import _configure_logging
    from fortuneo_sidecar.engine import FortuneoEngine
    from fortuneo_sidecar.serialize import (
        POSITION_KINDS,
        TRANSACTION_KINDS,
        account_kind,
        clean_value,
        serialize_investment,
        serialize_transactions,
    )

    for attribute in ("data_dir", "credentials_file", "otp_file"):
        value = getattr(args, attribute, None)
        if value is not None:
            setattr(args, attribute, Path(value).expanduser().resolve())
    modules_override = os.environ.get("FORTUNEO_SIDECAR_MODULES")
    if modules_override:
        os.environ["FORTUNEO_SIDECAR_MODULES"] = str(Path(modules_override).expanduser().resolve())

    paths = SidecarPaths.from_root(args.data_dir) if args.data_dir else SidecarPaths.default()
    paths.ensure()
    previous_env = paths.apply_sandbox_env()

    secrets: list[str] = []
    _configure_logging(args.debug, stderr, secrets)

    try:
        credentials = load_credentials(paths, args.credentials_file)
    except PasswordRequired:
        if args.no_interactive:
            raise
        password = getpass.getpass("Mot de passe Fortuneo: ", stream=stderr)
        credentials = load_credentials(paths, args.credentials_file, password=password)
    secrets.extend(credentials_secrets(credentials))

    def safe_error(exc: BaseException) -> str:
        if isinstance(exc, SidecarError):
            return f"{exc.code}: {redact(str(exc), secrets)}"
        return redact(f"{type(exc).__name__}: {exc}", secrets)

    otp_provider = OTPProvider(
        otp_file=args.otp_file,
        stdin=sys.stdin,
        stderr=stderr,
        interactive=not args.no_interactive,
        allow_stdin=bool(args.otp_stdin),
        secrets=secrets,
    )

    report: dict[str, Any] = {
        "schema": "fortuneo-sidecar/real-smoke/v1",
        "generatedAt": now_iso(),
        "status": "ok",
        "credentials": {
            "source": credentials.source,
            "loginMasked": mask_login(credentials.login),
        },
        "otpMode": otp_provider.mode(),
        "steps": [],
        "accounts": [],
        "targeted": {},
        "errors": [],
        "warnings": [],
    }

    if otp_provider.mode() == "unavailable":
        report["warnings"].append(
            "Aucun canal OTP disponible. Si Fortuneo demande une validation, "
            "le smoke test échouera avec OTP_REQUIRED."
        )

    def add_step(name: str, ok: bool | None, detail: Any = None, error: str | None = None) -> None:
        step: dict[str, Any] = {"name": name, "ok": ok}
        if detail is not None:
            step["detail"] = detail
        if error:
            step["error"] = error
        report["steps"].append(step)

    def add_error(step: str, exc: BaseException) -> None:
        message = safe_error(exc)
        code = exc.code if isinstance(exc, SidecarError) else "INTERNAL_ERROR"
        report["errors"].append({"step": step, "code": code, "message": message})

    engine = FortuneoEngine(
        credentials,
        paths,
        otp_provider=otp_provider,
        include_raw=False,
        persist_state=args.state,
    )

    try:
        # 1. Profil : premier appel réseau authentifié.
        try:
            profile = engine.profile()
            add_step("profile", True, {"hasName": bool(getattr(profile, "name", None))})
        except SidecarError as exc:
            add_step("profile", False, error=safe_error(exc))
            add_error("profile", exc)
            report["status"] = "failed"
            write_json(stdout, report)
            return 1

        # 2. Comptes.
        try:
            accounts = engine.accounts()
            report["accounts"] = [_summary_account(account) for account in accounts]
            add_step("accounts", True, {"count": len(accounts)})
            if not accounts:
                report["warnings"].append("Aucun compte retourné par Fortuneo.")
        except SidecarError as exc:
            add_step("accounts", False, error=safe_error(exc))
            add_error("accounts", exc)
            report["status"] = "failed"
            write_json(stdout, report)
            return 1

        # 3. Transactions ciblées.
        target = None
        if args.account:
            try:
                target = engine.find_account(args.account)
            except SidecarError as exc:
                add_step("select_account", False, error=safe_error(exc))
                add_error("select_account", exc)
        else:
            target = _pick_account(accounts, TRANSACTION_KINDS)

        if target is None:
            add_step("transactions", None, {"skipped": True, "reason": "aucun compte candidat"})
        else:
            try:
                account, items, step_warnings = engine.transactions(
                    str(target.id),
                    since=args.since,
                    limit=5 if args.limit is None else args.limit,
                )
                dates = [
                    clean_value(getattr(item, "date", None))
                    for item in items
                    if clean_value(getattr(item, "date", None))
                ]
                detail: dict[str, Any] = {
                    "accountId": clean_value(account.id),
                    "count": len(items),
                    "firstDate": min(dates) if dates else None,
                    "lastDate": max(dates) if dates else None,
                }
                if step_warnings:
                    detail["warnings"] = step_warnings
                    report["warnings"].extend(step_warnings)
                add_step("transactions", True, detail)
                report["targeted"]["transactions"] = detail
                if args.show_data:
                    report["sample"] = report.get("sample", {})
                    report["sample"]["transactions"] = serialize_transactions(
                        items, account_id=str(account.id)
                    )[:2]
            except SidecarError as exc:
                add_step("transactions", False, error=safe_error(exc))
                add_error("transactions", exc)

        # 4. Positions ciblées.
        position_target = target
        if position_target is not None and account_kind(position_target) not in POSITION_KINDS:
            position_target = _pick_account(accounts, POSITION_KINDS)
        if position_target is None:
            add_step("positions", None, {"skipped": True, "reason": "aucun compte de placement"})
        else:
            try:
                account, positions = engine.positions(str(position_target.id))
                currencies = sorted(
                    {
                        clean_value(getattr(position, "original_currency", None))
                        for position in positions
                        if clean_value(getattr(position, "original_currency", None))
                    }
                )
                detail = {
                    "accountId": clean_value(account.id),
                    "count": len(positions),
                    "currencies": currencies,
                }
                add_step("positions", True, detail)
                report["targeted"]["positions"] = detail
                if args.show_data:
                    report["sample"] = report.get("sample", {})
                    report["sample"]["positions"] = [
                        serialize_investment(item) for item in positions[:2]
                    ]
            except SidecarError as exc:
                add_step("positions", False, error=safe_error(exc))
                add_error("positions", exc)

        # 5. Export complet optionnel.
        if args.export:
            try:
                exported = engine.export(
                    include_transactions=True,
                    include_positions=True,
                    since=args.since,
                    limit=args.limit,
                )
                export_counts = {
                    "accounts": len(exported.get("accounts", [])),
                    "transactions": sum(
                        len(item.get("transactions", [])) for item in exported.get("accounts", [])
                    ),
                    "positions": sum(
                        len(item.get("positions", [])) for item in exported.get("accounts", [])
                    ),
                    "status": exported.get("status"),
                }
                add_step("export", exported.get("status") == "ok", export_counts)
                if exported.get("errors"):
                    report["errors"].extend(
                        {"step": "export", **item} for item in exported["errors"]
                    )
                if exported.get("warnings"):
                    report["warnings"].extend(exported["warnings"])
                if args.show_data:
                    report["sample"] = report.get("sample", {})
                    report["sample"]["accounts"] = exported.get("accounts", [])[:1]
            except SidecarError as exc:
                add_step("export", False, error=safe_error(exc))
                add_error("export", exc)

        if any(step["ok"] is False for step in report["steps"]):
            if report["status"] == "ok":
                report["status"] = "partial"
        write_json(stdout, report)
        return 0 if report["status"] == "ok" else 1
    finally:
        engine.close()
        paths.restore_env(previous_env)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit is not None and args.limit < 0:
        write_json(
            sys.stderr,
            {
                "schema": "fortuneo-sidecar/error/v1",
                "ok": False,
                "error": {"code": "USAGE_ERROR", "message": "--limit doit être positif ou nul."},
            },
        )
        return 2
    try:
        return run(args, sys.stdout, sys.stderr)
    except SidecarError as exc:
        write_json(
            sys.stderr,
            {
                "schema": "fortuneo-sidecar/error/v1",
                "ok": False,
                "error": {"code": exc.code, "message": str(exc)},
            },
        )
        return exc.exit_code
    except KeyboardInterrupt:
        write_json(
            sys.stderr,
            {
                "schema": "fortuneo-sidecar/error/v1",
                "ok": False,
                "error": {"code": "INTERRUPTED", "message": "Interrompu par l'utilisateur."},
            },
        )
        return 2
    except Exception as exc:
        logging.getLogger(__name__).exception("Erreur interne du smoke test")
        write_json(
            sys.stderr,
            {
                "schema": "fortuneo-sidecar/error/v1",
                "ok": False,
                "error": {"code": "INTERNAL_ERROR", "message": redact(f"Erreur interne: {exc}", [])},
        },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
