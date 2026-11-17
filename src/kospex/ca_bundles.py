"""Finding a CA bundle that lets requests verify an HTTPS host (#453).

requests verifies against certifi's bundle, or REQUESTS_CA_BUNDLE /
CURL_CA_BUNDLE when set; it never reads the operating system's store or
SSL_CERT_FILE. Behind a TLS-intercepting proxy the organisation's CA is
usually installed in the system store (that is what makes apt, dnf, curl and
git work), so the fix is to point REQUESTS_CA_BUNDLE at the system bundle.

`kospex connectivity` uses this module to find which system bundle verifies
deps.dev and, with -save, to write it to kospex.env. It replaced saving the
server's own certificate, which can never work as a bundle: verification needs
the CA that issued it, and REQUESTS_CA_BUNDLE replaces certifi rather than
adding to it, so a file of proxy certificates alone also breaks every host
that bypasses the proxy.
"""
import os
import ssl
from dataclasses import dataclass
from typing import Optional

import requests

# Where distributions keep the system bundle, as one PEM file. Python's own
# default (ssl.get_default_verify_paths) is tried first; these cover the
# common Linux layouts when Python was built against another OpenSSL.
KNOWN_BUNDLE_PATHS = (
    "/etc/ssl/certs/ca-certificates.crt",                # Debian, Ubuntu, Alpine, Arch
    "/etc/pki/tls/certs/ca-bundle.crt",                  # RHEL, Fedora, CentOS
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # RHEL, Fedora
    "/etc/ssl/ca-bundle.pem",                            # SUSE
    "/etc/ssl/cert.pem",                                 # macOS, Alpine, BSD
)

BUNDLE_VARS = ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


def bundle_in_use() -> tuple[Optional[str], str]:
    """(variable name or None, path) for the bundle requests will verify with.

    requests reads the first non-empty of REQUESTS_CA_BUNDLE, CURL_CA_BUNDLE,
    and otherwise uses certifi.
    """
    for var in BUNDLE_VARS:
        if os.environ.get(var):
            return var, os.environ[var]
    import certifi
    return None, certifi.where()


def system_bundles() -> list[str]:
    """Existing system CA bundle files, Python's default first, without duplicates.

    Two names for one file (a symlink) are listed once, under the first name.
    """
    defaults = ssl.get_default_verify_paths()
    seen, bundles = set(), []
    for path in (defaults.cafile, defaults.openssl_cafile, *KNOWN_BUNDLE_PATHS):
        if not path or not os.path.isfile(path):
            continue
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        bundles.append(path)
    return bundles


@dataclass
class BundleCheck:
    """The result of connecting to a URL with one CA bundle."""
    path: str
    verified: bool
    error: Optional[str] = None   # None when verified
    ssl_error: bool = False       # the failure was certificate verification


def check_bundle(path: str, url: str, timeout: int = 10) -> BundleCheck:
    """Connect to `url` verifying with `path`. Any HTTP response is verified:
    the TLS handshake succeeded, which is all that is being tested."""
    try:
        requests.get(url, timeout=timeout, verify=path)
        return BundleCheck(path, True)
    except requests.exceptions.SSLError as e:
        return BundleCheck(path, False, str(e), ssl_error=True)
    except (requests.RequestException, OSError) as e:
        # OSError: requests' "Could not find a suitable TLS CA certificate bundle".
        return BundleCheck(path, False, f"{type(e).__name__}: {e}")


def find_working_bundles(url: str, exclude=(), timeout: int = 10) -> list[BundleCheck]:
    """check_bundle() for every system bundle not in `exclude`, in order."""
    skip = {os.path.realpath(p) for p in exclude if p}
    return [check_bundle(path, url, timeout) for path in system_bundles()
            if os.path.realpath(path) not in skip]


def save_to_kospex_env(env_file: str, path: str) -> Optional[str]:
    """Set REQUESTS_CA_BUNDLE=`path` in `env_file`, creating it if needed.

    Other lines, comments included, are kept; an existing REQUESTS_CA_BUNDLE
    line (with or without `export`) is replaced. Returns the value it
    replaced, or None.
    """
    from dotenv import dotenv_values, set_key

    previous = dotenv_values(env_file).get("REQUESTS_CA_BUNDLE") \
        if os.path.exists(env_file) else None
    os.makedirs(os.path.dirname(env_file) or ".", exist_ok=True)
    set_key(env_file, "REQUESTS_CA_BUNDLE", path, quote_mode="never")
    return previous
