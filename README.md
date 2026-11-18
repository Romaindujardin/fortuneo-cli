# Fortuneo CLI

[![CI](https://github.com/Romaindujardin/fortuneo-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/Romaindujardin/fortuneo-cli/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

An unofficial command-line client for Fortuneo accounts. It authenticates
through the same OAuth2/PKCE + SMS strong customer authentication flow as the
website, then exports accounts, transactions and investment positions as stable
JSON. It is built on [Woob](https://woob.tech/).

> **Read-only.** The CLI never places orders, transfers or payments.
>
> **Unofficial.** This project is not affiliated with, endorsed by, or supported
> by Fortuneo or Crédit Mutuel Arkéa. Use it with your own account, at your own
> risk, and in accordance with Fortuneo's terms of service.

## Status

The Fortuneo OAuth2/PKCE + SMS flow was validated against a real account on
**2026-11-17** (v0.1.0):

- OAuth2/PKCE login and SMS SCA completed without bypassing 2FA;
- profile read successfully;
- accounts returned (checking, cash, securities, PEA);
- full export returned `status: "ok"` with transactions and positions.

Version 0.2.0 hardens local storage without changing the Fortuneo flow: the
password is no longer written to disk and Woob session state is opt-in. The
automated suite covers the new credential paths, but a fresh real-account smoke
test is recommended after upgrading.

The validation account is not part of the repository. No credential, OTP or
session data is published here. Account numbers in examples are synthetic.

## Features

- JSON-only output on stdout, diagnostics on stderr;
- accounts, profile, transactions and investment positions;
- full export with per-account error isolation (`ok` / `partial`);
- interactive, file, environment or stdin delivery of the SMS code;
- **password never written to disk**; only the customer ID is stored in a
  private file, and the password is prompted or read from an explicit channel;
- session cookies are **not persisted by default** (`--state` is opt-in);
- redaction of secrets in logs, errors and debug tracebacks;
- `doctor` command for Python, Woob, module, storage, credentials and OTP checks;
- sandboxed `HOME`/XDG directories so dependencies do not write to your home;
- deterministic JSON field names and stable transaction `dedupKey` values.

## Installation

### From source (recommended)

```bash
git clone https://github.com/Romaindujardin/fortuneo-cli.git
cd fortuneo-cli
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
```

The command is then available as `.venv/bin/fortuneo-sidecar` (and the alias
`.venv/bin/fortuneo-cli`).

### With pipx

```bash
pipx install "git+https://github.com/Romaindujardin/fortuneo-cli.git@v0.2.0"
```

### Requirements

- Python 3.11 or newer;
- network access to `mabanque.fortuneo.fr` and `api.fortuneo.fr`;
- a Fortuneo account whose login can be validated by SMS;
- Woob 3.7 (pinned in `pyproject.toml`).

## Quick start

```bash
# 1. Store only your customer ID in a private file. The password is never
#    written to disk; it is asked each time you run a command.
fortuneo-sidecar auth

# 2. Check the installation and configuration.
fortuneo-sidecar doctor

# 3. List accounts (password prompt).
fortuneo-sidecar accounts

# 4. Export balances and investment positions in one login.
fortuneo-sidecar export --no-transactions > fortuneo.json

# 5. Full export, including transactions.
fortuneo-sidecar export > fortuneo-full.json
```

Each command opens its own Fortuneo session. If Fortuneo asks for an SMS code,
the CLI prompts for it interactively; supply it in advance with `--otp-file`,
`FORTUNEO_OTP` or `--otp-stdin` for non-interactive use.

For unattended runs, provide the password without storing it:

```bash
# Environment variable (visible to same-user process inspection).
FORTUNEO_PASSWORD=... fortuneo-sidecar accounts

# Explicit stdin channel (not visible in argv).
printf '%s\n' "$FORTUNEO_PASSWORD" | fortuneo-sidecar --password-stdin accounts
```

A 0.1.x credentials file that still contains a password is rejected. Remove the
legacy secret once with:

```bash
fortuneo-sidecar auth --forget-password
```

## Commands

| Command | Description |
|---|---|
| `auth` | Store or check the customer ID (never the password). |
| `doctor` | Diagnose Python, Woob, module, storage, credentials, OTP and network. |
| `accounts` | List accounts with balances and metadata. |
| `profile` | Read the account holder profile (login smoke test). |
| `transactions` | Read transactions for one account. |
| `positions` | Read investment positions for one account. |
| `export` | Export accounts, transactions and positions in one session. |

### Common options

| Option | Description |
|---|---|
| `--data-dir PATH` | Runtime directory (default: `~/.local/share/fortuneo-sidecar`). |
| `--credentials-file PATH` | Private JSON file containing the customer ID (mode `0600`). |
| `--password-stdin` | Read the password from stdin (one line, never in `argv`). |
| `--otp-file PATH` | Private file containing the SMS code (mode `0600`). |
| `--otp-stdin` | Read the SMS code from stdin. |
| `--no-interactive` | Never prompt; fail cleanly if a secret is missing. |
| `--state` | Persist Woob session state (cookies/PKCE) in `data/state.yml`; opt-in. |
| `--no-state` | Deprecated compatibility flag; state is already off by default. |
| `--raw` | Include raw Woob objects in JSON output. |
| `--debug` | Detailed, redacted logs on stderr. |

### Selecting an account

`--account` accepts the Woob account id, the account number, the IBAN or the
exact label. When several accounts exist and no selector is given,
`transactions`, `positions` and export commands fail with `USAGE_ERROR` instead
of guessing.

```bash
fortuneo-sidecar transactions --account 000000000000 --since 2026-01-01 --limit 50
fortuneo-sidecar positions --account "PEA"
fortuneo-sidecar export --since 2026-01-01 --no-positions
```

### Authentication helpers

```bash
fortuneo-sidecar auth --check
fortuneo-sidecar auth --from-env     # copies FORTUNEO_LOGIN only
fortuneo-sidecar auth --forget-password  # removes a legacy stored password
fortuneo-sidecar auth --delete
```

## JSON output

Every command writes one JSON document to stdout. Unknown or unavailable Woob
values become `null`; dates are ISO 8601 (`YYYY-MM-DD`); amounts are JSON
numbers.

| Command | Schema |
|---|---|
| `accounts` | `fortuneo-sidecar/accounts/v1` |
| `profile` | `fortuneo-sidecar/profile/v1` |
| `transactions` | `fortuneo-sidecar/transactions/v1` |
| `positions` | `fortuneo-sidecar/positions/v1` |
| `export` | `fortuneo-sidecar/export/v1` |
| `doctor` | `fortuneo-sidecar/doctor/v1` |
| `auth` | `fortuneo-sidecar/auth/v1` |
| errors | `fortuneo-sidecar/error/v1` |

`accounts[].balance` is the cash balance of the account. For a securities
account or PEA, the value of the holdings is in `positions[].valuation`, not in
`balance`. The export exposes:

```json
{
  "schema": "fortuneo-sidecar/export/v1",
  "status": "ok",
  "accounts": [
    {
      "id": "...",
      "label": "...",
      "kind": "trading",
      "type": "pea",
      "balance": 0,
      "currency": "EUR",
      "transactions": [],
      "positions": [
        {
          "label": "ETF Monde",
          "code": "FR0000000001",
          "quantity": 10,
          "unitPrice": 100.0,
          "valuation": 1055.0,
          "diff": 55.0,
          "diffRatio": 0.055,
          "currency": "EUR"
        }
      ]
    }
  ],
  "totals": {
    "balances": {"EUR": 0},
    "positionValues": {"EUR": 1055.0}
  },
  "errors": [],
  "warnings": []
}
```

Exports are **partial by design**: if one account fails, its error is reported
in `errors[]`, the other accounts are still exported, and `status` becomes
`"partial"`. Authentication and module-loading failures abort the whole export.

Transactions include a stable `dedupKey` of the form
`fortuneo:<accountId>:<uniqueId>`.

The complete field reference is in [docs/JSON-SCHEMAS.md](docs/JSON-SCHEMAS.md).

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success (`doctor`: all critical checks passed). |
| `1` | Internal error, or `doctor` critical check failed. |
| `2` | Usage error. |
| `3` | Missing/invalid customer ID file, or missing password channel. |
| `4` | Authentication, OTP or account-locked error. |
| `5` | Upstream or network error. |
| `6` | Woob module not installed or not loadable. |
| `7` | Account not found or ambiguous. |

## Authentication and OTP

The **customer ID** is resolved in this order:

1. `FORTUNEO_LOGIN`;
2. the private JSON file created by `fortuneo-sidecar auth`.

That file contains only `{"login": "..."}`. It is a private identifier, not a
secret, and is written atomically with mode `0600`.

The **password** is resolved in this order:

1. `FORTUNEO_PASSWORD`;
2. `--password-stdin` (one line);
3. an interactive prompt on a terminal.

The password is never written to disk, never passed in `argv`, and is kept in
memory only for the duration of the command. `--no-interactive` disables the
prompt so unattended jobs fail cleanly instead of hanging.

The **SMS code** is resolved in this order:

1. `FORTUNEO_OTP`;
2. `--otp-file` (mode `0600`);
3. `--otp-stdin` (only when explicitly enabled);
4. an interactive prompt on a terminal.

`--password-stdin` and `--otp-stdin` cannot be combined because both consume
stdin. Use one explicit channel and an environment variable or file for the
other.

No password or OTP is ever accepted as a command-line argument. A code is
validated as 4 to 8 digits and is rejected if reused within the same process.
The CLI never bypasses or automates the SMS challenge.

Environment variables are convenient for supervised automation but are visible
to some same-user process inspection tools. Prefer the interactive prompt or
`--password-stdin` on a shared or untrusted machine.

### Upgrading from 0.1.x

Version 0.1.x stored the password in `credentials.json`. Version 0.2.0 refuses
to use such a file. Run `fortuneo-sidecar auth --forget-password` once; the file
is rewritten with only the customer ID. You can verify the result with
`fortuneo-sidecar auth --check` or `fortuneo-sidecar doctor`.

## Security

The full model, threat analysis and residual risks are documented in
[SECURITY.md](SECURITY.md) and [docs/SECURITY-AUDIT.md](docs/SECURITY-AUDIT.md).
The main guarantees are:

- runtime directories created by the CLI are `0700`; the customer-ID file, OTP
  file and opt-in Woob state file are `0600`;
- **the password is never written to disk**; only the customer ID is stored;
- **Woob session state is not persisted by default**; `--state` is an explicit
  opt-in and the resulting `data/state.yml` is forced to `0600`;
- customer-ID and OTP files that are symlinks, non-regular files, group/other
  readable, or oversized are refused; state-file symlinks and non-regular state
  paths are refused;
- the customer-ID file is written atomically with `0600`, then `fsync`ed and
  renamed;
- `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_CACHE_HOME` are redirected
  into the private runtime directory while the CLI runs;
- known secrets plus URL-encoded, JSON, form-encoded and `Authorization` forms
  are redacted from logs, errors, error details and debug tracebacks;
- transient 2FA forms and codes are cleared in memory **before** Woob persists
  its state;
- dependencies are pinned (`woob==3.7`) and the vendored module has a recorded
  provenance;
- only HTTPS endpoints are used, with certificate verification enabled.

Residual risks are explicit: the customer-ID file is plaintext (it is private
but not a password), the password and OTP live in memory or in environment
variables while a command runs, a local process running as your user can read
your files or memory, and SMS interception or a compromised Fortuneo account
remain outside the CLI's control.

## Doctor

```bash
fortuneo-sidecar doctor
fortuneo-sidecar doctor --network
```

`doctor` checks Python, Woob, the vendored module, storage permissions, the
customer-ID file, the password channel and the OTP channel. It reports whether
a legacy password is still stored (`passwordStored`) and whether a password is
available (`passwordSource`: `env`, `prompt` or `missing`). `--network`
additionally calls `https://mabanque.fortuneo.fr/env.json`. Warnings about
permissions include the exact `chmod` command to apply. A symlinked or
non-regular state file makes the storage check fail because the CLI will refuse
to use it.

## Development

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The test suite uses fake Woob backends and never contacts Fortuneo. The
`real` and `network` pytest markers are reserved for manual smoke tests.

A manual end-to-end smoke test is available:

```bash
.venv/bin/python scripts/real_smoke.py
.venv/bin/python scripts/real_smoke.py --export
```

It prints counters, account types and errors, not balances or IBANs, unless
`--show-data` is passed. It never accepts a password or OTP in `argv`; the
password and OTP are prompted on the terminal or read through the same private
channels as the CLI. Session state is not persisted unless `--state` is passed.

## Project layout

```text
fortuneo_sidecar/
  cli.py                 JSON CLI, argument parsing, redaction, doctor
  engine.py              Woob session lifecycle, OTP handling, export
  credentials.py         customer-ID storage and redaction helpers
  private_files.py       symlink-safe, permission-checked file reads
  otp.py                 OTP channels and anti-reuse checks
  paths.py               private runtime directories and HOME sandbox
  serialize.py           stable JSON serialization
  vendor/woob_modules/   vendored Fortuneo module (LGPLv3+)
scripts/real_smoke.py    manual end-to-end validation
tests/                   unit and integration tests with fake backends
docs/                    architecture, JSON schemas and security audit
```

## Vendored Woob module

The Fortuneo module is vendored because the SMS SCA flow is not yet in the
released upstream module. Its provenance, compatibility and local patches are
documented in
[`fortuneo_sidecar/vendor/woob_modules/fortuneo/UPSTREAM.md`](fortuneo_sidecar/vendor/woob_modules/fortuneo/UPSTREAM.md).
The vendored code remains under LGPL-3.0-or-later; the original code of this
repository is MIT. See [NOTICE](NOTICE) and `LICENSES/`.

## Contributing

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md)
before submitting changes. Security issues must be reported privately as
described in [SECURITY.md](SECURITY.md).

## License

Original code: MIT, copyright 2026 Romain Dujardin.

Vendored Woob Fortuneo module: LGPL-3.0-or-later, copyright Gilles-Alexandre
Quenot and Woob contributors. Full texts are in `LICENSES/`.
