"""Chemins et isolation du sidecar.

Tout ce que le sidecar écrit (cookies Woob, état 2FA, credentials) reste sous
un répertoire unique, avec des permissions privées. Le processus exporte aussi
un HOME/XDG sandbox pour éviter qu'une dépendance ne retombe sur les fichiers
de l'utilisateur.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import UsageError

APP_DIR_NAME = "fortuneo-sidecar"


def _ensure_private_directory(path: Path) -> None:
    """Crée un répertoire privé, sans toucher à un répertoire préexistant.

    Un répertoire déjà présent peut appartenir à l'utilisateur ou être partagé
    (par exemple ``/tmp``) : le sidecar ne doit jamais en modifier les
    permissions. Seuls les répertoires qu'il crée lui-même passent en ``0700``.
    """
    if path.exists():
        if not path.is_dir():
            raise UsageError(
                f"Le chemin {path} existe mais n'est pas un répertoire. "
                "Choisissez un autre --data-dir ou FORTUNEO_SIDECAR_HOME."
            )
        return

    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        if not path.is_dir():
            raise UsageError(
                f"Le chemin {path} existe mais n'est pas un répertoire. "
                "Choisissez un autre --data-dir ou FORTUNEO_SIDECAR_HOME."
            ) from None
        return
    except OSError as exc:
        raise UsageError(f"Impossible de créer le répertoire {path}: {exc}") from exc

    # mkdir() applique le umask au mode ; on force donc 0700 sur le répertoire
    # créé, et uniquement sur celui-ci.
    os.chmod(path, 0o700)


@dataclass(frozen=True)
class SidecarPaths:
    root: Path
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    home_dir: Path
    credentials_file: Path
    state_file: Path

    @classmethod
    def from_root(cls, root: str | os.PathLike[str]) -> "SidecarPaths":
        root_path = Path(root).expanduser().resolve()
        config_dir = root_path / "config"
        data_dir = root_path / "data"
        cache_dir = root_path / "cache"
        home_dir = root_path / "home"
        return cls(
            root=root_path,
            config_dir=config_dir,
            data_dir=data_dir,
            cache_dir=cache_dir,
            home_dir=home_dir,
            credentials_file=config_dir / "credentials.json",
            state_file=data_dir / "state.yml",
        )

    @classmethod
    def default(cls) -> "SidecarPaths":
        configured = os.environ.get("FORTUNEO_SIDECAR_HOME")
        if configured:
            return cls.from_root(configured)
        return cls.from_root(Path.home() / ".local" / "share" / APP_DIR_NAME)

    def ensure(self) -> "SidecarPaths":
        for directory in (
            self.root,
            self.config_dir,
            self.data_dir,
            self.cache_dir,
            self.home_dir,
        ):
            _ensure_private_directory(directory)
        return self

    def check_state_file(self) -> None:
        """Refuse un fichier d'état qui n'est pas un fichier régulier.

        Woob écrit les cookies de session et parfois un formulaire 2FA dans ce
        fichier. Un lien symbolique pourrait rediriger cette écriture vers un
        autre fichier de l'utilisateur : on l'interdit explicitement.
        """
        state = self.state_file
        if state.is_symlink():
            raise UsageError(
                f"Le fichier d'état {state} est un lien symbolique. "
                "Supprimez ce lien après vérification avant de relancer le sidecar."
            )
        if state.exists() and not state.is_file():
            raise UsageError(
                f"Le chemin d'état {state} existe mais n'est pas un fichier régulier. "
                "Supprimez-le ou choisissez un autre répertoire de données."
            )

    def apply_sandbox_env(self) -> dict[str, str | None]:
        """Isole les bibliothèques tierces dans le répertoire du sidecar.

        Retourne les valeurs précédentes pour pouvoir restaurer l'environnement
        à la fin de la commande (utile quand ``main()`` est appelé en
        bibliothèque ou dans des tests).
        """
        keys = ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME")
        previous = {key: os.environ.get(key) for key in keys}
        os.environ["HOME"] = str(self.home_dir)
        os.environ["XDG_CONFIG_HOME"] = str(self.config_dir)
        os.environ["XDG_DATA_HOME"] = str(self.data_dir)
        os.environ["XDG_CACHE_HOME"] = str(self.cache_dir)
        return previous

    @staticmethod
    def restore_env(previous: dict[str, str | None]) -> None:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    @property
    def vendor_modules_dir(self) -> Path:
        """Répertoire contenant woob_modules/fortuneo (modules vendorisés)."""
        override = os.environ.get("FORTUNEO_SIDECAR_MODULES")
        if override:
            return Path(override).expanduser().resolve()
        return Path(__file__).resolve().parent / "vendor" / "woob_modules"
