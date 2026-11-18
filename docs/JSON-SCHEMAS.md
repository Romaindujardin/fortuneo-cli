# JSON schemas

Every command writes one JSON document to stdout and exits with a status code
described in the README. This document lists the stable fields of version `v1`
schemas. Unknown values are `null`; dates are `YYYY-MM-DD`; amounts are JSON
numbers.

## `accounts` — `fortuneo-sidecar/accounts/v1`

```json
{
  "schema": "fortuneo-sidecar/accounts/v1",
  "generatedAt": "2026-11-18T12:00:00+00:00",
  "accounts": [
    {
      "id": "000000000000",
      "label": "Compte courant",
      "kind": "banking",
      "type": "checking",
      "typeLabel": "Compte courant",
      "balance": 1234.56,
      "currency": "EUR",
      "coming": null,
      "iban": "FR76...",
      "number": "000000000000",
      "bankName": "Fortuneo",
      "ownerType": null,
      "ownership": null,
      "valuationDiff": null,
      "valuationDiffRatio": null,
      "openingDate": "2020-01-01",
      "paydate": null,
      "paymin": null,
      "cardlimit": null,
      "managementType": null,
      "companyName": null,
      "allBalances": []
    }
  ]
}
```

`kind` is a coarse category: `banking`, `savings`, `trading`, `insurance`,
`loan`, `deposit`, `real_estate`, `crowdlending` or `unknown`.

With `--raw`, each account also has `raw`, the serialized Woob object.

## `profile` — `fortuneo-sidecar/profile/v1`

```json
{
  "schema": "fortuneo-sidecar/profile/v1",
  "generatedAt": "2026-11-18T12:00:00+00:00",
  "profile": {
    "name": "...",
    "postalAddress": "...",
    "phone": "...",
    "professionalPhone": null,
    "email": "...",
    "professionalEmail": null,
    "mainBank": "Fortuneo"
  }
}
```

This command is also the cheapest login smoke test.

## `transactions` — `fortuneo-sidecar/transactions/v1`

```json
{
  "schema": "fortuneo-sidecar/transactions/v1",
  "generatedAt": "2026-11-18T12:00:00+00:00",
  "account": { "...": "account object" },
  "transactions": [
    {
      "id": "tx-1",
      "dedupKey": "fortuneo:000000000000:abc123",
      "date": "2026-11-17",
      "rdate": "2026-11-17",
      "vdate": "2026-11-17",
      "bdate": null,
      "label": "CARTE ...",
      "raw": null,
      "amount": -12.34,
      "currency": "EUR",
      "type": "card",
      "typeLabel": "Carte",
      "category": "Alimentation",
      "coming": false,
      "card": null,
      "counterparty": null,
      "commission": null,
      "originalAmount": null,
      "originalCurrency": null,
      "investments": [],
      "attachments": []
    }
  ],
  "warnings": []
}
```

`dedupKey` is stable for a transaction within an account. Transactions are
sorted by descending date, then label. `--since`, `--until`, `--limit` and
`--coming` filter or extend the list.

With `--raw`, `raw` contains the textual raw label and `rawObject` contains the
serialized Woob object.

## `positions` — `fortuneo-sidecar/positions/v1`

```json
{
  "schema": "fortuneo-sidecar/positions/v1",
  "generatedAt": "2026-11-18T12:00:00+00:00",
  "account": { "...": "account object" },
  "positions": [
    {
      "label": "ETF Monde",
      "code": "FR0000000001",
      "codeType": "ISIN",
      "stockSymbol": "FR0000000001",
      "stockMarket": null,
      "description": null,
      "quantity": 10,
      "unitPrice": 100.0,
      "unitValue": 105.5,
      "valuation": 1055.0,
      "currency": "EUR",
      "vdate": "2026-11-17",
      "diff": 55.0,
      "diffRatio": 0.055,
      "portfolioShare": 0.42,
      "performanceHistory": [],
      "srri": 4,
      "assetCategory": "Actions internationales",
      "recommendedPeriod": null,
      "originalCurrency": "EUR",
      "originalValuation": 1055.0,
      "originalUnitValue": 105.5,
      "originalUnitPrice": 100.0,
      "originalDiff": 55.0
    }
  ],
  "totals": {
    "EUR": 1055.0
  }
}
```

`totals` here is keyed directly by currency. In an export, the equivalent is
`totals.positionValues`.

## `export` — `fortuneo-sidecar/export/v1`

```json
{
  "schema": "fortuneo-sidecar/export/v1",
  "generatedAt": "2026-11-18T12:00:00+00:00",
  "status": "ok",
  "accounts": [
    {
      "...": "account object",
      "transactions": [],
      "positions": []
    }
  ],
  "totals": {
    "balances": {"EUR": 1234.56},
    "positionValues": {"EUR": 1055.0}
  },
  "errors": [],
  "warnings": []
}
```

`status` is `ok` when `errors` is empty, otherwise `partial`. Errors have this
shape:

```json
{
  "accountId": "000000000000",
  "step": "positions",
  "code": "ACCOUNT_ERROR",
  "message": "..."
}
```

Authentication and module-loading failures abort the export instead of being
recorded as per-account errors. Messages in `errors` and `warnings` are passed
through the redaction layer.

## `doctor` — `fortuneo-sidecar/doctor/v1`

```json
{
  "schema": "fortuneo-sidecar/doctor/v1",
  "generatedAt": "2026-11-18T12:00:00+00:00",
  "ok": true,
  "ready": true,
  "checks": [
    {
      "name": "storage",
      "ok": true,
      "critical": true,
      "detail": {
        "dataDir": "...",
        "writable": true,
        "stateMode": null,
        "permissionWarnings": []
      }
    },
    {
      "name": "credentials",
      "ok": true,
      "critical": false,
      "detail": {
        "file": "...",
        "exists": true,
        "loginMasked": "7***",
        "loginLength": 8,
        "loginNumeric": true,
        "passwordStored": false,
        "source": "file",
        "passwordSource": "prompt",
        "symlink": false,
        "mode": "600"
      }
    }
  ],
  "warnings": []
}
```

Critical checks are `python`, `woob`, `module` and `storage`. `credentials` and
`otp` affect `ready` but are not critical for the installation itself.
`--network` adds a non-critical `network` check.

For `credentials`, `passwordStored` is always `false` on a healthy 0.2.0
install: it becomes `true` only for a legacy 0.1.x file that still contains a
password, in which case the check fails and `warnings` points to
`auth --forget-password`. `passwordSource` is `env`, `prompt` or `missing`.
`stateMode` is `null` when no opt-in state file exists.

## `auth` — `fortuneo-sidecar/auth/v1`

```json
{
  "schema": "fortuneo-sidecar/auth/v1",
  "ok": true,
  "credentialsFile": "...",
  "loginMasked": "7***",
  "passwordStored": false,
  "mode": "600"
}
```

The credentials file contains only `{"login": "..."}`. `--from-env` copies
`FORTUNEO_LOGIN` and adds a warning when `FORTUNEO_PASSWORD` is set, because the
password is never written.

`--check` returns a `credentials` summary (`exists`, `mode`, `symlink`,
`source`, `loginMasked`, `loginLength`, `loginNumeric`, `passwordStored`).
`--delete` returns `deleted`. `--forget-password` returns `removedPassword`
(boolean), `passwordStored: false` and the new `mode`; it rewrites a legacy file
with only its `login`. A legacy file that still contains a password makes
`--check` exit with code 3 and `error.code == "PASSWORD_STORED"`. Errors use the
standard error schema.

## Errors — `fortuneo-sidecar/error/v1`

```json
{
  "schema": "fortuneo-sidecar/error/v1",
  "ok": false,
  "error": {
    "code": "AUTH_FAILED",
    "message": "...",
    "details": {}
  }
}
```

With `--debug`, `error.traceback` is added; it is redacted like every other
message. Error and warning text never contains the known credentials in raw,
URL-encoded, JSON, form-encoded or `Authorization` form.
