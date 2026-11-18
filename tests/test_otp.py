from __future__ import annotations

import io
import os

import pytest

from fortuneo_sidecar.errors import OTPRequired
from fortuneo_sidecar.otp import OTPProvider


def test_otp_from_env():
    provider = OTPProvider(env={"FORTUNEO_OTP": "123456"}, interactive=False)
    assert provider.mode() == "env"
    assert provider.get_code() == "123456"
    with pytest.raises(OTPRequired, match="déjà été utilisé"):
        provider.get_code()


def test_otp_from_private_file(tmp_path):
    otp_file = tmp_path / "otp.txt"
    otp_file.write_text("654321\n", encoding="utf-8")
    os.chmod(otp_file, 0o600)
    provider = OTPProvider(env={}, otp_file=otp_file, interactive=False)
    assert provider.mode() == "file"
    assert provider.get_code() == "654321"


def test_otp_file_must_be_private(tmp_path):
    otp_file = tmp_path / "otp.txt"
    otp_file.write_text("654321\n", encoding="utf-8")
    os.chmod(otp_file, 0o644)
    provider = OTPProvider(env={}, otp_file=otp_file, interactive=False)
    with pytest.raises(OTPRequired, match="chmod 600"):
        provider.get_code()


def test_otp_probe_checks_env_code():
    assert OTPProvider(env={"FORTUNEO_OTP": "123456"}, interactive=False).probe() is None
    assert "4 à 8" in OTPProvider(env={"FORTUNEO_OTP": "abc"}, interactive=False).probe()


def test_otp_probe_checks_file(tmp_path):
    otp_file = tmp_path / "otp.txt"
    otp_file.write_text("654321\n", encoding="utf-8")
    os.chmod(otp_file, 0o600)
    assert OTPProvider(env={}, otp_file=otp_file, interactive=False).probe() is None

    os.chmod(otp_file, 0o644)
    assert "chmod 600" in OTPProvider(env={}, otp_file=otp_file, interactive=False).probe()

    missing = tmp_path / "missing.txt"
    assert "introuvable" in OTPProvider(env={}, otp_file=missing, interactive=False).probe()


def test_otp_stdin_is_explicit():
    provider = OTPProvider(
        env={},
        stdin=io.StringIO("123456\n"),
        stderr=io.StringIO(),
        interactive=False,
        allow_stdin=True,
    )
    assert provider.mode() == "stdin"
    assert provider.get_code() == "123456"


def test_otp_unavailable_without_channel():
    provider = OTPProvider(env={}, stdin=io.StringIO("123456\n"), interactive=False)
    assert provider.mode() == "unavailable"
    with pytest.raises(OTPRequired, match="validation 2FA"):
        provider.get_code()


def test_otp_code_is_registered_as_secret():
    secrets: list[str] = []
    provider = OTPProvider(
        env={"FORTUNEO_OTP": "123456"},
        interactive=False,
        secrets=secrets,
    )
    assert provider.get_code() == "123456"
    assert secrets == ["123456"]


def test_otp_rejects_non_numeric_code():
    provider = OTPProvider(env={"FORTUNEO_OTP": "abc"}, interactive=False)
    with pytest.raises(OTPRequired, match="chiffres"):
        provider.get_code()


def test_otp_file_symlink_is_rejected(tmp_path):
    real = tmp_path / "real-otp.txt"
    real.write_text("654321\n", encoding="utf-8")
    os.chmod(real, 0o600)
    link = tmp_path / "otp.txt"
    link.symlink_to(real)

    provider = OTPProvider(env={}, otp_file=link, interactive=False)
    with pytest.raises(OTPRequired, match="lien symbolique"):
        provider.get_code()
    assert "lien symbolique" in (provider.probe() or "")


def test_otp_file_is_size_limited(tmp_path):
    otp_file = tmp_path / "otp.txt"
    otp_file.write_text("1" * (64 * 1024 + 1), encoding="utf-8")
    os.chmod(otp_file, 0o600)

    provider = OTPProvider(env={}, otp_file=otp_file, interactive=False)
    with pytest.raises(OTPRequired, match="trop volumineux"):
        provider.get_code()
