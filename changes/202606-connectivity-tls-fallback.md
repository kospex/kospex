# Connectivity command — TLS hardening + legacy fallback (handoff)

**Status:** Part 1 shipped (`6fc14f9`, 2026-06-11). Part 2 designed, not started.
**Owner:** Peter
**Started:** 2026-06-05 · last updated 2026-06-10

## Context

GitHub code-scanning **alert #27** (`py/insecure-protocol`, CWE-327, "high") flagged
`ssl.create_default_context()` in the `kospex connectivity` command
(`src/kospex_cli.py`, ~line 1452).

`kospex connectivity [-save]` is a **diagnostic** command: it connects to
`api.deps.dev`, and if the normal `requests.get` SSL check fails (or `-save` is
passed) it opens a raw socket to pull the server's certificate chain and
optionally append it to `~/kospex/REQUEST_CA_CERTS`. It deliberately sets
`check_hostname = False` / `verify_mode = ssl.CERT_NONE` because it must read the
cert *even when validation fails* (the classic "corporate TLS-intercepting proxy"
scenario). That disabled verification is **by design** and is NOT what alert #27
is about — #27 is only about the TLS protocol version.

## Part 1 — DONE (shipped)

Pinned the minimum TLS version explicitly so the alert clears and the handshake is
safe regardless of Python/OpenSSL defaults. Shipped in `6fc14f9` (2026-06-11);
verified in `main` at `src/kospex_cli.py:1470` on 2026-08-04:

```python
# in connectivity(), cert-probe block (~line 1452)
context = ssl.create_default_context()
context.minimum_version = ssl.TLSVersion.TLSv1_2   # <-- added
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE
```
(plus an explanatory comment block above it).

Part 1 landed on its own rather than being bundled with Part 2, so the alert
clears independently of whether Part 2 is ever built.

## Part 2 — Legacy TLS fallback + warn (DESIGNED, not built)

User question that prompted this: *what if TLS 1.2 isn't supported by the peer
(legacy env on TLS 1.0/1.1) — can we fall back and warn?*

### Verified behaviour (tested on this machine, OpenSSL 3.6.2)

1. With `minimum_version = TLSv1_2`, a TLS-1.1-only peer fails the handshake with
   `ssl.SSLError: SSLV3_ALERT_HANDSHAKE_FAILURE`. This is already caught by the
   existing `except ssl.SSLError` at ~line 1525 → prints "SSL error while
   retrieving certificate". Graceful, but uninformative (doesn't say *why*).

2. **Gotcha:** lowering `minimum_version` alone does NOT enable legacy TLS on
   OpenSSL 3.x. The default security level (SECLEVEL=2) refuses TLS < 1.2
   regardless. Test results against `tls-v1-1.badssl.com:1011`:
   - `min=TLS1.2`               → handshake failure
   - `min=TLS1.1, default seclevel` → handshake failure (still!)
   - `min=TLS1.1, SECLEVEL=0`   → CONNECTED, negotiated TLSv1.1
   So a real fallback needs BOTH `minimum_version = TLSv1_1` AND
   `set_ciphers("DEFAULT@SECLEVEL=0")`. Also: Python emits a `DeprecationWarning`
   for the `TLSv1_1` constant itself.

### Proposed design (agreed direction, pending final go-ahead)

- **Secure-first:** keep the TLS 1.2+ attempt as-is.
- **Fallback probe only on protocol/handshake failure:** retry the *cert probe*
  with `SECLEVEL=0` + lowered minimum, purely to **detect and report** what the
  peer actually speaks, then print a loud warning, e.g.:
  > ⚠ Endpoint negotiated **TLSv1.1**, a deprecated/insecure protocol. Usually a
  > misconfigured corporate TLS-intercepting proxy.
- **Report-only, do not normalise:** detecting weak TLS is the feature; silently
  trusting a TLS 1.1 connection is not. Still save the cert under `-save` if
  retrieved, but the headline is the warning.
- **Never relax the real data path:** the `requests.get(test_url)` call at ~line
  1431 stays strict. Relax only inside the cert-probe block, only for diagnostics.

### Open decisions for next session
- Worth it for *this* endpoint? `api.deps.dev` is Google → always TLS 1.2/1.3; the
  only realistic legacy case is a proxy in the middle. Narrow but legitimate for a
  diagnostic. Leaning yes.
- Exact warning wording + whether to gate the fallback behind a flag
  (e.g. `--allow-legacy-tls`) vs. always-probe-on-failure.
- Before building, run brainstorming on wording/flags (behavior change to a
  user-facing command).

## Key references
- Code: `src/kospex_cli.py`, `connectivity()` command (~lines 1403–1543).
- Alert: https://github.com/kospex/kospex/security/code-scanning/27
- Existing exception handling to integrate with: ~lines 1522–1530.
