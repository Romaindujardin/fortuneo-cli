from __future__ import annotations

import os
import stat

import pytest

from fortuneo_sidecar.errors import UsageError
from fortuneo_sidecar.paths import SidecarPaths


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _directories(paths: SidecarPaths):
    return (paths.root, paths.config_dir, paths.data_dir, paths.cache_dir, paths.home_dir)


def test_ensure_creates_private_directories(tmp_path):
    paths = SidecarPaths.from_root(tmp_path / "runtime").ensure()

    for directory in _directories(paths):
        assert directory.is_dir()
        assert _mode(directory) == 0o700


def test_ensure_does_not_chmod_preexisting_directories(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    config = root / "config"
    config.mkdir()
    os.chmod(root, 0o755)
    os.chmod(config, 0o755)

    paths = SidecarPaths.from_root(root).ensure()

    assert _mode(root) == 0o755
    assert _mode(config) == 0o755
    # Les sous-répertoires absents sont bien créés en privé.
    assert _mode(paths.data_dir) == 0o700
    assert _mode(paths.cache_dir) == 0o700
    assert _mode(paths.home_dir) == 0o700


def test_ensure_rejects_a_non_directory_root(tmp_path):
    root = tmp_path / "runtime"
    root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(UsageError, match="n'est pas un répertoire"):
        SidecarPaths.from_root(root).ensure()


def test_state_file_symlink_is_rejected(tmp_path):
    paths = SidecarPaths.from_root(tmp_path / "runtime").ensure()
    target = tmp_path / "target.yml"
    target.write_text("cookies: {}\n", encoding="utf-8")
    paths.state_file.symlink_to(target)

    with pytest.raises(UsageError, match="lien symbolique"):
        paths.check_state_file()
    assert target.exists()


def test_state_file_must_be_a_regular_file(tmp_path):
    paths = SidecarPaths.from_root(tmp_path / "runtime").ensure()
    paths.state_file.mkdir()

    with pytest.raises(UsageError, match="pas un fichier régulier"):
        paths.check_state_file()


def test_ensure_rejects_a_non_directory_subdirectory(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "data").write_text("oops", encoding="utf-8")

    with pytest.raises(UsageError, match="data"):
        SidecarPaths.from_root(root).ensure()
