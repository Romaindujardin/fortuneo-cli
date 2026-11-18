from __future__ import annotations

import io
import json
import logging
import os
import stat

import pytest

from woob.capabilities.bank import Account

from fortuneo_sidecar.cli import _resolve_runtime_credentials, build_parser, main
from fortuneo_sidecar.credentials import (
    Credentials,
    load_login_file,
    save_login,
)
from fortuneo_sidecar.engine import FortuneoEngine
from fortuneo_sidecar.errors import PasswordRequired, UsageError
from fortuneo_sidecar.otp import OTPProvider
from fortuneo_sidecar.paths import SidecarPaths

from .conftest import FakeBackend, make_account, make_investment


def _prepare_root(tmp_path):
    root = tmp_path / "sidecar"
    SidecarPaths.from_root(root).ensure()
    return root


def _set_credentials(monkeypatch, login="12345678", password="secret"):
    monkeypatch.setenv("FORTUNEO_LOGIN", login)
    monkeypatch.setenv("FORTUNEO_PASSWORD", password)


def _make_engine(root, backend, *, otp="123456"):
    provider = OTPProvider(
        env={"FORTUNEO_OTP": otp} if otp else {},
        interactive=False,
    )
    return FortuneoEngine(
        Credentials("12345678", "secret"),
        SidecarPaths.from_root(root).ensure(),
        otp_provider=provider,
        backend_factory=lambda: backend,
    )


def _run(args, *, stdin=None, engine_factory=None):
    out = io.StringIO()
    err = io.StringIO()
    code = main(args, stdout=out, stderr=err, stdin=stdin or io.StringIO(""), engine_factory=engine_factory)
    return code, out.getvalue(), err.getvalue()


def test_accounts_requires_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(tmp_path / "sidecar"))
    monkeypatch.delenv("FORTUNEO_LOGIN", raising=False)
    monkeypatch.delenv("FORTUNEO_PASSWORD", raising=False)

    code, out, err = _run(["accounts"])
    assert code == 3
    assert out == ""
    payload = json.loads(err)
    assert payload["error"]["code"] == "CREDENTIALS_MISSING"


def test_accounts_with_fake_engine(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    _set_credentials(monkeypatch)
    backend = FakeBackend([make_account("acc-checking", label="Compte courant")])

    code, out, err = _run(["accounts"], engine_factory=lambda: _make_engine(root, backend))
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["schema"] == "fortuneo-sidecar/accounts/v1"
    assert payload["accounts"][0]["id"] == "acc-checking"
    assert payload["accounts"][0]["balance"] == 1234.56


def test_transactions_requires_account_with_several_accounts(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    _set_credentials(monkeypatch)
    backend = FakeBackend([make_account("a"), make_account("b", label="Autre")])

    code, out, err = _run(["transactions"], engine_factory=lambda: _make_engine(root, backend))
    assert code == 2
    assert out == ""
    assert json.loads(err)["error"]["code"] == "USAGE_ERROR"


def test_transactions_filter_and_limit(tmp_path, monkeypatch, sample_transactions):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    _set_credentials(monkeypatch)
    backend = FakeBackend(
        [make_account("acc-checking")],
        histories={"acc-checking": sample_transactions},
    )

    code, out, _ = _run(
        ["transactions", "--account", "acc-checking", "--since", "2026-02-01", "--limit", "1"],
        engine_factory=lambda: _make_engine(root, backend),
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["schema"] == "fortuneo-sidecar/transactions/v1"
    assert [item["id"] for item in payload["transactions"]] == ["tx-3"]
    assert payload["transactions"][0]["dedupKey"].startswith("fortuneo:acc-checking:")


def test_positions_output(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    _set_credentials(monkeypatch)
    pea = make_account(
        "acc-pea",
        label="PEA",
        account_type=Account.TYPE_PEA,
        history_link=None,
        investment_link="https://example.invalid/pea",
    )
    backend = FakeBackend([pea], investments={"acc-pea": [make_investment()]})

    code, out, _ = _run(
        ["positions", "--account", "acc-pea"],
        engine_factory=lambda: _make_engine(root, backend),
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["positions"][0]["code"] == "FR0000000001"
    assert payload["totals"]["EUR"] == 1055.0


def test_export_output(tmp_path, monkeypatch, sample_accounts, sample_transactions):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    _set_credentials(monkeypatch)
    backend = FakeBackend(
        sample_accounts,
        histories={"acc-checking": sample_transactions},
    )

    code, out, _ = _run(["export"], engine_factory=lambda: _make_engine(root, backend))
    assert code == 0
    payload = json.loads(out)
    assert payload["schema"] == "fortuneo-sidecar/export/v1"
    assert payload["status"] == "ok"
    assert len(payload["accounts"]) == 2


def test_auth_from_env_and_check(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    _set_credentials(monkeypatch, "jean.dupont", "motdepasse")

    code, out, err = _run(["auth", "--from-env"])
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["loginMasked"] == "j***"
    assert payload["passwordStored"] is False
    assert any("jamais enregistré" in warning for warning in payload["warnings"])

    credentials_file = SidecarPaths.from_root(root).credentials_file
    assert stat.S_IMODE(credentials_file.stat().st_mode) == 0o600
    document = json.loads(credentials_file.read_text(encoding="utf-8"))
    assert document == {"login": "jean.dupont"}
    assert "motdepasse" not in credentials_file.read_text(encoding="utf-8")
    assert load_login_file(credentials_file) == "jean.dupont"

    code, out, _ = _run(["auth", "--check"])
    assert code == 0
    payload = json.loads(out)
    assert payload["credentials"]["exists"] is True
    assert payload["credentials"]["passwordStored"] is False


def test_auth_interactive_stores_login_only(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    monkeypatch.delenv("FORTUNEO_LOGIN", raising=False)
    monkeypatch.delenv("FORTUNEO_PASSWORD", raising=False)

    code, out, err = _run(["auth"], stdin=io.StringIO("12345678\n"))
    assert code == 0, err
    assert json.loads(out)["loginMasked"] == "1***"
    credentials_file = SidecarPaths.from_root(root).credentials_file
    assert json.loads(credentials_file.read_text(encoding="utf-8")) == {"login": "12345678"}


def test_auth_check_rejects_legacy_password_file(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    credentials_file = SidecarPaths.from_root(root).credentials_file
    credentials_file.write_text(
        json.dumps({"login": "12345678", "password": "legacy-secret"}),
        encoding="utf-8",
    )
    os.chmod(credentials_file, 0o600)

    code, out, err = _run(["auth", "--check"])

    assert code == 3
    assert out == ""
    payload = json.loads(err)
    assert payload["error"]["code"] == "PASSWORD_STORED"
    assert payload["credentials"]["passwordStored"] is True
    assert "legacy-secret" not in err


def test_auth_forget_password_migrates_legacy_file(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    credentials_file = SidecarPaths.from_root(root).credentials_file
    credentials_file.write_text(
        json.dumps({"login": "12345678", "password": "legacy-secret"}),
        encoding="utf-8",
    )
    os.chmod(credentials_file, 0o600)

    code, out, _ = _run(["auth", "--forget-password"])

    assert code == 0
    payload = json.loads(out)
    assert payload["removedPassword"] is True
    assert payload["passwordStored"] is False
    document = json.loads(credentials_file.read_text(encoding="utf-8"))
    assert document == {"login": "12345678"}
    assert "legacy-secret" not in credentials_file.read_text(encoding="utf-8")


def test_auth_delete(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    _set_credentials(monkeypatch)
    assert _run(["auth", "--from-env"])[0] == 0
    code, out, _ = _run(["auth", "--delete"])
    assert code == 0
    assert json.loads(out)["deleted"] is True


def test_doctor_without_credentials(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    monkeypatch.delenv("FORTUNEO_LOGIN", raising=False)
    monkeypatch.delenv("FORTUNEO_PASSWORD", raising=False)

    code, out, _ = _run(["doctor"])
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["ready"] is False
    names = {check["name"] for check in payload["checks"]}
    assert {"python", "woob", "module", "storage", "credentials", "otp"} <= names


def test_doctor_with_credentials(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    _set_credentials(monkeypatch)
    assert _run(["auth", "--from-env"])[0] == 0

    code, out, _ = _run(["doctor"])
    assert code == 0
    payload = json.loads(out)
    assert payload["ready"] is True
    credentials_check = next(c for c in payload["checks"] if c["name"] == "credentials")
    assert credentials_check["detail"]["loginMasked"] == "1***"
    assert credentials_check["detail"]["loginLength"] == 8
    assert credentials_check["detail"]["loginNumeric"] is True


def test_doctor_reports_invalid_otp_file(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    missing = tmp_path / "missing-otp.txt"

    code, out, _ = _run(["doctor", "--otp-file", str(missing), "--no-interactive"])
    assert code == 0
    otp_check = next(c for c in json.loads(out)["checks"] if c["name"] == "otp")
    assert otp_check["ok"] is False
    assert "introuvable" in otp_check["detail"]["error"]


def test_data_dir_before_and_after_subcommand(tmp_path, monkeypatch):
    root = tmp_path / "other-root"
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(tmp_path / "ignored"))

    code, out, _ = _run(["--data-dir", str(root), "doctor"])
    assert code == 0
    payload = json.loads(out)
    storage = next(c for c in payload["checks"] if c["name"] == "storage")
    assert storage["detail"]["dataDir"] == str(root.resolve() / "data")

    code, out, _ = _run(["doctor", "--data-dir", str(root)])
    assert code == 0
    payload = json.loads(out)
    storage = next(c for c in payload["checks"] if c["name"] == "storage")
    assert storage["detail"]["dataDir"] == str(root.resolve() / "data")


def test_data_dir_pointing_to_a_file_fails_cleanly(tmp_path, monkeypatch):
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("x", encoding="utf-8")
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(tmp_path / "ignored"))

    code, out, err = _run(["--data-dir", str(file_path), "doctor"])
    assert code == 2
    assert out == ""
    payload = json.loads(err)
    assert payload["error"]["code"] == "USAGE_ERROR"
    assert "n'est pas un répertoire" in payload["error"]["message"]


def test_tilde_paths_are_resolved_before_sandbox(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    monkeypatch.delenv("FORTUNEO_LOGIN", raising=False)
    monkeypatch.delenv("FORTUNEO_PASSWORD", raising=False)

    code, out, _ = _run(["--data-dir", "~/custom-sidecar", "doctor"])
    assert code == 0
    storage = next(c for c in json.loads(out)["checks"] if c["name"] == "storage")
    assert storage["detail"]["dataDir"] == str(home / "custom-sidecar" / "data")

    _set_credentials(monkeypatch)
    code, _, err = _run(["auth", "--from-env", "--credentials-file", "~/creds.json"])
    assert code == 0, err
    assert (home / "creds.json").exists()
    assert not (SidecarPaths.from_root(root).home_dir / "creds.json").exists()


def test_traceback_not_leaked_by_default(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    _set_credentials(monkeypatch)

    class ExplodingBackend(FakeBackend):
        def iter_accounts(self):
            raise RuntimeError("boom with secret")

    code, out, err = _run(
        ["accounts"],
        engine_factory=lambda: _make_engine(root, ExplodingBackend([])),
    )
    assert code == 1
    payload = json.loads(err)
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert "Traceback" not in err


def test_logging_redacts_exception_tracebacks():
    from fortuneo_sidecar.cli import _configure_logging

    stream = io.StringIO()
    _configure_logging(True, stream, ["s3cret"])
    parent = logging.getLogger("fortuneo_sidecar")
    try:
        try:
            raise RuntimeError("password=s3cret")
        except RuntimeError:
            logging.getLogger("fortuneo_sidecar.test").exception("échec de test")
        output = stream.getvalue()
    finally:
        for handler in list(parent.handlers):
            if getattr(handler, "_fortuneo_sidecar", False):
                parent.removeHandler(handler)

    assert "s3cret" not in output
    assert "***" in output
    assert "Traceback" in output


def test_error_details_are_redacted(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    _set_credentials(monkeypatch, "12345678", "secret")

    class ExplodingEngine:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def accounts(self):
            raise UsageError(
                "échec",
                details={"password": "secret", "nested": ["secret", {"login": "12345678"}]},
            )

    code, out, err = _run(["accounts"], engine_factory=lambda: ExplodingEngine())

    assert code == 2
    payload = json.loads(err)
    assert payload["error"]["details"] == {
        "password": "***",
        "nested": ["***", {"login": "***"}],
    }


def test_doctor_warns_about_state_permissions(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    state = SidecarPaths.from_root(root).state_file
    state.write_text("cookies: {}\n", encoding="utf-8")
    os.chmod(state, 0o644)

    code, out, _ = _run(["doctor"])

    assert code == 0
    payload = json.loads(out)
    storage = next(check for check in payload["checks"] if check["name"] == "storage")
    assert storage["ok"] is True
    assert storage["detail"]["stateMode"] == "644"
    assert any("chmod 600" in warning for warning in payload["warnings"])


def test_doctor_rejects_state_symlink(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    target = tmp_path / "target.yml"
    target.write_text("cookies: {}\n", encoding="utf-8")
    SidecarPaths.from_root(root).state_file.symlink_to(target)

    code, out, _ = _run(["doctor"])

    assert code == 1
    payload = json.loads(out)
    storage = next(check for check in payload["checks"] if check["name"] == "storage")
    assert storage["ok"] is False
    assert any("lien symbolique" in warning for warning in payload["warnings"])


def test_resolve_runtime_credentials_from_stdin(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    save_login(paths.credentials_file, "12345678")
    args = build_parser().parse_args(["--password-stdin", "accounts"])

    credentials = _resolve_runtime_credentials(args, paths, io.StringIO("s3cret\n"), io.StringIO())

    assert credentials.login == "12345678"
    assert credentials.password == "s3cret"
    assert credentials.source == "file+prompt"


def test_resolve_runtime_credentials_requires_an_explicit_channel_when_not_tty(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    save_login(paths.credentials_file, "12345678")
    args = build_parser().parse_args(["accounts"])

    with pytest.raises(PasswordRequired):
        _resolve_runtime_credentials(args, paths, io.StringIO(""), io.StringIO())


def test_resolve_runtime_credentials_prompts_on_tty(tmp_path, monkeypatch):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    save_login(paths.credentials_file, "12345678")
    args = build_parser().parse_args(["accounts"])

    class TtyInput(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("fortuneo_sidecar.cli.getpass.getpass", lambda *a, **k: "s3cret")
    credentials = _resolve_runtime_credentials(args, paths, TtyInput(""), io.StringIO())

    assert credentials.password == "s3cret"


def test_password_stdin_and_otp_stdin_are_mutually_exclusive(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))

    code, out, err = _run(["--password-stdin", "--otp-stdin", "accounts"])

    assert code == 2
    assert out == ""
    assert json.loads(err)["error"]["code"] == "USAGE_ERROR"


def test_state_is_opt_in_and_legacy_flag_is_accepted(tmp_path, monkeypatch):
    assert build_parser().parse_args(["accounts"]).state is False
    assert build_parser().parse_args(["accounts", "--state"]).state is True
    assert build_parser().parse_args(["accounts", "--no-state"]).state is False

    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    code, out, err = _run(["--state", "--no-state", "accounts"])
    assert code == 2
    assert out == ""
    assert json.loads(err)["error"]["code"] == "USAGE_ERROR"


def test_doctor_warns_about_a_stored_password(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    credentials_file = SidecarPaths.from_root(root).credentials_file
    credentials_file.write_text(
        json.dumps({"login": "12345678", "password": "legacy-secret"}),
        encoding="utf-8",
    )
    os.chmod(credentials_file, 0o600)

    code, out, _ = _run(["doctor"])

    assert code == 0
    payload = json.loads(out)
    assert payload["ready"] is False
    credentials_check = next(c for c in payload["checks"] if c["name"] == "credentials")
    assert credentials_check["ok"] is False
    assert credentials_check["detail"]["passwordStored"] is True
    assert any("auth --forget-password" in warning for warning in payload["warnings"])


def test_doctor_reports_missing_password_channel(tmp_path, monkeypatch):
    root = _prepare_root(tmp_path)
    monkeypatch.setenv("FORTUNEO_SIDECAR_HOME", str(root))
    save_login(SidecarPaths.from_root(root).credentials_file, "12345678")
    monkeypatch.delenv("FORTUNEO_LOGIN", raising=False)
    monkeypatch.delenv("FORTUNEO_PASSWORD", raising=False)

    code, out, _ = _run(["doctor"])

    assert code == 0
    payload = json.loads(out)
    assert payload["ready"] is False
    credentials_check = next(c for c in payload["checks"] if c["name"] == "credentials")
    assert credentials_check["detail"]["passwordStored"] is False
    assert credentials_check["detail"]["passwordSource"] == "missing"
    assert any("Mot de passe non disponible" in warning for warning in payload["warnings"])
