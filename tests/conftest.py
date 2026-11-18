from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from woob.capabilities.bank import Account, Transaction
from woob.capabilities.bank.wealth import Investment
from woob.capabilities.profile import Profile
from woob.exceptions import BrowserIncorrectPassword, BrowserQuestion
from woob.tools.value import Value


class FakeValue:
    def __init__(self, value=None):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


def make_account(
    account_id: str = "acc-checking",
    *,
    label: str = "Compte courant",
    account_type: int = Account.TYPE_CHECKING,
    balance: Decimal | None = Decimal("1234.56"),
    currency: str = "EUR",
    number: str | None = "123456789",
    iban: str | None = None,
    history_link: str | None = "https://mabanque.fortuneo.fr/history",
    investment_link: str | None = None,
) -> Account:
    account = Account(id=account_id)
    account.label = label
    account.type = account_type
    if balance is not None:
        account.balance = balance
    if currency is not None:
        account.currency = currency
    if number is not None:
        account.number = number
    if iban is not None:
        account.iban = iban
    if history_link is not None:
        account._history_link = history_link
    if investment_link is not None:
        account._investment_link = investment_link
    return account


def make_transaction(
    transaction_id: str = "tx-1",
    *,
    day: date = date(2026, 1, 15),
    label: str = "CARTE 15/01/26 BOULANGERIE",
    amount: Decimal | str = Decimal("-12.34"),
    transaction_type: int = Transaction.TYPE_CARD,
    raw: str | None = None,
    category: str | None = "Alimentation",
    coming: bool | None = None,
) -> Transaction:
    transaction = Transaction(id=transaction_id)
    transaction.date = day
    transaction.rdate = day
    transaction.vdate = day
    transaction.label = label
    transaction.raw = raw or label
    transaction.amount = Decimal(str(amount))
    transaction.type = transaction_type
    if category is not None:
        transaction.category = category
    if coming is not None:
        transaction.coming = coming
    return transaction


def make_investment(
    investment_id: str = "inv-1",
    *,
    label: str = "ETF Monde",
    code: str = "FR0000000001",
    quantity: Decimal = Decimal("10"),
    unitprice: Decimal = Decimal("100.00"),
    unitvalue: Decimal = Decimal("105.50"),
    valuation: Decimal = Decimal("1055.00"),
    currency: str = "EUR",
) -> Investment:
    investment = Investment(id=investment_id)
    investment.label = label
    investment.code = code
    investment.code_type = Investment.CODE_TYPE_ISIN
    investment.stock_symbol = code
    investment.quantity = quantity
    investment.unitprice = unitprice
    investment.unitvalue = unitvalue
    investment.valuation = valuation
    investment.original_currency = currency
    investment.diff = valuation - unitprice * quantity
    investment.diff_ratio = Decimal("0.055")
    investment.portfolio_share = Decimal("0.42")
    investment.vdate = date(2026, 2, 1)
    investment.asset_category = "Actions internationales"
    investment.srri = 4
    return investment


def make_profile() -> Profile:
    profile = Profile()
    profile.name = "Jean Dupont"
    profile.email = "jean.dupont@example.com"
    profile.phone = "+33600000000"
    profile.main_bank = "Fortuneo"
    return profile


class FakeBackend:
    """Backend minimal imitant l'API Module de Woob."""

    def __init__(
        self,
        accounts: list[Account] | None = None,
        *,
        histories: dict[str, list[Transaction]] | None = None,
        comings: dict[str, list[Transaction]] | None = None,
        investments: dict[str, list[Investment]] | None = None,
        profile: Profile | None = None,
        require_otp: bool = False,
        expected_otp: str = "123456",
        raise_after_first_yield_once: bool = False,
        fail_history_for: set[str] | None = None,
        fail_positions_for: set[str] | None = None,
    ) -> None:
        self.accounts = accounts or []
        self.histories = histories or {}
        self.comings = comings or {}
        self.investments = investments or {}
        self.profile = profile or make_profile()
        self.require_otp = require_otp
        self.expected_otp = expected_otp
        self.raise_after_first_yield_once = raise_after_first_yield_once
        self.fail_history_for = fail_history_for or set()
        self.fail_positions_for = fail_positions_for or set()
        self.generator_calls = 0
        self.history_calls = 0
        self.position_calls = 0
        self.deinit_called = False
        self.config = {
            "code": FakeValue(None),
            "request_information": FakeValue({}),
        }
        self._browser = SimpleNamespace(sms_form={"otp": "pending"}, need_reload_state=True)

    def _authenticate(self) -> None:
        if not self.require_otp:
            return
        code = self.config["code"].get()
        if code is None:
            raise BrowserQuestion(Value("code", label="Entrez le code reçu par SMS"))
        if code != self.expected_otp:
            raise BrowserIncorrectPassword("Code SMS incorrect")

    def iter_accounts(self):
        self._authenticate()
        self.generator_calls += 1
        if self.raise_after_first_yield_once and self.generator_calls == 1 and self.accounts:
            yield self.accounts[0]
            raise BrowserQuestion(Value("code", label="Code SMS"))
        yield from self.accounts

    def iter_history(self, account):
        self._authenticate()
        self.history_calls += 1
        if str(account.id) in self.fail_history_for:
            raise RuntimeError("history boom")
        return list(self.histories.get(str(account.id), []))

    def iter_coming(self, account):
        self._authenticate()
        return list(self.comings.get(str(account.id), []))

    def iter_investment(self, account):
        self._authenticate()
        self.position_calls += 1
        if str(account.id) in self.fail_positions_for:
            raise RuntimeError("positions boom")
        return list(self.investments.get(str(account.id), []))

    def get_profile(self):
        self._authenticate()
        return self.profile

    def deinit(self):
        self.deinit_called = True


@pytest.fixture
def sample_accounts() -> list[Account]:
    return [
        make_account("acc-checking", label="Compte courant"),
        make_account(
            "acc-pea",
            label="PEA Fortuneo",
            account_type=Account.TYPE_PEA,
            balance=Decimal("5000.00"),
            number="987654321",
            history_link=None,
            investment_link="https://mabanque.fortuneo.fr/pea",
        ),
    ]


@pytest.fixture
def sample_transactions() -> list[Transaction]:
    return [
        make_transaction("tx-1", day=date(2026, 1, 15), label="CARTE BOULANGERIE", amount="-12.34"),
        make_transaction("tx-2", day=date(2026, 2, 15), label="VIR SALAIRE", amount="2500.00", transaction_type=Transaction.TYPE_TRANSFER),
        make_transaction("tx-3", day=date(2026, 3, 15), label="CARTE RESTAURANT", amount="-42.00"),
    ]


@pytest.fixture
def sample_investments() -> list[Investment]:
    return [make_investment()]
