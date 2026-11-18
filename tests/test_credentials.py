from __future__ import annotations

import json
import os
import stat

import pytest

from fortuneo_sidecar.credentials import (
    Credentials,
    credentials_secrets,
    credentials_summary,
    delete_credentials,
    forget_stored_password,
    inspect_credentials,
    load_credentials,
    load_login_file,
    mask_login,
    redact,
    save_login,
)
from fortuneo_sidecar.errors import CredentialsError, PasswordRequired
from fortuneo_sidecar.paths import SidecarPaths


def _write_legacy(paths: SidecarPaths, login: str = "12345678", password: str = "legacy-secret") -> None:
    paths.credentials_file.write_text(
        json.dumps({"login": login, "password": password}),
        encoding="utf-8",
    )
    os.chmod(paths.credentials_file, 0o600)


def test_save_login_writes_only_login_with_private_mode(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    save_login(paths.credentials_file, "12345678")

    document = json.loads(paths.credentials_file.read_text(encoding="utf-8"))
    assert document == {"login": "12345678"}
    assert "password" not in document
    assert stat.S_IMODE(paths.credentials_file.stat().st_mode) == 0o600
    assert load_login_file(paths.credentials_file) == "12345678"


def test_password_is_required_at_runtime_and_comes_from_memory(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    save_login(paths.credentials_file, "12345678")

    with pytest.raises(PasswordRequired, match="FORTUNEO_PASSWORD"):
        load_credentials(paths, env={})

    loaded = load_credentials(paths, env={}, password="s3cret")
    assert loaded.login == "12345678"
    assert loaded.password == "s3cret"
    assert loaded.source == "file+prompt"


def test_environment_has_priority_over_file(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    save_login(paths.credentials_file, "file-login")
    loaded = load_credentials(
        paths,
        env={"FORTUNEO_LOGIN": "env-login", "FORTUNEO_PASSWORD": "env-pass"},
    )
    assert loaded.login == "env-login"
    assert loaded.password == "env-pass"
    assert loaded.source == "env"


def test_environment_password_completes_file_login(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    save_login(paths.credentials_file, "file-login")
    loaded = load_credentials(paths, env={"FORTUNEO_PASSWORD": "env-pass"})
    assert loaded.login == "file-login"
    assert loaded.password == "env-pass"
    assert loaded.source == "file+env"


def test_environment_login_still_requires_a_password(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    with pytest.raises(PasswordRequired):
        load_credentials(paths, env={"FORTUNEO_LOGIN": "login"})


def test_legacy_password_file_is_rejected(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    _write_legacy(paths)

    info = inspect_credentials(paths.credentials_file)
    assert info.password_stored is True
    assert info.login == "12345678"

    with pytest.raises(CredentialsError, match="contient encore un mot de passe"):
        load_login_file(paths.credentials_file)
    with pytest.raises(CredentialsError, match="auth --forget-password"):
        load_credentials(paths, env={})


def test_forget_stored_password_removes_legacy_secret(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    _write_legacy(paths)

    assert forget_stored_password(paths.credentials_file) is True

    document = json.loads(paths.credentials_file.read_text(encoding="utf-8"))
    assert document == {"login": "12345678"}
    assert "legacy-secret" not in paths.credentials_file.read_text(encoding="utf-8")
    assert inspect_credentials(paths.credentials_file).password_stored is False
    assert stat.S_IMODE(paths.credentials_file.stat().st_mode) == 0o600

    loaded = load_credentials(paths, env={"FORTUNEO_PASSWORD": "fresh"})
    assert loaded.login == "12345678"
    assert loaded.password == "fresh"


def test_forget_stored_password_is_a_noop_for_login_only_file(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    save_login(paths.credentials_file, "12345678")
    assert forget_stored_password(paths.credentials_file) is False


def test_credentials_summary_never_exposes_the_password(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    _write_legacy(paths)

    summary = credentials_summary(inspect_credentials(paths.credentials_file), source="file")

    assert summary["loginMasked"] == "1***"
    assert summary["passwordStored"] is True
    assert "legacy-secret" not in json.dumps(summary)
    assert "password" not in summary


def test_insecure_file_mode_is_rejected(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    paths.credentials_file.write_text(json.dumps({"login": "x"}), encoding="utf-8")
    os.chmod(paths.credentials_file, 0o644)
    with pytest.raises(CredentialsError, match="chmod 600"):
        load_login_file(paths.credentials_file)


def test_invalid_json_is_rejected(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    paths.credentials_file.write_text("{not json", encoding="utf-8")
    os.chmod(paths.credentials_file, 0o600)
    with pytest.raises(CredentialsError, match="JSON invalide"):
        load_login_file(paths.credentials_file)


def test_missing_login_file_is_rejected(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    with pytest.raises(CredentialsError, match="introuvable"):
        load_credentials(paths, env={})


def test_delete_credentials(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    save_login(paths.credentials_file, "12345678")
    assert delete_credentials(paths.credentials_file) is True
    assert delete_credentials(paths.credentials_file) is False


def test_mask_and_redact():
    assert mask_login("jean.dupont@example.com") == "j***"
    assert mask_login("") == "***"
    assert redact("login=abc password=secret", ["abc", "secret"]) == "login=*** password=***"
    assert credentials_secrets(Credentials("abc", "secret"), "123456") == ["secret", "abc", "123456"]


def test_redact_masks_encoded_and_structured_secrets():
    secret = "p@ss word&x"
    encoded = "p%40ss%20word%26x"
    text = (
        f"GET /login?password={encoded}&session_id=abc HTTP/1.1\n"
        "Authorization: Bearer tok_123456\n"
        '{"code": "654321", "access_token": "tok_abcdef"}\n'
        "access_code=999999&state=xyz"
    )

    cleaned = redact(text, [secret])

    for leaked in (secret, encoded, "tok_123456", "654321", "tok_abcdef", "999999", "xyz"):
        assert leaked not in cleaned
    assert cleaned.count("***") >= 6


def test_redact_value_is_recursive():
    from fortuneo_sidecar.credentials import redact_value

    payload = {
        "message": "password=hunter2",
        "nested": [{"code": "123456"}, "hunter2"],
        "count": 3,
    }
    cleaned = redact_value(payload, ["hunter2"])

    assert cleaned["message"] == "password=***"
    assert cleaned["nested"] == [{"code": "123456"}, "***"]
    assert cleaned["count"] == 3


def test_credentials_symlink_is_rejected(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    real = tmp_path / "real-credentials.json"
    real.write_text(json.dumps({"login": "x"}), encoding="utf-8")
    os.chmod(real, 0o600)
    paths.credentials_file.symlink_to(real)

    with pytest.raises(CredentialsError, match="lien symbolique"):
        load_login_file(paths.credentials_file)

    with pytest.raises(CredentialsError, match="lien symbolique"):
        forget_stored_password(paths.credentials_file)

    with pytest.raises(CredentialsError, match="lien symbolique"):
        delete_credentials(paths.credentials_file)
    assert real.exists()
