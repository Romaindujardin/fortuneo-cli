# Security policy

## Supported versions

| Version | Supported |
|---|---|
| `0.2.x` | Yes |
| `0.1.x` | No (password was stored on disk; upgrade to 0.2.x) |
| Older / forks | No |

Only the latest tagged release is supported. Security fixes are released as a
new patch version and documented in `CHANGELOG.md`.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

Use GitHub's private vulnerability reporting:

<https://github.com/Romaindujardin/fortuneo-cli/security/advisories/new>

Include, when possible:

- the affected version or commit;
- the command and environment used;
- a minimal reproduction;
- the impact you believe is possible;
- whether real credentials, OTP codes, cookies or account data were exposed.

You will receive an acknowledgement as soon as possible. Please allow time for
a fix and a coordinated release before publishing details.

## Scope

In scope:

- credential, OTP and session-state storage;
- secret leakage through stdout, stderr, logs, tracebacks or errors;
- permission and symlink handling;
- argument handling and process-environment exposure;
- unsafe network usage in original code;
- vulnerabilities introduced by the original code of this repository.

Out of scope:

- vulnerabilities in Fortuneo's website, API or SMS infrastructure;
- vulnerabilities in Woob itself (report them upstream, then inform this
  project if the vendored module is affected);
- an attacker who already controls your user account, shell, keylogger or
  disk encryption;
- account takeover performed through Fortuneo's own channels;
- the plaintext nature of the customer-ID file when the filesystem
  permissions and disk encryption are working as intended (it contains no
  password).

## Security model

### Assets

- Fortuneo customer ID (login) and password;
- SMS/SCA codes;
- session cookies, OAuth tokens and PKCE values stored by Woob;
- account data exported by the CLI (balances, transactions, positions, IBAN,
  profile).

The **customer ID is an identifier, not a secret**: it is stored so the CLI can
address the right account. The **password is a secret and is never stored**.

### Trust boundaries

- The local user account is trusted. Other local users and unprivileged
  processes are not.
- The network is untrusted; TLS certificate verification is mandatory.
- Fortuneo and its API are trusted for authentication, but their responses are
  treated as untrusted input and parsed defensively.
- The Woob module is trusted code, vendored at a recorded version and reviewed
  when updated.

### Storage

| Path | Content | Mode |
|---|---|---|
| `~/.local/share/fortuneo-sidecar/` | runtime root | `0700` when created by the CLI |
| `config/credentials.json` | customer ID only (`{"login": ...}`) | `0600` |
| `data/state.yml` | opt-in Woob browser state (cookies, PKCE, SCA form) | `0600` |
| `cache/`, `home/` | sandboxed dependency data | `0700` |
| `--otp-file` | one SMS code | must be `0600` |
| `--credentials-file` | customer ID only | must be `0600` |

The CLI never changes the permissions of a pre-existing directory it did not
create. `doctor` reports insecure modes with the exact `chmod` command.

The customer-ID file is **plaintext JSON** containing no password. This is a
deliberate trade-off for a portable CLI: it relies on `0600`, the user's umask,
disk encryption and the operating system's account separation.

**The password is never written to disk.** It comes from `FORTUNEO_PASSWORD`,
`--password-stdin`, or a terminal prompt (via `getpass`), and lives only in
memory for the duration of the command. A 0.1.x file that still contains a
`password` field is rejected until the user runs
`fortuneo-sidecar auth --forget-password`.

**Woob session state is not persisted by default.** Passing `--state` opts in
to `data/state.yml`; the file is forced to `0600` and symlinks are refused. When
`--state` is not passed, an existing state file is removed on exit and Woob
storage is disabled.

### Secrets in transit and in memory

- Passwords and OTP codes are never accepted in `argv`.
- The password is read from `FORTUNEO_PASSWORD`, `--password-stdin` (a single
  line, with a 4096-character cap) or a terminal prompt; it is never echoed and
  never serialized.
- Credential and OTP files are opened with `O_NOFOLLOW`, checked with `fstat`
  as regular files, and rejected if group/other readable or larger than 64 KiB.
- SMS codes are validated as 4 to 8 digits and cannot be reused in the same
  process.
- Transient 2FA forms (`sms_form`, recipient/transfer forms) and `code` /
  `sca_code` values are cleared **before** Woob's `deinit()` serializes state.
- Logs, errors, structured error details and debug tracebacks pass through a
  redaction layer that removes known secrets plus common URL, JSON,
  form-encoded and `Authorization` representations.
- `--raw` deliberately includes more Woob fields; treat its output as sensitive.

### Network

The CLI only uses HTTPS endpoints from the vendored Fortuneo module:

- `https://mabanque.fortuneo.fr`
- `https://api.fortuneo.fr`

Woob/requests uses the system CA store and certificate verification is not
disabled anywhere in this repository. `doctor --network` reads
`https://mabanque.fortuneo.fr/env.json` and reports only whether the expected
keys are present, not the API key value.

### Dependencies

- Woob is pinned to `3.7` in `pyproject.toml`.
- The Fortuneo module is vendored at `202605011706`; its local SCA patches and
  upstream references are documented in `UPSTREAM.md`.
- CI runs the test suite on supported Python versions. For release work, run
  `pip-audit` in a fresh virtual environment and update pins deliberately.
- Do not install this project from an untrusted fork. Review the diff of any
  vendored module update.

### Known limitations

- A malicious process running as your user can read the customer-ID file,
  inspect process memory while a command runs (including the password), install
  a keylogger or modify the CLI. Keeping the password off disk reduces exposure
  after the process exits but cannot defeat a compromised session.
- Environment variables are visible to some same-user process inspection tools;
  prefer the interactive prompt or `--password-stdin` on a shared machine.
- SMS interception, SIM swapping and account takeover at Fortuneo are outside
  the CLI's control.
- Fortuneo can change its website, API or SCA flow at any time; a failed login
  is reported explicitly rather than bypassed.
- The CLI does not implement certificate pinning.
- The CLI does not encrypt exports. Redirect them to a protected location and
  handle them as sensitive financial data.
