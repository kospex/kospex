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
- `src/kospex_cli.py`: `_explain_no_package_record()` picks the message.
- `tests/test_first_release.py`: `TestScaPackageNetworkErrors` replaces
  `requests.get` under the real `url_request_with_status`, so the whole path
  from the request to the CLI message is exercised.
- `docs/troubleshooting.md`: "SSL Certificate Errors (deps.dev, PyPI and other
  lookups)".

## Implementation notes

- `last_url_error` is instance state, read straight after the call that set
  it. It is not thread-safe; nothing in the `sca -package` path is threaded.
- Only `sca -package` reads it so far. The file-based paths (`kospex sca FILE`,
  `krunner osi`) still record an unreachable deps.dev as a package that was not
  found. The log line now makes that visible; reporting it per row is a
  follow-up.
- `kospex connectivity -save` writes the server's leaf certificate to
  `~/kospex/REQUEST_CA_CERTS` and suggests it as `REQUESTS_CA_BUNDLE`. A
  leaf-only file does not verify (`unable to get local issuer certificate`,
  checked against api.deps.dev), so the new troubleshooting section does not
  point at it. Fixing `-save` to write the issuing CA is a follow-up.
