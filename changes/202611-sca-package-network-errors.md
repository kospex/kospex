# `sca -package`: report SSL and connection errors, not "no record"

## Overview

Behind a TLS-intercepting proxy, `kospex sca -package pypi:requests` printed
`deps.dev has no record of requests (pypi).` The package exists; the request
had failed certificate verification.

`KospexQuery.url_request_with_status()` catches `requests.RequestException` and
returns `(None, None)`. `SSLError` is a `RequestException`, so it was swallowed
without a log line. `release_list()` turned that into `None`, `first_release()`
passed the `None` on, and the CLI read `None` as "no record".

Now:

| What happened | `sca -package` says | Exit |
|---|---|---|
| `requests.exceptions.SSLError` | SSL error, the detail, how to set `REQUESTS_CA_BUNDLE` | 1 |
| Any other `RequestException` (DNS, refused, timeout, proxy) | `Could not reach deps.dev ... ExceptionName: detail` | 1 |
| HTTP status other than 200 / 404 | `deps.dev returned HTTP N` | 1 |
| 404, or a 200 with no versions | `deps.dev has no record of ...` (unchanged) | 1 |

Every swallowed `RequestException` is now logged at WARNING in
`kospex_query.log`, whichever command made the request.

## Why `REQUESTS_CA_BUNDLE`

requests verifies against certifi's bundle, not the OS store, and ignores
`SSL_CERT_FILE` (checked with requests 2.34.2 / Python 3.12: a bogus
`SSL_CERT_FILE` still reaches deps.dev, a bogus `REQUESTS_CA_BUNDLE` or
`CURL_CA_BUNDLE` fails). So a CA that makes `apt`, `curl` or `git` work is not
seen by kospex. `kospex.env` is loaded with `load_dotenv` (no override), so the
variable can go there as well as in the environment.

## Files changed

- `src/kospex_query.py`: `url_request_with_status()` sets `last_url_status`
  and `last_url_error` on the instance and logs the exception. Its
  `(content, status)` return is unchanged, so none of its callers move.
  It also counts each failure in `url_failures`.
- `src/kospex_utils.py`: `CA_BUNDLE_VARS`, `expand_ca_bundle_paths()` (called
  from `load_config()`), `ca_bundle_problem()`, `SSL_CA_BUNDLE_ADVICE`,
  `url_failure_summary()`.
- `src/kospex_cli.py`: `_explain_no_package_record()` picks the message; `sca`
  checks the bundle path before lookups and prints the failure summary after
  a file run.
- `src/krunner.py`: `osi` checks the bundle path, and `_report_url_failures()`
  runs after the lookups on both paths.
- `tests/test_first_release.py`: `TestScaPackageNetworkErrors` replaces
  `requests.get` under the real `url_request_with_status`, so the whole path
  from the request to the CLI message is exercised.
- `tests/test_ca_bundle.py`: the bundle helpers, the failure count, and
  `krunner osi`.
- `docs/troubleshooting.md`: "SSL Certificate Errors (deps.dev, PyPI and other
  lookups)".

## Implementation notes

- `last_url_error` is instance state, read straight after the call that set
  it. It is not thread-safe; nothing in the `sca -package` path is threaded.
  A failed `-package NAME@VERSION` lookup uses it too, so an SSL error on the
  version request is explained the same way.

## File-based lookups: already `lookup_error`, but silent

The issue said `kospex sca FILE` and `krunner osi` record an unreachable
deps.dev as a missing package. Run with every request raising `SSLError` (a
pin, a `>=` range, an npm caret), they don't: an exact-version miss with no
status is classified `lookup_error` by `_classify_lookup_miss()`, and so is a
range whose release list could not be fetched. The stored data was honest.

What was missing is the reason. Every row read `lookup_error`, the command
exited 0, and nothing mentioned SSL. Now:

- `KospexQuery.url_failures` counts failed requests by exception class and
  keeps the first of each: `{"SSLError": (count, first exception)}`.
  `last_url_error` is reset on every request, so it cannot answer "did
  anything in this run fail".
- After the lookups, `sca FILE` (stderr, so `-json` stays clean) and
  `krunner osi` (both `-all`/scope and `-next`) print
  `KospexUtils.url_failure_summary()`: the count by class, and for an SSL
  failure the CA bundle advice, the same text `-package` prints
  (`KospexUtils.SSL_CA_BUNDLE_ADVICE`).
- The summary is also logged, as one WARNING line
  (`KospexUtils.url_failure_log_line()`: the count, the first error, and for
  SSL a pointer to `REQUESTS_CA_BUNDLE` / `kospex connectivity`, without the
  advice paragraph), in the command's own log: `kospex.log` for sca (naming
  the file), `krunner.log` for osi. The console output of a scheduled
  `krunner osi -next` usually goes nowhere, and `kospex_query.log` has only
  one line per failed request, so without this a run whose every lookup
  failed left no record of it in the log people check.
- Each run clears `url_failures` before its lookups. The query object is
  module-level and outlives a command; in one process (tests, kweb, the
  agent) the count otherwise carried over from the previous run.
- The exit code stays 0: the rows are recorded and correct, and a scheduled
  `krunner osi -next` must not fail its tick because deps.dev was briefly
  unreachable.

## A CA bundle path that doesn't exist

requests checks `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE` only when it connects,
and raises a plain `OSError` ("Could not find a suitable TLS CA certificate
bundle"), not a `RequestException`. Nothing catches it, so `sca -package`,
`sca FILE` and `krunner osi` ended with a traceback on the first lookup.

- `KospexUtils.ca_bundle_problem()` mirrors requests' choice (the first
  non-empty of `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`) and returns a message
  if that path doesn't exist. A directory is accepted, as requests accepts
  one.
- `kospex sca` (both forms) and `krunner osi` call it when they are going to
  look things up, and exit 1 with the message before any request. With
  `-no-enrich` nothing is contacted, so it isn't checked.
- `~` in `kospex.env`: python-dotenv does not expand it, and requests opens
  the path as given, so `REQUESTS_CA_BUNDLE=~/certs/ca.pem` was the common
  way to hit this. `load_config()` now expands a leading `~` in both
  variables (`KospexUtils.expand_ca_bundle_paths()`). A shell expands it
  before kospex runs anyway.

## `kospex connectivity`: find a bundle that works

`-save` wrote the certificate api.deps.dev presented to
`~/kospex/REQUEST_CA_CERTS` and suggested it as `REQUESTS_CA_BUNDLE`. That can
never work, and saving the whole chain doesn't fix it:

- verification needs the CA that issued the certificate, and proxies usually
  don't send their root;
- Python 3.12 (kospex's minimum) has no stdlib way to read the chain
  (`SSLSocket.get_unverified_chain()` is 3.13);
- `REQUESTS_CA_BUNDLE` replaces certifi, so a file of proxy certificates alone
  breaks every host that bypasses the proxy.

The organisation's CA is normally already in the system store (that is what
makes apt and curl work behind the proxy), so connectivity now looks there.
`src/kospex/ca_bundles.py`:

- `bundle_in_use()`: the bundle requests will use (REQUESTS_CA_BUNDLE,
  CURL_CA_BUNDLE, else certifi).
- `system_bundles()`: Python's default verify file, then the Debian/Ubuntu,
  RHEL/Fedora, SUSE and macOS/Alpine paths that exist, a symlinked file listed
  once.
- `check_bundle()` / `find_working_bundles()`: connect with `verify=path`. Any
  HTTP response counts, since only the handshake is being tested.
- `save_to_kospex_env()`: python-dotenv's `set_key`, which creates the file,
  replaces an existing line (with or without `export`) and keeps comments and
  other keys.

The command:

1. Prints the bundle in use. A path that doesn't exist (`ca_bundle_problem()`)
   is treated as a failure, since requests would raise an `OSError`.
2. On an SSL failure, prints the certificate the host presented: subject,
   issuer (behind a proxy, the CA to trust) and validity. The old code read
   these with `getpeercert()`, which returns `{}` when verification is off, so
   they never printed. They are now decoded from the DER through
   `ssl._ssl._test_decode_cert` (private, so an `AttributeError` skips the
   details rather than the command). Verification stays off in this probe on
   purpose, with the TLS 1.2 minimum from alert #27 (see
   `202606-connectivity-tls-fallback.md`).
3. Checks each system bundle and marks which verify.
4. Without `-save`, prints the `export REQUESTS_CA_BUNDLE=` line. With
   `-save`, writes it to `HabitatConfig.config_file`. kospex.env never
   overrides the environment, so if `REQUESTS_CA_BUNDLE` was set in the shell
   (its value isn't the file's), it warns that the shell value still wins.
5. If no bundle works, says the organisation's CA has to go into the system
   store first.

A connection error that isn't SSL (DNS, refused, timeout) stops at step 1: no
bundle fixes it. Exit codes are unchanged (0).

Checked live against api.deps.dev with a bogus `REQUESTS_CA_BUNDLE` in the
shell and in kospex.env, a missing path, and `-save` followed by a re-run,
which then verified. `tests/test_connectivity.py` covers the module and the
command with `requests.get` and the certificate probe replaced. The old
leaf-only file was checked earlier against api.deps.dev: it fails with
`unable to get local issuer certificate`.
