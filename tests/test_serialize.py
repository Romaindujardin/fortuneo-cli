from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from woob.capabilities.bank import Account, Transaction
from woob.capabilities.bank.wealth import Investment
from woob.capabilities.base import NotAvailable

from fortuneo_sidecar.serialize import (
    clean_value,
    dump_json,
    filter_transactions,
    serialize_account,
    serialize_investment,
    serialize_profile,
    serialize_transaction,
    serialize_transactions,
)

from .conftest import make_account, make_investment, make_profile, make_transaction


def test_clean_value_converts_woob_types():
    assert clean_value(Decimal("12.50")) == 12.5
    assert clean_value(date(2026, 1, 15)) == "2026-01-15"
    assert clean_value(NotAvailable) is None
    assert clean_value([Decimal("1.5"), NotAvailable]) == [1.5, None]


def test_serialize_account_maps_type_and_amounts():
    account = make_account(
        "acc-pea",
        label="PEA Fortuneo",
        account_type=Account.TYPE_PEA,
        balance=Decimal("5000.00"),
        number="987654321",
        iban="FR7612345678901234567890123",
        investment_link="https://example.invalid/pea",
    )
    payload = serialize_account(account)
    assert payload["id"] == "acc-pea"
    assert payload["label"] == "PEA Fortuneo"
    assert payload["kind"] == "trading"
    assert payload["type"] == "pea"
    assert payload["typeLabel"] == "PEA"
    assert payload["balance"] == 5000.0
    assert payload["currency"] == "EUR"
    assert payload["iban"] == "FR7612345678901234567890123"
    assert payload["number"] == "987654321"
    json.dumps(payload)


def test_serialize_account_missing_values_become_null():
    account = Account(id="acc-1")
    payload = serialize_account(account)
    assert payload["label"] is None
    assert payload["balance"] is None
    assert payload["iban"] is None
    assert payload["allBalances"] == []


def test_serialize_transaction_dedup_key_is_stable_and_unique():
    first = make_transaction("tx-1", label="CARTE", amount="-10.00")
    second = make_transaction("tx-2", label="CARTE", amount="-10.00")
    payloads = serialize_transactions([first, second], account_id="acc-1")
    assert payloads[0]["dedupKey"].startswith("fortuneo:acc-1:")
    assert payloads[0]["dedupKey"] != payloads[1]["dedupKey"]
    assert payloads[0]["amount"] == -10.0
    assert payloads[0]["type"] == "card"
    assert payloads[0]["date"] == "2026-01-15"
    json.dumps(payloads)


def test_serialize_transaction_handles_missing_amount():
    transaction = Transaction(id="tx-1")
    transaction.label = "Inconnu"
    payload = serialize_transaction(transaction, account_id="acc-1")
    assert payload["amount"] is None
    assert payload["date"] is None
    assert payload["label"] == "Inconnu"


def test_serialize_investment():
    payload = serialize_investment(make_investment())
    assert payload["label"] == "ETF Monde"
    assert payload["code"] == "FR0000000001"
    assert payload["codeType"] == "ISIN"
    assert payload["quantity"] == 10.0
    assert payload["unitPrice"] == 100.0
    assert payload["unitValue"] == 105.5
    assert payload["valuation"] == 1055.0
    assert payload["currency"] == "EUR"
    assert payload["assetCategory"] == "Actions internationales"
    json.dumps(payload)


def test_serialize_investment_without_currency():
    investment = Investment(id="inv-empty")
    investment.label = "Fonds sans devise"
    payload = serialize_investment(investment)
    assert payload["currency"] is None
    assert payload["valuation"] is None


def test_serialize_profile_masks_nothing_but_keeps_expected_keys():
    payload = serialize_profile(make_profile())
    assert payload["name"] == "Jean Dupont"
    assert payload["email"] == "jean.dupont@example.com"
    assert payload["mainBank"] == "Fortuneo"


def test_filter_transactions_since_until_and_limit(sample_transactions):
    selected = filter_transactions(sample_transactions, since=date(2026, 2, 1), limit=1)
    assert [item.id for item in selected] == ["tx-3"]

    selected = filter_transactions(sample_transactions, until=date(2026, 2, 15))
    assert [item.id for item in selected] == ["tx-2", "tx-1"]


def test_dump_json_is_utf8_and_newline_terminated():
    text = dump_json({"label": "Épargne", "amount": Decimal("1.5")})
    assert text.endswith("\n")
    assert '"amount": 1.5' in text
    assert "Épargne" in text
