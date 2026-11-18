# Contributing

Thanks for considering a contribution. This project handles financial
credentials and session data; security and predictability come first.

## Before you start

- Do not open a public issue for a security vulnerability. Follow
  [SECURITY.md](SECURITY.md).
- Do not paste real credentials, OTP codes, cookies, IBANs, account numbers,
  balances or transaction labels into issues or pull requests.
- Use synthetic data in tests.

## Development setup

```bash
git clone https://github.com/Romaindujardin/fortuneo-cli.git
cd fortuneo-cli
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The automated suite must never contact Fortuneo. Use fake backends and mocked
responses.

## Coding guidelines

- Keep the JSON schemas stable. Add fields rather than renaming existing ones;
  document any intentional schema change.
- **Never persist passwords.** The credentials file must contain only
  `{"login": ...}`. Read passwords from `FORTUNEO_PASSWORD`, `--password-stdin`
  or a terminal prompt, and never write them to disk, logs or diagnostics.
- Do not add a password or OTP command-line option that accepts a value; only
  boolean switches such as `--password-stdin` and `--otp-stdin` are allowed.
- Keep secrets out of `argv`, logs, exception messages, tracebacks and test
  fixtures.
- Route new user-visible errors through the typed exceptions in `errors.py`.
- Keep file access on `private_files.read_private_text()` or an equally strict
  helper.
- When adding a new sensitive field, register it with the redaction layer and
  add a test.
- Do not enable Woob state persistence by default; `--state` remains explicit.
- Prefer standard-library solutions over new dependencies. A new dependency
  needs a clear reason and a pinned version.
- Follow the existing formatting and naming style; the codebase targets Python
  3.11+.

## Tests

```bash
.venv/bin/pytest
.venv/bin/pytest -m "not real and not network"
```

Add regression tests for every security fix. Useful patterns:

- create a `FakeBackend` subclass that raises the upstream exception;
- use `tmp_path` for private-file and permissions tests;
- use `monkeypatch` for environment variables;
- spy on `deinit()` when testing state cleanup.

## Vendored Woob module

Changes to `fortuneo_sidecar/vendor/woob_modules/fortuneo/` must:

1. keep the upstream copyright and LGPL headers;
2. update `UPSTREAM.md` with the source version and a description of the
   local diff;
3. include the upstream issue/MR references when applicable;
4. pass the existing SCA tests and the offline module-loading test;
5. be reviewed separately from unrelated changes.

## Real-account validation

A real smoke test is manual and optional:

```bash
.venv/bin/python scripts/real_smoke.py --export
```

The script prompts for the password; it never accepts one in `argv`. It also
does not persist Woob state unless `--state` is passed.

Never commit its output unless you have removed all account identifiers,
balances, IBANs and transaction data. The script masks balances and IBANs by
default, but `--show-data` is intentionally more verbose. Before pushing,
check the diff for real customer IDs, emails, IBANs, local paths and runtime
files (`config/credentials.json`, `data/state.yml`, OTP files, exports).

## Pull requests

- Keep the change focused.
- Describe the behavior change and the security impact.
- Mention the tests you ran.
- Update `README.md`, `SECURITY.md`, `docs/SECURITY-AUDIT.md` or `CHANGELOG.md`
  when relevant.
- Do not bump the version unless a maintainer asks; releases are tagged
  separately.

## License

By contributing, you agree that your original contribution is licensed under
the MIT License, and that changes to the vendored Woob module remain under
LGPL-3.0-or-later.
