# Security audit

- **Project:** Fortuneo CLI (`fortuneo-sidecar` distribution)
- **Version audited:** `0.2.0` (initial audit at `0.1.0`)
- **Date:** 2026-11-18
- **Method:** manual source review, threat modelling, targeted security tests,
  filesystem inspection of a real runtime directory, remote-repository blob
  scanning, and a real-account smoke test performed on 2026-11-17.

This audit covers the original code of this repository, the vendored Fortuneo
Woob module, the command-line interface, credential/OTP/session storage,
logging, error handling, dependencies and network usage. It is not a formal
third-party penetration test.

## Executive summary

The sidecar is a read-only JSON CLI built around Woob. It does not accept
passwords or OTP codes in `argv`, does not bypass 2FA, and keeps runtime files
private. The audit found no critical or high-severity vulnerability in the
original code after the fixes below.

Version 0.2.0 removes the two main residual risks of 0.1.0: **the password is no
longer written to disk** (only the customer ID is stored) and **Woob session
state is no longer persisted by default** (`--state` is opt-in). The remaining
accepted risk is that a process running as the same user can read the
customer-ID file, inspect process memory while a command runs, or modify the
CLI.

Five issues were fixed during the initial audit:

1. credentials/OTP files could be read through symlinks;
2. a pending SMS form containing the OTP could be persisted by Woob before the
   engine cleaned it;
3. structured error `details` were not redacted;
4. export errors/warnings could contain unredacted upstream text;
5. the `export` totals used `positions` instead of the documented
   `positionValues` key.

Additional hardening was added in 0.1.0: `O_NOFOLLOW` file opening, file-size
limits, state-file symlink/type checks, permission warnings in `doctor`, and a
larger redaction surface. Version 0.2.0 adds login-only storage, refusal and
migration of legacy password files, `--password-stdin`, and opt-in session
state.

The published repository was also scanned: every blob reachable from the public
history was checked for the real login, the real password, email addresses,
IBANs and home paths. No real credential or personal datum was found. Two
example account numbers that looked like real identifiers were replaced with
`000000000000`, and the history was rewritten with a GitHub `noreply` author
address before republication.

## Assets and threat model

| Asset | Where it lives | Main threats |
|---|---|---|
| Customer ID (login) | private JSON file or environment | local file read, logs, argv, process env |
| Password | environment, stdin or memory only | shell history, logs, process memory, env inspection |
| SMS/SCA code | prompt, file, env or stdin | argv, shell history, logs, state file |
| Session cookies / OAuth tokens | memory; opt-in Woob `state.yml` | local file read, symlink redirection, backups |
| Account data | stdout / redirected files | shell history, world-readable exports |
| Vendor code | `fortuneo_sidecar/vendor` | supply-chain tampering, stale module |

Trust assumptions:

- the local user account and its shell are trusted;
- other local users and unprivileged processes are not;
- the network is untrusted and TLS is enforced;
- Fortuneo API responses are untrusted input;
- the vendored Woob module is reviewed at its pinned version.

Out of scope: malware running as the same user, keyloggers, disk forensics
without disk encryption, SIM swapping, SMS interception, and compromise of
Fortuneo's servers.

## Findings

| ID | Severity | Finding | Status |
|---|---|---|---|
| F01 | Medium | Credential and OTP files followed symlinks | Fixed |
| F02 | Medium | A pending `sms_form` containing `otp` could be serialized by Woob before cleanup | Fixed |
| F03 | Medium | Error `details` were emitted without redaction | Fixed |
| F04 | Low | Export errors/warnings could include raw upstream exception text | Fixed |
| F05 | Low | Export totals key `positions` differed from the documented `positionValues` | Fixed |
| F06 | Low | Pre-existing directories with `0755` were silently accepted | Mitigated: `doctor` warnings |
| F07 | Low | Session cookies were persisted by Woob (`state.yml`) | Fixed in 0.2.0: state is opt-in, off by default |
| F08 | Low | Password was stored in plaintext JSON | Fixed in 0.2.0: password never stored |
| F09 | Low | Environment-provided credentials are visible to some same-user tools | Accepted: documented; prompt/stdin preferred |
| F10 | Info | No dependency lockfile / no automated CVE scan in CI | Noted: version pin + CI recommendation |

## Detailed review

### F01 — Symlink-safe private files

**Before:** credentials and OTP files were read with `Path.read_text()`, after
`exists()` / `is_file()` / permission checks. A symlink could point to another
file, and the check/read sequence was vulnerable to a replacement race.

**Fix:** `fortuneo_sidecar/private_files.py` opens files with
`O_RDONLY | O_NOFOLLOW`, then calls `fstat` on the open descriptor to verify:

- the target is a regular file;
- the mode has no group/other bits (`mode & 0o077 == 0`);
- the content is at most 64 KiB and valid UTF-8.

`credentials.py` and `otp.py` both use this helper. `auth --delete` refuses to
delete a symlinked credentials file. `--data-dir` state files get a separate
`check_state_file()` guard in `paths.py`.

**Tests:** `test_credentials_symlink_is_rejected`,
`test_otp_file_symlink_is_rejected`, `test_state_file_symlink_is_rejected`,
`test_state_file_must_be_a_regular_file`,
`test_otp_file_is_size_limited`.

### F02 — OTP persistence in Woob state

**Before:** `FortuneoEngine.close()` called `backend.deinit()` first. Woob's
`deinit()` calls `dump_state()`, which serializes `browser.__states__`, including
`sms_form`. After an OTP refusal, `sms_form` can still contain the submitted
`otp` value, so the code could land in `state.yml`.

**Fix:** `close()` now calls `_scrub_transient_auth(backend)` **before**
`deinit()`:

- `sms_form`, `add_recipient_form`, `execute_transfer_form` are set to `None`;
- `need_reload_state` is reset;
- `config["code"]` and `config["sca_code"]` are reset to `None` when supported.

The same scrub runs after every successful operation. A test spies on
`deinit()` and asserts the values are already cleared when Woob serializes.

**Test:** `test_close_scrubs_otp_before_deinit`.

### F03 — Structured error details

**Before:** `_emit_error()` redacted the message and traceback but emitted
`exc.details` unchanged.

**Fix:** `redact_value()` recursively redacts strings in dictionaries, lists
and tuples. `_emit_error()` now applies it to `details`.

**Test:** `test_error_details_are_redacted`.

### F04 — Export error and warning redaction

**Before:** `FortuneoEngine.export()` copied `str(exc)` into per-account errors
and warnings. Upstream messages can contain URLs or form values.

**Fix:** the engine keeps a redacted view of credential secrets and applies
`redact()` to:

- per-account error messages;
- `iter_coming` warnings;
- warning text in the final export;
- authentication/upstream exception messages raised by `_call()`.

**Test:** `test_export_error_messages_are_redacted`.

### F05 — Export totals schema

The export previously returned `totals.positions` while the README documented
`totals.positionValues`. The key is now `positionValues`, matching the
documented schema and avoiding a silent consumer mismatch.

**Test:** `test_export_totals_position_values`.

### F06 — Permissions of pre-existing directories

The CLI creates missing runtime directories with `0700` and deliberately does
not chmod pre-existing directories (they may be shared or user-managed).
`doctor` now inspects `root`, `config`, `data`, `cache` and `home`, reports a
warning with the exact `chmod 700` command when group/other bits are present,
and reports the state-file mode.

**Tests:** `test_doctor_warns_about_state_permissions`,
`test_ensure_does_not_chmod_preexisting_directories`.

### F07 — Woob session state

Before 0.2.0, `state.yml` was written on every run and contained session
cookies, a URL and, depending on the flow, PKCE values and OAuth token fields.
Since 0.2.0:

- state is **not persisted by default**; `persist_state=False` disables Woob
  storage and removes any pre-existing state file on exit;
- `--state` is an explicit opt-in; the resulting file is forced to `0600`;
- the file is refused if it is a symlink or not a regular file;
- `--no-state` remains accepted as a deprecated no-op for compatibility.

The state is intentionally not encrypted, because decrypting it would require
another key stored on the same machine. Users who need session reuse must opt in
with `--state` and should treat `data/state.yml` as sensitive.

**Tests:** `test_default_does_not_persist_state`,
`test_default_disables_woob_storage`, `test_state_opt_in_uses_woob_storage`,
`test_engine_refuses_state_symlink`, `test_close_hardens_state_file`.

### F08 — Plaintext credentials file

Before 0.2.0, `credentials.json` contained `login` **and** `password` in
plaintext JSON. Since 0.2.0 the file contains only the customer ID:

```json
{"login": "12345678"}
```

The password is resolved at runtime from `FORTUNEO_PASSWORD`,
`--password-stdin` (single line, 4096-character cap), or a `getpass` terminal
prompt. It is never written, never placed in `argv`, and is discarded when the
process exits. Protections for the login file:

- atomic write to a temporary file created with `mkstemp`;
- `fchmod(0600)` before writing;
- `fsync` then `os.replace`;
- `0600` re-applied after replacement;
- `doctor` reports the mode and `passwordStored`;
- file reads reject group/other permissions, symlinks and non-regular files;
- a 0.1.x file that still contains `password` is rejected with an explicit
  `auth --forget-password` migration path.

The customer ID is an identifier, not a secret. It is still personal data: keep
the runtime directory out of cloud sync and backups. This does **not** protect
against malware running as the same user or a keylogger; keeping the password
off disk does mean that a later disk/backup leak no longer exposes it.

### F09 — Environment credentials

`FORTUNEO_LOGIN` / `FORTUNEO_PASSWORD` are supported for supervised automation.
On some systems, environment variables of the same user's processes can be
inspected. The README and `SECURITY.md` recommend the interactive prompt or
`--password-stdin`. Passwords are never placed in `argv`.

### F10 — Dependencies and supply chain

- `woob==3.7` is pinned in `pyproject.toml`.
- Transitive dependencies are not locked. CI installs the current compatible
  versions and runs tests.
- The vendored Fortuneo module is pinned by version `202605011706` and its
  local patches are documented. It is not auto-updated.
- Audit command used during this review:

  ```bash
  uvx pip-audit --path .venv/lib/python3.11/site-packages
  ```

  Result before the fix: 9 advisories against the stale build-time
  `setuptools 65.5.0` in the development virtualenv. `setuptools` was upgraded
  to `84.2.1` (the project requires `setuptools>=77` to build) and the audit was
  re-run:

  ```text
  No known vulnerabilities found
  ```

  Runtime dependencies (`woob==3.7` and its transitive packages) reported no
  known advisories. Re-run this command in a fresh environment before each
  release.

### Logging and error handling

- `RedactionFilter` and `RedactionFormatter` are attached to the
  `fortuneo_sidecar` and `woob` loggers.
- Known secrets are replaced first, longest first. Variants covered include
  raw, URL-encoded, `quote_plus`, JSON-escaped, query-string, form-encoded and
  `Authorization: Bearer/Basic` values.
- Generic fields (`password`, `access_code`, `sca_code`, `otp`, `code`,
  `session_id`, `state`, `token`, `access_token`, `refresh_token`) are masked
  even when the exact secret is unknown.
- Debug tracebacks pass through the same redaction.
- Typed exceptions are emitted as JSON on stderr; unexpected exceptions become
  `INTERNAL_ERROR` without a traceback unless `--debug` is used.
- `--raw` is opt-in and deliberately exposes more Woob fields.

### Network review

- Only `https://mabanque.fortuneo.fr` and `https://api.fortuneo.fr` are used by
  the vendored module.
- No `verify=False`, custom CA bypass or HTTP endpoint was found in original or
  vendored code.
- `doctor --network` reads `env.json` and reports only the presence of
  `apigeeUrl` / `apigeeApiKey`, never the key value.
- OAuth callback errors are reduced to safe error codes; the callback URL and
  its values are not logged.
- The SCA bearer token is sent in an `Authorization` header, not in a URL.

### Process and CLI review

- No `subprocess`, `eval`, `exec`, `pickle` or shell invocation in the runtime
  code.
- YAML is loaded by Woob with `SafeLoader`.
- `--account` selection requires a unique match; ambiguity is an error rather
  than a guess.
- Output schemas are versioned; exit codes distinguish usage, credentials,
  authentication, upstream, module and not-found errors.
- `--no-interactive` and `--otp-stdin` make unattended behavior explicit.
- `--password-stdin` and `--otp-stdin` cannot be combined; both consume stdin.
- `--state` and `--no-state` cannot be combined.
- `auth` writes only `{"login": ...}`; `--forget-password` rewrites a legacy
  file without its password; `--check` reports `passwordStored` without
  printing any secret.

### Tests executed

```text
.venv/bin/pytest
117 passed

uvx pip-audit --path .venv/lib/python3.11/site-packages
No known vulnerabilities found
```

Coverage includes login-only storage and permissions, legacy password rejection
and migration, symlink refusal, URL/JSON/header redaction, OTP channels and
anti-reuse, OTP scrubbing before `deinit`, default-vs-opt-in state, export
partial errors and redaction, totals schema, CLI error details, doctor
permission/password/state warnings, password stdin and TTY prompts, incompatible
flags, account selection, serialization and the vendored module loading offline.

## Residual risk register

| Risk | Severity | Mitigation / recommendation |
|---|---|---|
| Malware or keylogger running as the user reads the customer ID, OTP or in-memory password | High but out of scope | Use a dedicated, hardened user account; keep the OS updated; avoid untrusted software. |
| Opt-in session cookies in `state.yml` are stolen | Medium | Only pass `--state` when session reuse is needed; rely on `0600` and disk encryption. |
| Customer-ID file is copied through backups or sync tools | Medium | Exclude `~/.local/share/fortuneo-sidecar` from cloud sync and backups, or use `--data-dir` on an encrypted volume. |
| SMS code is intercepted or the phone is compromised | Medium | Outside the CLI; contact Fortuneo if SCA is abused. |
| A transitive dependency has a CVE | Medium | Run `pip-audit` before release; pin or update deliberately. |
| Fortuneo changes its API/SCA flow | Medium | The CLI fails explicitly; update the vendored module and re-run the real smoke test. |
| Export redirected to a world-readable file | Medium | Write to a private directory; `umask 077`; encrypt at rest if needed. |
| User runs with `--raw` and shares output | Low | Treat `--raw` output as sensitive; redact before sharing. |

## Recommendations

1. Run `pip-audit` and the full test suite before each release.
2. Keep `woob==3.7` pinned and review every vendored module update.
3. Leave `--state` off unless Woob session reuse is genuinely needed.
4. Keep the runtime directory out of cloud sync and backups.
5. Prefer the interactive prompt or `--password-stdin` over environment
   variables for the password.
6. Use full-disk encryption and a dedicated user account for automation.
7. Re-run the real smoke test after any Fortuneo or Woob update.

## Repository publication check (0.2.0)

The public repository was reviewed before republication:

- every blob reachable from `main` and the tag was scanned for the real login,
  the real password, email addresses, French IBAN patterns, phone numbers,
  long digit runs and local home paths;
- the two example account numbers that resembled real identifiers were replaced
  with `000000000000`;
- the commit/tag metadata uses the GitHub `noreply` address
  `116721352+Romaindujardin@users.noreply.github.com`;
- GitHub secret scanning reports zero alerts;
- no `credentials.json`, `state.yml`, OTP file, export or runtime directory is
  tracked by Git;
- the 0.1.0 tag and release that contained the personal email in their metadata
  were withdrawn from public view: the old repository is archived as
  `Romaindujardin/fortuneo-cli-archive-0.1.0` (private), and the public
  `fortuneo-cli` repository starts from a clean single commit plus `v0.2.0`.
  Anonymous requests for the old commit and `v0.1.0` release return 404, and the
  archived repository has no forks.
