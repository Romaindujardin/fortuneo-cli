# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-18

### Changed

- **Breaking:** the credentials file now contains only the customer ID
  (`{"login": ...}`). The password is never written to disk.
- The password is resolved at runtime from `FORTUNEO_PASSWORD`,
  `--password-stdin` or an interactive terminal prompt.
- Woob session state is **not persisted by default**; pass `--state` to opt in.
- `auth` only stores the login and no longer asks for a password.
- `doctor` reports `passwordStored` and `passwordSource`, and treats a stored
  password as not ready.
- `--no-state` is deprecated and kept as a compatibility no-op.
- README, security policy, audit report, architecture and JSON schemas updated
  for login-only storage.

### Added

- `--password-stdin` reads the password from one stdin line, never from `argv`;
  it is mutually exclusive with `--otp-stdin`.
- `auth --forget-password` removes a password left by a 0.1.x file.
- `PasswordRequired` error (`PASSWORD_REQUIRED`, exit code 3) when no password
  channel is available.
- `auth --check` and `doctor` flag a legacy file that still contains a password.
- Example account numbers replaced with the synthetic `000000000000`.

### Security

- A 0.1.x credentials file containing `password` is refused until migrated.
- Passwords are capped at 4096 characters on stdin and never echoed.
- `--state` and `--no-state` are rejected when combined, as are
  `--password-stdin` and `--otp-stdin`.
- The published Git history uses a GitHub `noreply` author address; no real
  login, password, email, IBAN or account number is present.

### Validated

- Automated suite: 117 tests. The OAuth2/PKCE + SCA flow is unchanged since the
  real-account smoke test of 2026-11-17; a fresh 0.2.0 smoke test is recommended.

## [0.1.0] - 2026-11-18 — withdrawn

> The original `v0.1.0` tag was withdrawn: it stored the password on disk and
> its Git metadata contained a personal email address. It is not supported and
> not republished. Use 0.2.0 or later.

### Added

- Standalone JSON CLI for Fortuneo accounts, profile, transactions, positions
  and full export.
- OAuth2/PKCE authentication with SMS SCA through the vendored Woob Fortuneo
  module.
- Credential storage via `fortuneo-sidecar auth`, with atomic `0600` writes.
- OTP channels: interactive prompt, private file, environment variable and
  explicit stdin.
- `doctor` command covering Python, Woob, module, storage, credentials, OTP and
  optional network checks.
- Stable versioned JSON schemas with per-account export errors.
- `--no-state` mode to avoid persisting Woob session cookies.
- Security documentation, audit report, contributing guide and CI workflow.

### Security

- Credentials, OTP and state files are opened with `O_NOFOLLOW` and checked as
  regular, non-group/other-readable files.
- Symlinked credentials, OTP and state files are rejected.
- Private file reads are limited to 64 KiB.
- Transient 2FA forms and codes are cleared before Woob serializes state.
- Structured error details, export errors, warnings and tracebacks are
  redacted.
- `doctor` reports insecure pre-existing directory and state-file permissions.
- Only HTTPS endpoints are used; certificate verification remains enabled.

### Fixed

- Export totals now use the documented `positionValues` key instead of
  `positions`.
- OTP codes are no longer at risk of being persisted in `state.yml` after a
  rejected SCA attempt.

### Validated

- Real-account smoke test on 2026-11-17: OAuth2/PKCE + SMS SCA, accounts,
  transactions and positions exported with status `ok`.

[0.2.0]: https://github.com/Romaindujardin/fortuneo-cli/releases/tag/v0.2.0
