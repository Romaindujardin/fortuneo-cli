# Architecture

```text
fortuneo-sidecar (CLI)
        |
        v
FortuneoEngine  --->  Woob  --->  vendored Fortuneo module
        |                              |
        |                              +--> mabanque.fortuneo.fr
        |                              +--> api.fortuneo.fr
        |
        +--> paths.py       private runtime directories, HOME sandbox
        +--> credentials.py customer-ID file, runtime password resolution, redaction
        +--> private_files.py symlink-safe reads
        +--> otp.py         OTP channels and anti-reuse
        +--> serialize.py   stable JSON payloads
```

## Request flow

1. `cli.main()` parses arguments, rejects incompatible flags, resolves paths
   before the HOME sandbox, and ensures the runtime directories exist.
2. `paths.apply_sandbox_env()` redirects `HOME` and XDG variables into the
   private runtime directory for the duration of the command.
3. The customer ID is loaded from `FORTUNEO_LOGIN` or from the private file. The
   password is resolved from `FORTUNEO_PASSWORD`, `--password-stdin`, or a
   terminal prompt; it is never read from disk and never written. Secrets are
   registered with the redaction layer.
4. `FortuneoEngine` loads the vendored module through `WoobBase`. Woob storage
   is disabled by default; `--state` enables `StandardStorage` for browser
   state.
5. On a `BrowserQuestion`, the engine asks `OTPProvider` for the code, sets it
   on the backend configuration, and retries the operation. Codes are
   validated, single-use per process, and cleared before state serialization.
6. Serializers convert Woob objects to versioned JSON. Missing values become
   `null`; dates use ISO 8601; amounts are JSON numbers.
7. `close()` scrubs transient authentication values, calls Woob's `deinit()`,
   and either removes the state file (default) or forces it to `0600`
   (`--state`).

## Error model

`errors.py` defines typed exceptions with stable codes and exit codes. Expected
failures become `fortuneo-sidecar/error/v1` JSON on stderr. Authentication and
module-loading failures abort an export; per-account failures are collected in
`errors[]` and set `status` to `"partial"`.

## Storage layout

```text
~/.local/share/fortuneo-sidecar/
  config/credentials.json   0600  {"login": "..."} only
  data/state.yml            0600  opt-in Woob browser state (--state)
  cache/                    0700
  home/                     0700  sandboxed HOME
```

The password is never part of this layout. Directories are only chmodded when
the CLI creates them. Pre-existing directories keep their mode and are reported
by `doctor` when insecure.

### Credential resolution

| Step | Customer ID | Password |
|---|---|---|
| 1 | `FORTUNEO_LOGIN` | `FORTUNEO_PASSWORD` |
| 2 | `credentials.json` (`login`) | `--password-stdin` |
| 3 | — | terminal prompt (`getpass`) |

A complete `FORTUNEO_LOGIN` + `FORTUNEO_PASSWORD` pair short-circuits the file.
Any other combination requires a password channel; otherwise the command fails
with `PASSWORD_REQUIRED` (exit code 3). A legacy file containing `password` is
rejected and must be migrated with `auth --forget-password`.

## Vendored module

The Fortuneo module lives under
`fortuneo_sidecar/vendor/woob_modules/fortuneo/`. It is loaded from the local
filesystem, not downloaded at runtime. Its provenance and local SCA patches are
recorded in `UPSTREAM.md`.

## Testing strategy

- Unit tests use `FakeBackend` and `FakeValue` to exercise the CLI, engine,
  OTP flow, serialization and redaction without network access.
- `test_sca.py` exercises the vendored browser's SCA handling with mocked HTTP
  responses.
- `test_real_vendored_module_loads_without_network` verifies that the vendored
  module and its configuration load offline.
- `scripts/real_smoke.py` is the manual end-to-end check; it is not part of the
  automated suite.
