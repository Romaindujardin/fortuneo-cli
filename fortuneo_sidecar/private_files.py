"""Lecture sécurisée des fichiers privés (identifiants, code OTP).

Les fichiers sensibles sont ouverts sans suivre un éventuel lien symbolique,
puis vérifiés comme fichiers réguliers et non lisibles par le groupe ou les
autres. La vérification porte sur le descripteur ouvert, ce qui évite qu'un
remplacement de fichier entre le contrôle et la lecture ne contourne les
contrôles.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

from .errors import SidecarError

MAX_PRIVATE_FILE_BYTES = 64 * 1024


def _open_error(path: Path, exc: OSError, *, error_cls: type[SidecarError], label: str) -> SidecarError:
    if exc.errno == errno.ELOOP:
        return error_cls(
            f"{label} {path} est un lien symbolique. "
            "Utilisez le chemin réel du fichier, ou supprimez le lien."
        )
    if exc.errno == errno.ENOENT:
        return error_cls(f"{label} introuvable: {path}")
    return error_cls(f"Impossible d'ouvrir {label} {path}: {exc}")


def read_private_text(path: Path, *, error_cls: type[SidecarError], label: str) -> str:
    """Lit un fichier texte privé, en refusant liens, modes trop larges et excès de taille."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise _open_error(path, exc, error_cls=error_cls, label=label) from exc

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise error_cls(f"{label} {path} n'est pas un fichier régulier.")
        mode = stat.S_IMODE(info.st_mode)
        if mode & 0o077:
            raise error_cls(
                f"{label} {path} est accessible au groupe ou aux autres "
                f"(mode {mode:o}). Corrigez avec: chmod 600 {path}"
            )
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            content = handle.read(MAX_PRIVATE_FILE_BYTES + 1)
        if len(content) > MAX_PRIVATE_FILE_BYTES:
            raise error_cls(f"{label} {path} est trop volumineux.")
        return content
    except UnicodeDecodeError as exc:
        raise error_cls(f"{label} {path} n'est pas un texte UTF-8 valide.") from exc
    finally:
        if fd >= 0:
            os.close(fd)
