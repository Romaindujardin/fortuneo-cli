from __future__ import annotations

import os
import stat
from datetime import date
from decimal import Decimal

import pytest
from woob.capabilities.bank import Account
from woob.exceptions import BrowserQuestion, ModuleLoadError, NeedInteractiveFor2FA
from woob.tools.value import Value

from fortuneo_sidecar.credentials import Credentials
from fortuneo_sidecar.engine import FortuneoEngine
from fortuneo_sidecar.errors import (
    AmbiguousAccount,
    AuthenticationError,
    ModuleMissing,
    NotFoundError,
    OTPRequired,
    UnsupportedAuth,
    UsageError,
)
from fortuneo_sidecar.otp import OTPProvider
from fortuneo_sidecar.paths import SidecarPaths

from .conftest import FakeBackend, make_account, make_investment, make_transaction


def make_engine(tmp_path, backend: FakeBackend, *, otp: str | None = None) -> FortuneoEngine:
    otp_provider = OTPProvider(
        env={"FORTUNEO_OTP": otp} if otp else {},
        interactive=False,
    )
    return FortuneoEngine(
        Credentials("login", "password"),
        SidecarPaths.from_root(tmp_path).ensure(),
        otp_provider=otp_provider,
        backend_factory=lambda: backend,
    )


def test_accounts_with_otp_flow(tmp_path, sample_accounts):
    backend = FakeBackend(sample_accounts, require_otp=True, expected_otp="123456")
    engine = make_engine(tmp_path, backend, otp="123456")
    assert [account.id for account in engine.accounts()] == ["acc-checking", "acc-pea"]
    # Les secrets transitoires sont effacés après une opération réussie.
    assert backend.config["code"].get() is None
    assert backend._browser.sms_form is None
    assert backend._browser.need_reload_state is False
    engine.close()
    assert backend.deinit_called is True


def test_generator_is_materialized_before_otp_retry(tmp_path, sample_accounts):
    backend = FakeBackend(sample_accounts, raise_after_first_yield_once=True)
    engine = make_engine(tmp_path, backend, otp="123456")
    assert [account.id for account in engine.accounts()] == ["acc-checking", "acc-pea"]
    assert backend.generator_calls == 2


def test_otp_required_when_no_channel(tmp_path, sample_accounts):
    backend = FakeBackend(sample_accounts, require_otp=True)
    engine = make_engine(tmp_path, backend, otp=None)
    with pytest.raises(OTPRequired, match="validation 2FA"):
        engine.accounts()


def test_wrong_otp_maps_to_authentication_error(tmp_path, sample_accounts):
    backend = FakeBackend(sample_accounts, require_otp=True, expected_otp="123456")
    engine = make_engine(tmp_path, backend, otp="000000")
    with pytest.raises(AuthenticationError, match="Code SMS incorrect"):
        engine.accounts()


def test_need_interactive_maps_to_otp_required(tmp_path, sample_accounts):
    class InteractiveBackend(FakeBackend):
        def iter_accounts(self):
            raise NeedInteractiveFor2FA()

    engine = make_engine(tmp_path, InteractiveBackend(sample_accounts), otp="123456")
    with pytest.raises(OTPRequired, match="2FA"):
        engine.accounts()


def test_unsupported_interactive_field_is_rejected(tmp_path, sample_accounts):
    class RecipientQuestionBackend(FakeBackend):
        def iter_accounts(self):
            raise BrowserQuestion(Value("recipient", label="Bénéficiaire à ajouter"))

    engine = make_engine(tmp_path, RecipientQuestionBackend(sample_accounts), otp="123456")
    with pytest.raises(UnsupportedAuth, match="recipient"):
        engine.accounts()


def test_find_account_by_id_number_iban_and_label(tmp_path, sample_accounts):
    checking = sample_accounts[0]
    checking.iban = "FR7612345678901234567890123"
    backend = FakeBackend(sample_accounts)
    engine = make_engine(tmp_path, backend)

    assert engine.find_account("acc-checking").id == "acc-checking"
    assert engine.find_account("123456789").id == "acc-checking"
    assert engine.find_account("FR7612345678901234567890123").id == "acc-checking"
    assert engine.find_account("compte courant").id == "acc-checking"

    with pytest.raises(NotFoundError):
        engine.find_account("inconnu")


def test_find_account_without_identifier_requires_single_account(tmp_path, sample_accounts):
    engine = make_engine(tmp_path, FakeBackend(sample_accounts))
    with pytest.raises(UsageError, match="Plusieurs comptes"):
        engine.find_account(None)

    one = make_engine(tmp_path, FakeBackend([sample_accounts[0]]))
    assert one.find_account(None).id == "acc-checking"

    empty = make_engine(tmp_path, FakeBackend([]))
    with pytest.raises(NotFoundError, match="Aucun compte"):
        empty.find_account(None)


def test_ambiguous_account(tmp_path, sample_accounts):
    duplicate = make_account("acc-other", label="Compte courant")
    engine = make_engine(tmp_path, FakeBackend([sample_accounts[0], duplicate]))
    with pytest.raises(AmbiguousAccount):
        engine.find_account("Compte courant")


def test_transactions_filter_sort_and_limit(tmp_path, sample_accounts, sample_transactions):
    backend = FakeBackend(
        [sample_accounts[0]],
        histories={"acc-checking": sample_transactions},
    )
    engine = make_engine(tmp_path, backend)
    account, items, warnings = engine.transactions(
        "acc-checking",
        since="2026-02-01",
        limit=1,
    )
    assert account.id == "acc-checking"
    assert [item.id for item in items] == ["tx-3"]
    assert warnings == []


def test_transactions_include_coming(tmp_path, sample_accounts, sample_transactions):
    coming = make_transaction("tx-coming", day=date(2026, 4, 1), label="CARTE À VENIR", coming=True)
    backend = FakeBackend(
        [sample_accounts[0]],
        histories={"acc-checking": sample_transactions},
        comings={"acc-checking": [coming]},
    )
    engine = make_engine(tmp_path, backend)
    _, items, warnings = engine.transactions("acc-checking", include_coming=True)
    assert [item.id for item in items] == ["tx-coming", "tx-3", "tx-2", "tx-1"]
    assert warnings == []


def test_transactions_invalid_date(tmp_path, sample_accounts):
    engine = make_engine(tmp_path, FakeBackend([sample_accounts[0]]))
    with pytest.raises(UsageError, match="YYYY-MM-DD"):
        engine.transactions("acc-checking", since="2026-13-99")


def test_positions(tmp_path, sample_accounts, sample_investments):
    backend = FakeBackend(
        sample_accounts,
        investments={"acc-pea": sample_investments},
    )
    engine = make_engine(tmp_path, backend)
    account, items = engine.positions("acc-pea")
    assert account.id == "acc-pea"
    assert items[0].label == "ETF Monde"
    assert engine.accounts()[1].balance == Decimal("5000.00")


def test_export_partial_errors(tmp_path, sample_accounts, sample_transactions):
    sample_accounts[1]._investment_link = "https://mabanque.fortuneo.fr/pea"
    backend = FakeBackend(
        sample_accounts,
        histories={"acc-checking": sample_transactions},
        fail_positions_for={"acc-pea"},
    )
    engine = make_engine(tmp_path, backend)
    payload = engine.export()

    assert payload["schema"] == "fortuneo-sidecar/export/v1"
    assert payload["status"] == "partial"
    assert len(payload["accounts"]) == 2
    checking = next(item for item in payload["accounts"] if item["id"] == "acc-checking")
    assert len(checking["transactions"]) == 3
    assert payload["errors"][0]["accountId"] == "acc-pea"
    assert payload["errors"][0]["step"] == "positions"
    assert payload["totals"]["balances"]["EUR"] == 6234.56


def test_export_without_positions(tmp_path, sample_accounts, sample_transactions):
    backend = FakeBackend(sample_accounts, histories={"acc-checking": sample_transactions})
    engine = make_engine(tmp_path, backend)
    payload = engine.export(include_positions=False)
    assert all(account["positions"] == [] for account in payload["accounts"])
    assert payload["status"] == "ok"


def test_real_vendored_module_loads_without_network(tmp_path):
    engine = FortuneoEngine(
        Credentials("dummy", "dummy"),
        SidecarPaths.from_root(tmp_path).ensure(),
    )
    backend = engine._get_backend()
    try:
        assert backend.NAME == "fortuneo"
        assert set(backend.config.keys()) >= {
            "login",
            "password",
            "code",
            "sca_code",
            "request_information",
        }
    finally:
        engine.close()


def test_missing_vendor_module(tmp_path, monkeypatch):
    empty = tmp_path / "empty-vendor"
    empty.mkdir()
    monkeypatch.setenv("FORTUNEO_SIDECAR_MODULES", str(empty))
    engine = FortuneoEngine(Credentials("dummy", "dummy"), SidecarPaths.from_root(tmp_path).ensure())
    with pytest.raises(ModuleMissing, match="Module Woob Fortuneo introuvable"):
        engine._get_backend()


def test_module_load_error_maps_to_module_missing(tmp_path):
    def failing_factory():
        raise ModuleLoadError("fortuneo", "broken module")

    engine = FortuneoEngine(
        Credentials("dummy", "dummy"),
        SidecarPaths.from_root(tmp_path).ensure(),
        backend_factory=failing_factory,
    )
    with pytest.raises(ModuleMissing, match="broken module"):
        engine.accounts()


def test_close_scrubs_otp_before_deinit(tmp_path, sample_accounts):
    backend = FakeBackend(sample_accounts)
    backend.config["code"].set("123456")
    backend.config["sca_code"] = type(backend.config["code"])("654321")
    backend._browser.sms_form = {"otp": "123456"}
    backend._browser.need_reload_state = True

    observed: dict[str, object] = {}
    original_deinit = backend.deinit

    def deinit():
        observed["sms_form"] = backend._browser.sms_form
        observed["need_reload_state"] = backend._browser.need_reload_state
        observed["code"] = backend.config["code"].get()
        observed["sca_code"] = backend.config["sca_code"].get()
        original_deinit()

    backend.deinit = deinit
    engine = make_engine(tmp_path, backend)
    engine._get_backend()
    engine.close()

    assert observed == {
        "sms_form": None,
        "need_reload_state": False,
        "code": None,
        "sca_code": None,
    }


def test_close_hardens_state_file(tmp_path, sample_accounts):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    paths.state_file.write_text("cookies: {}\n", encoding="utf-8")
    os.chmod(paths.state_file, 0o644)

    engine = FortuneoEngine(
        Credentials("login", "password"),
        paths,
        backend_factory=lambda: FakeBackend(sample_accounts),
        persist_state=True,
    )
    engine.close()

    assert stat.S_IMODE(paths.state_file.stat().st_mode) == 0o600


def test_default_does_not_persist_state(tmp_path, sample_accounts):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    paths.state_file.write_text("cookies: {}\n", encoding="utf-8")

    engine = FortuneoEngine(
        Credentials("login", "password"),
        paths,
        backend_factory=lambda: FakeBackend(sample_accounts),
    )
    engine._get_backend()
    engine.close()

    assert not paths.state_file.exists()


def test_default_disables_woob_storage(tmp_path):
    engine = FortuneoEngine(
        Credentials("dummy", "dummy"),
        SidecarPaths.from_root(tmp_path).ensure(),
    )
    backend = engine._get_backend()
    try:
        assert backend.storage.storage is None
    finally:
        engine.close()


def test_state_opt_in_uses_woob_storage(tmp_path):
    engine = FortuneoEngine(
        Credentials("dummy", "dummy"),
        SidecarPaths.from_root(tmp_path).ensure(),
        persist_state=True,
    )
    backend = engine._get_backend()
    try:
        assert backend.storage.storage is not None
    finally:
        engine.close()


def test_engine_refuses_state_symlink(tmp_path):
    paths = SidecarPaths.from_root(tmp_path).ensure()
    target = tmp_path / "target.yml"
    target.write_text("cookies: {}\n", encoding="utf-8")
    paths.state_file.symlink_to(target)

    engine = FortuneoEngine(Credentials("dummy", "dummy"), paths, persist_state=True)
    with pytest.raises(UsageError, match="lien symbolique"):
        engine._get_backend()
    assert target.exists()


def test_export_error_messages_are_redacted(tmp_path, sample_accounts):
    sample_accounts[1]._investment_link = "https://mabanque.fortuneo.fr/pea"

    class LeakyBackend(FakeBackend):
        def iter_investment(self, account):
            if str(account.id) == "acc-pea":
                raise RuntimeError("connexion refusée: password=password login=login")
            return []

    engine = make_engine(tmp_path, LeakyBackend(sample_accounts))
    payload = engine.export()

    assert payload["status"] == "partial"
    message = payload["errors"][0]["message"]
    assert "password" not in message
    assert "login" not in message
    assert "***" in message


def test_export_totals_position_values(tmp_path, sample_accounts, sample_investments):
    backend = FakeBackend(sample_accounts, investments={"acc-pea": sample_investments})
    payload = make_engine(tmp_path, backend).export(include_transactions=False)

    assert payload["totals"]["positionValues"]["EUR"] == 1055.0
    assert "positions" not in payload["totals"]
