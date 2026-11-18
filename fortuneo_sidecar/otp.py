"""Fourniture du code SMS/2FA.

Sources, dans l'ordre : variable d'environnement, fichier privé, saisie
interactive sur un terminal, ou stdin explicitement autorisé.
"""

from __future__ import annotations

import getpass
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, TextIO

from .errors import OTPRequired
from .private_files import read_private_text

OTP_ENV = "FORTUNEO_OTP"
_OTP_RE = re.compile(r"^\d{4,8}$")


def _read_otp_file(path: Path) -> str:
    content = read_private_text(path, error_cls=OTPRequired, label="Le fichier OTP")
    for line in content.splitlines():
        candidate = line.strip()
        if candidate:
            return candidate
    raise OTPRequired(f"Le fichier OTP est vide: {path}")


def _validate_otp(code: str) -> str:
    code = (code or "").strip().replace(" ", "")
    if not _OTP_RE.fullmatch(code):
        raise OTPRequired("Le code de validation doit contenir de 4 à 8 chiffres.")
    return code


@dataclass
class OTPProvider:
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    otp_file: Path | None = None
    stdin: TextIO | None = None
    stderr: TextIO | None = None
    interactive: bool = True
    allow_stdin: bool = False
    secrets: list[str] | None = None
    _used: set[str] = field(default_factory=set, init=False)

    def mode(self) -> str:
        if (self.env.get(OTP_ENV) or "").strip():
            return "env"
        if self.otp_file:
            return "file"
        if self.allow_stdin and self.stdin is not None and not self.stdin.isatty():
            return "stdin"
        if self.interactive and self.stdin is not None and self.stdin.isatty():
            return "interactive"
        return "unavailable"

    def probe(self) -> str | None:
        """Valide le canal configuré sans consommer ni révéler le code.

        Retourne un message d'erreur si le canal est utilisable en apparence
        mais invalide (fichier absent, permissions trop larges, code mal formé).
        """
        mode = self.mode()
        if mode == "env":
            try:
                _validate_otp(self.env.get(OTP_ENV) or "")
            except OTPRequired as exc:
                return str(exc)
        elif mode == "file" and self.otp_file is not None:
            try:
                _read_otp_file(self.otp_file)
            except OTPRequired as exc:
                return str(exc)
        return None

    def get_code(self, field_id: str = "code", label: str | None = None) -> str:
        code: str | None = None
        mode = self.mode()

        if mode == "env":
            code = self.env.get(OTP_ENV)
        elif mode == "file" and self.otp_file is not None:
            code = _read_otp_file(self.otp_file)
        elif mode == "stdin" and self.stdin is not None:
            if self.stderr is not None:
                print("Code de validation Fortuneo (lu sur stdin): ", end="", file=self.stderr, flush=True)
            code = self.stdin.readline()
            if not code:
                raise OTPRequired("Aucun code de validation reçu sur stdin.")
        elif mode == "interactive" and self.stdin is not None:
            prompt = f"{label or 'Code de validation Fortuneo'}: "
            code = getpass.getpass(prompt, stream=self.stderr or self.stdin)
        else:
            raise OTPRequired(
                "Fortuneo demande une validation 2FA. Fournissez le code via "
                f"{OTP_ENV}, --otp-file (fichier 0600), ou relancez dans un terminal interactif."
            )

        code = _validate_otp(code)

        if mode in {"env", "file", "stdin"} and code in self._used:
            raise OTPRequired(
                "Ce code de validation a déjà été utilisé. "
                "Un nouveau code SMS est probablement nécessaire: relancez sans code pour en demander un autre."
            )
        self._used.add(code)
        if self.secrets is not None and code not in self.secrets:
            self.secrets.append(code)
        return code
