"""Gestion des identifiants Fortuneo.

Le fichier privé ne contient que l'identifiant client. Le mot de passe n'est
jamais écrit sur disque : il provient de ``FORTUNEO_PASSWORD``, de
``--password-stdin`` ou d'une invite interactive, et reste en mémoire.
Aucun mot de passe n'est accepté en argument de ligne de commande.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, quote_plus

from .errors import CredentialsError, PasswordRequired
from .paths import SidecarPaths
from .private_files import read_private_text

LOGIN_ENV = "FORTUNEO_LOGIN"
PASSWORD_ENV = "FORTUNEO_PASSWORD"

_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&#](?:password|passwd|pwd|access_code|sca_code|otp|code|session_id|state|"
    r"access_token|refresh_token|token)=)[^&\s\"'<>]+"
)
_HEADER_SECRET_RE = re.compile(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+")
_JSON_SECRET_RE = re.compile(
    r'(?i)("(?:password|passwd|pwd|access_code|sca_code|otp|code|session_id|state|'
    r'access_token|refresh_token|token)"\s*:\s*")[^"]*(")'
)
_FORM_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|access_code|sca_code|otp|code|session_id|state|"
    r"access_token|refresh_token|token)=([^&\s,;\"']+)"
)


def _secret_variants(secret: str) -> set[str]:
    """Formes sous lesquelles un secret peut apparaître dans un log HTTP."""
    variants = {secret}
    for encoded in (quote(secret, safe=""), quote_plus(secret, safe="")):
        if encoded != secret:
            variants.add(encoded)
    escaped = json.dumps(secret, ensure_ascii=False)[1:-1]
    if escaped != secret:
        variants.add(escaped)
    return variants


def _mask_patterns(text: str) -> str:
    text = _QUERY_SECRET_RE.sub(r"\1***", text)
    text = _HEADER_SECRET_RE.sub(r"\1***", text)
    text = _JSON_SECRET_RE.sub(r"\1***\2", text)
    text = _FORM_SECRET_RE.sub(r"\1=***", text)
    return text


def redact(text: str, secrets: list[str] | tuple[str, ...]) -> str:
    """Remplace les secrets connus et les champs sensibles dans un texte.

    Les variantes URL-encodées/JSON sont couvertes, ainsi que les champs
    ``password``, ``access_code``, ``code``, ``session_id`` et les en-têtes
    ``Authorization``. Les valeurs exactes restent prioritaires pour éviter
    qu'un secret court ne soit masqué partiellement.
    """
    if not text:
        return text
    result = str(text)
    variants: set[str] = set()
    for secret in secrets:
        if secret and not re.fullmatch(r"\*+", secret):
            variants.update(_secret_variants(str(secret)))
    for secret in sorted(variants, key=len, reverse=True):
        result = result.replace(secret, "***")
    return _mask_patterns(result)


def redact_value(value: Any, secrets: list[str] | tuple[str, ...]) -> Any:
    """Applique :func:`redact` récursivement à une structure JSON."""
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, dict):
        return {key: redact_value(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item, secrets) for item in value]
    return value


@dataclass(frozen=True)
class Credentials:
    login: str
    password: str
    source: str = "unknown"

    def __post_init__(self) -> None:
        if not isinstance(self.login, str) or not self.login.strip():
            raise CredentialsError("Identifiant Fortuneo manquant.")
        if not isinstance(self.password, str) or self.password == "":
            raise CredentialsError("Mot de passe Fortuneo manquant.")

    def __repr__(self) -> str:  # ne jamais afficher le mot de passe
        return f"Credentials(login={mask_login(self.login)!r}, password='***', source={self.source!r})"


@dataclass(frozen=True)
class CredentialFileInfo:
    """Métadonnées non sensibles du fichier d'identifiant."""

    path: Path
    login: str | None
    password_stored: bool
    symlink: bool
    mode: str | None


def mask_login(login: str) -> str:
    login = (login or "").strip()
    if not login:
        return "***"
    if len(login) <= 2:
        return "***"
    return f"{login[0]}***"


def _read_json_object(path: Path) -> dict[str, Any]:
    content = read_private_text(
        path,
        error_cls=CredentialsError,
        label="Le fichier d'identifiants",
    )
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise CredentialsError(f"Fichier d'identifiants JSON invalide: {path} ({exc.msg})") from exc
    if not isinstance(raw, dict):
        raise CredentialsError(f"Le fichier d'identifiants doit contenir un objet JSON: {path}")
    return raw


def inspect_credentials(path: Path) -> CredentialFileInfo:
    """Lit les métadonnées du fichier sans exposer ni retourner le mot de passe."""
    raw = _read_json_object(path)
    login_value = raw.get("login")
    login = login_value.strip() if isinstance(login_value, str) and login_value.strip() else None
    password_value = raw.get("password")
    password_stored = isinstance(password_value, str) and password_value != ""

    symlink = False
    mode = None
    try:
        info = path.lstat()
    except OSError:
        info = None
    if info is not None:
        symlink = stat.S_ISLNK(info.st_mode)
        if stat.S_ISREG(info.st_mode):
            mode = f"{stat.S_IMODE(info.st_mode):o}"
    return CredentialFileInfo(
        path=path,
        login=login,
        password_stored=password_stored,
        symlink=symlink,
        mode=mode,
    )


def load_login_file(path: Path) -> str:
    """Charge l'identifiant client, en refusant un fichier hérité avec mot de passe."""
    info = inspect_credentials(path)
    if info.password_stored:
        raise CredentialsError(
            f"Le fichier {path} contient encore un mot de passe. "
            "Le sidecar ne stocke plus les mots de passe. "
            "Exécutez `fortuneo-sidecar auth --forget-password` pour le supprimer."
        )
    if not info.login:
        raise CredentialsError(f"Champ `login` manquant ou invalide dans {path}")
    return info.login


def load_credentials(
    paths: SidecarPaths,
    credentials_file: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    password: str | None = None,
) -> Credentials:
    """Résout le couple identifiant/mot de passe sans jamais lire un mot de passe stocké.

    Priorité : variables d'environnement complètes, puis identifiant du fichier
    privé. Le mot de passe vient ensuite de ``FORTUNEO_PASSWORD``, du paramètre
    ``password`` (invite ou stdin explicite), ou lève ``PasswordRequired``.
    """
    env = os.environ if env is None else env
    env_login = (env.get(LOGIN_ENV) or "").strip()
    env_password = env.get(PASSWORD_ENV) or ""

    if env_login and env_password:
        return Credentials(login=env_login, password=env_password, source="env")

    if env_login:
        login, source = env_login, "env"
    else:
        path = Path(credentials_file).expanduser() if credentials_file else paths.credentials_file
        login, source = load_login_file(path), "file"

    if env_password:
        return Credentials(login=login, password=env_password, source=f"{source}+env")
    if password:
        return Credentials(login=login, password=password, source=f"{source}+prompt")
    raise PasswordRequired(
        "Mot de passe Fortuneo manquant. Définissez FORTUNEO_PASSWORD, "
        "utilisez --password-stdin, ou lancez la commande dans un terminal."
    )


def _write_private_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".credentials-", suffix=".json", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        tmp_path.unlink(missing_ok=True)
        raise


def save_login(path: Path, login: str) -> Path:
    """Écrit uniquement l'identifiant client, jamais le mot de passe."""
    login = (login or "").strip()
    if not login:
        raise CredentialsError("Identifiant Fortuneo manquant.")
    _write_private_json(path, {"login": login})
    return path


def forget_stored_password(path: Path) -> bool:
    """Supprime un mot de passe hérité d'un fichier 0.1.x.

    Retourne ``True`` si un mot de passe a été retiré, ``False`` si le fichier
    ne contenait déjà que l'identifiant.
    """
    raw = _read_json_object(path)
    login = raw.get("login")
    if not isinstance(login, str) or not login.strip():
        raise CredentialsError(f"Champ `login` manquant ou invalide dans {path}")
    password_value = raw.get("password")
    if not isinstance(password_value, str) or password_value == "":
        return False
    _write_private_json(path, {"login": login.strip()})
    return True


def delete_credentials(path: Path) -> bool:
    if path.is_symlink():
        raise CredentialsError(
            f"Le fichier d'identifiants {path} est un lien symbolique; "
            "le sidecar refuse de le supprimer. Supprimez-le manuellement après vérification."
        )
    if path.exists():
        path.unlink()
        return True
    return False


def credentials_summary(info: CredentialFileInfo, *, source: str | None = None) -> dict:
    """Résumé sûr pour `doctor` / `auth --check`."""
    return {
        "file": str(info.path),
        "exists": True,
        "loginMasked": mask_login(info.login) if info.login else None,
        # Utile pour distinguer le numéro client (8 chiffres) d'un numéro de
        # compte lors d'un échec de login, sans révéler l'identifiant.
        "loginLength": len(info.login) if info.login else None,
        "loginNumeric": info.login.isdigit() if info.login else None,
        "passwordStored": info.password_stored,
        "source": source,
        "symlink": info.symlink,
        "mode": info.mode,
    }


def credentials_secrets(credentials: Credentials | None, otp: str | None = None) -> list[str]:
    secrets: list[str] = []
    if credentials:
        secrets.append(credentials.password)
        secrets.append(credentials.login)
    if otp:
        secrets.append(otp)
    return [s for s in secrets if s and not re.fullmatch(r"\*+", s)]
