"""Sérialisation JSON stable des objets métier Woob."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Sequence

from woob.capabilities.bank import Account, Transaction
from woob.capabilities.bank.wealth import Investment
from woob.capabilities.base import BaseObject, NotAvailable, NotLoaded

ACCOUNT_TYPES: dict[int, tuple[str, str]] = {
    Account.TYPE_UNKNOWN: ("unknown", "Inconnu"),
    Account.TYPE_CHECKING: ("checking", "Compte courant"),
    Account.TYPE_SAVINGS: ("savings", "Épargne"),
    Account.TYPE_DEPOSIT: ("deposit", "Dépôt"),
    Account.TYPE_LOAN: ("loan", "Prêt"),
    Account.TYPE_MARKET: ("securities", "Compte-titres"),
    Account.TYPE_JOINT: ("joint", "Compte joint"),
    Account.TYPE_CARD: ("card", "Carte"),
    Account.TYPE_LIFE_INSURANCE: ("life_insurance", "Assurance-vie"),
    Account.TYPE_PEE: ("pee", "PEE"),
    Account.TYPE_PERCO: ("perco", "PERCO"),
    Account.TYPE_ARTICLE_83: ("article_83", "Article 83"),
    Account.TYPE_RSP: ("rsp", "RSP"),
    Account.TYPE_PEA: ("pea", "PEA"),
    Account.TYPE_CAPITALISATION: ("capitalisation", "Capitalisation"),
    Account.TYPE_PERP: ("perp", "PERP"),
    Account.TYPE_MADELIN: ("madelin", "Madelin"),
    Account.TYPE_MORTGAGE: ("mortgage", "Crédit immobilier"),
    Account.TYPE_CONSUMER_CREDIT: ("consumer_credit", "Crédit consommation"),
    Account.TYPE_REVOLVING_CREDIT: ("revolving_credit", "Crédit renouvelable"),
    Account.TYPE_PER: ("per", "PER"),
    Account.TYPE_REAL_ESTATE: ("real_estate", "Immobilier"),
    Account.TYPE_CROWDLENDING: ("crowdlending", "Crowdlending"),
    Account.TYPE_LDDS: ("ldds", "LDDS"),
    Account.TYPE_PEL: ("pel", "PEL"),
    Account.TYPE_CSL: ("csl", "Compte sur livret"),
    Account.TYPE_CEL: ("cel", "CEL"),
    Account.TYPE_CAT: ("cat", "Compte à terme"),
    Account.TYPE_LIVRET_A: ("livret_a", "Livret A"),
    Account.TYPE_LIVRET_B: ("livret_b", "Livret B"),
}

ACCOUNT_KINDS: dict[int, str] = {
    Account.TYPE_UNKNOWN: "unknown",
    Account.TYPE_CHECKING: "banking",
    Account.TYPE_JOINT: "banking",
    Account.TYPE_CARD: "banking",
    Account.TYPE_SAVINGS: "savings",
    Account.TYPE_LDDS: "savings",
    Account.TYPE_PEL: "savings",
    Account.TYPE_CSL: "savings",
    Account.TYPE_CEL: "savings",
    Account.TYPE_CAT: "savings",
    Account.TYPE_LIVRET_A: "savings",
    Account.TYPE_LIVRET_B: "savings",
    Account.TYPE_DEPOSIT: "deposit",
    Account.TYPE_MARKET: "trading",
    Account.TYPE_PEA: "trading",
    Account.TYPE_PEE: "trading",
    Account.TYPE_PERCO: "trading",
    Account.TYPE_ARTICLE_83: "trading",
    Account.TYPE_RSP: "trading",
    Account.TYPE_CAPITALISATION: "trading",
    Account.TYPE_PERP: "trading",
    Account.TYPE_PER: "trading",
    Account.TYPE_MADELIN: "trading",
    Account.TYPE_LIFE_INSURANCE: "insurance",
    Account.TYPE_LOAN: "loan",
    Account.TYPE_MORTGAGE: "loan",
    Account.TYPE_CONSUMER_CREDIT: "loan",
    Account.TYPE_REVOLVING_CREDIT: "loan",
    Account.TYPE_REAL_ESTATE: "real_estate",
    Account.TYPE_CROWDLENDING: "crowdlending",
}

TRANSACTION_TYPES: dict[int, tuple[str, str]] = {
    Transaction.TYPE_UNKNOWN: ("unknown", "Inconnu"),
    Transaction.TYPE_TRANSFER: ("transfer", "Virement"),
    Transaction.TYPE_ORDER: ("order", "Ordre"),
    Transaction.TYPE_CHECK: ("check", "Chèque"),
    Transaction.TYPE_DEPOSIT: ("deposit", "Dépôt"),
    Transaction.TYPE_PAYBACK: ("payback", "Remboursement"),
    Transaction.TYPE_WITHDRAWAL: ("withdrawal", "Retrait"),
    Transaction.TYPE_CARD: ("card", "Carte"),
    Transaction.TYPE_LOAN_PAYMENT: ("loan_payment", "Échéance de prêt"),
    Transaction.TYPE_BANK: ("bank", "Opération bancaire"),
    Transaction.TYPE_CASH_DEPOSIT: ("cash_deposit", "Dépôt espèces"),
    Transaction.TYPE_CARD_SUMMARY: ("card_summary", "Récapitulatif carte"),
    Transaction.TYPE_DEFERRED_CARD: ("deferred_card", "Carte à débit différé"),
    Transaction.TYPE_INSTANT: ("instant", "Virement instantané"),
    Transaction.TYPE_MARKET_ORDER: ("market_order", "Ordre de bourse"),
    Transaction.TYPE_MARKET_FEE: ("market_fee", "Frais de bourse"),
    Transaction.TYPE_ARBITRAGE: ("arbitrage", "Arbitrage"),
    Transaction.TYPE_PROFIT: ("profit", "Plus-value"),
}

TRANSACTION_KINDS = {"banking", "savings", "trading", "insurance", "loan", "deposit"}
POSITION_KINDS = {"trading", "insurance"}


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if value is NotAvailable or value is NotLoaded:
        return True
    return value.__class__.__name__ in {"NotAvailableType", "NotLoadedType"}


def clean_value(value: Any) -> Any:
    """Convertit un objet Woob en valeur JSON simple."""
    if _is_missing(value):
        return None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return clean_value(value.value)
    if isinstance(value, BaseObject):
        return clean_value(value.to_dict())
    if isinstance(value, dict):
        return {str(key): clean_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean_value(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, str):
        return value.strip()
    return value


def _text(value: Any) -> str | None:
    cleaned = clean_value(value)
    return cleaned if isinstance(cleaned, str) and cleaned else None


def _number(value: Any) -> float | None:
    cleaned = clean_value(value)
    if isinstance(cleaned, bool):
        return None
    if isinstance(cleaned, (int, float)):
        return float(cleaned)
    if isinstance(cleaned, str):
        try:
            return float(cleaned.replace(",", "."))
        except ValueError:
            return None
    return None


def _date(value: Any) -> str | None:
    cleaned = clean_value(value)
    if isinstance(cleaned, str) and cleaned:
        return cleaned[:10]
    return None


def account_type_info(account: Account) -> tuple[str, str]:
    account_type = account.type if isinstance(account.type, int) else Account.TYPE_UNKNOWN
    return ACCOUNT_TYPES.get(account_type, ("unknown", "Inconnu"))


def account_kind(account: Account) -> str:
    account_type = account.type if isinstance(account.type, int) else Account.TYPE_UNKNOWN
    return ACCOUNT_KINDS.get(account_type, "unknown")


def serialize_account(account: Account, *, include_raw: bool = False) -> dict:
    type_key, type_label = account_type_info(account)
    payload = {
        "id": _text(account.id),
        "label": _text(account.label),
        "kind": account_kind(account),
        "type": type_key,
        "typeLabel": type_label,
        "balance": _number(account.balance),
        "currency": _text(account.currency),
        "coming": _number(account.coming),
        "iban": _text(account.iban),
        "number": _text(account.number),
        "bankName": _text(account.bank_name),
        "ownerType": _text(account.owner_type),
        "ownership": _text(account.ownership),
        "valuationDiff": _number(account.valuation_diff),
        "valuationDiffRatio": _number(account.valuation_diff_ratio),
        "openingDate": _date(account.opening_date),
        "paydate": _date(account.paydate),
        "paymin": _number(account.paymin),
        "cardlimit": _number(account.cardlimit),
        "managementType": _text(account.management_type),
        "companyName": _text(account.company_name),
        "allBalances": clean_value(account.all_balances) or [],
    }
    if include_raw:
        payload["raw"] = clean_value(account.to_dict())
    return payload


def serialize_transaction(
    transaction: Transaction,
    *,
    account_id: str,
    seen: set[str] | None = None,
    include_raw: bool = False,
) -> dict:
    type_code = transaction.type if isinstance(transaction.type, int) else Transaction.TYPE_UNKNOWN
    type_key, type_label = TRANSACTION_TYPES.get(type_code, ("unknown", "Inconnu"))
    unique = transaction.unique_id(seen=seen, account_id=account_id)
    external_id = _text(transaction.id)
    payload = {
        "id": external_id,
        "dedupKey": f"fortuneo:{account_id}:{unique}",
        "date": _date(transaction.date),
        "rdate": _date(transaction.rdate),
        "vdate": _date(transaction.vdate),
        "bdate": _date(transaction.bdate),
        "label": _text(transaction.label),
        "raw": _text(transaction.raw) if include_raw else None,
        "amount": _number(transaction.amount),
        "currency": _text(getattr(transaction, "currency", None)),
        "type": type_key,
        "typeLabel": type_label,
        "category": _text(transaction.category),
        "coming": clean_value(transaction.coming),
        "card": _text(transaction.card),
        "counterparty": _text(transaction.counterparty),
        "commission": _number(transaction.commission),
        "originalAmount": _number(transaction.original_amount),
        "originalCurrency": _text(transaction.original_currency),
        "investments": clean_value(transaction.investments) or [],
        "attachments": clean_value(transaction.attachments) or [],
    }
    if include_raw:
        payload["rawObject"] = clean_value(transaction.to_dict())
    return payload


def serialize_transactions(
    transactions: Iterable[Transaction],
    *,
    account_id: str,
    include_raw: bool = False,
) -> list[dict]:
    seen: set[str] = set()
    return [
        serialize_transaction(item, account_id=account_id, seen=seen, include_raw=include_raw)
        for item in transactions
    ]


def serialize_investment(investment: Investment, *, include_raw: bool = False) -> dict:
    code_type = _text(investment.code_type)
    code_type = code_type.upper() if code_type else None
    payload = {
        "label": _text(investment.label),
        "code": _text(investment.code),
        "codeType": code_type,
        "stockSymbol": _text(investment.stock_symbol),
        "stockMarket": _text(investment.stock_market),
        "description": _text(investment.description),
        "quantity": _number(investment.quantity),
        "unitPrice": _number(investment.unitprice),
        "unitValue": _number(investment.unitvalue),
        "valuation": _number(investment.valuation),
        "currency": _text(investment.original_currency) or _text(getattr(investment, "currency", None)),
        "vdate": _date(investment.vdate),
        "diff": _number(investment.diff),
        "diffRatio": _number(investment.diff_ratio),
        "portfolioShare": _number(investment.portfolio_share),
        "performanceHistory": clean_value(investment.performance_history) or [],
        "srri": clean_value(investment.srri),
        "assetCategory": _text(investment.asset_category),
        "recommendedPeriod": clean_value(investment.recommended_period),
        "originalCurrency": _text(investment.original_currency),
        "originalValuation": _number(investment.original_valuation),
        "originalUnitValue": _number(investment.original_unitvalue),
        "originalUnitPrice": _number(investment.original_unitprice),
        "originalDiff": _number(investment.original_diff),
    }
    if include_raw:
        payload["raw"] = clean_value(investment.to_dict())
    return payload


def serialize_profile(profile: Any) -> dict:
    fields = (
        "name",
        "postal_address",
        "phone",
        "professional_phone",
        "email",
        "professional_email",
        "main_bank",
    )
    payload = {}
    for field in fields:
        value = getattr(profile, field, None)
        key = "".join(part.capitalize() if idx else part for idx, part in enumerate(field.split("_")))
        payload[key] = clean_value(value)
    return payload


def transaction_date(transaction: Transaction) -> date | None:
    for candidate in (transaction.date, transaction.rdate, transaction.vdate):
        if _is_missing(candidate):
            continue
        if isinstance(candidate, datetime):
            return candidate.date()
        if isinstance(candidate, date):
            return candidate
        try:
            return date.fromisoformat(str(candidate)[:10])
        except (ValueError, TypeError):
            continue
    return None


def filter_transactions(
    transactions: Sequence[Transaction],
    *,
    since: date | None = None,
    until: date | None = None,
    limit: int | None = None,
) -> list[Transaction]:
    selected: list[Transaction] = []
    for transaction in transactions:
        tx_date = transaction_date(transaction)
        if since is not None and (tx_date is None or tx_date < since):
            continue
        if until is not None and (tx_date is None or tx_date > until):
            continue
        selected.append(transaction)

    selected.sort(key=lambda item: (transaction_date(item) or date.min, str(item.label or "")), reverse=True)
    if limit is not None:
        selected = selected[: max(0, limit)]
    return selected


def dump_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=clean_value) + "\n"
