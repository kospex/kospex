# Connectivity Command Implementation

**Date:** 2025-10-24
**File Modified:** `src/kospex.py`
**Lines:** 1247-1354

## Summary

Added a new `connectivity` CLI command to test connectivity to `api.deps.dev` and detect SSL certificate errors. This command helps users diagnose SSL/TLS issues and optionally export certificates for troubleshooting.

## Features Implemented

### 1. SSL Connectivity Test
The command makes a request to `api.deps.dev` to check for SSL certificate errors and validates the connection.

### 2. Error Detection & Warning
If SSL errors are detected, the command:
- Captures and displays the error details
- Warns the user with helpful recommendations
- Automatically analyzes the certificate chain

### 3. Certificate Export (`-save` option)
Saves the server's certificate to `~/kospex/REQUEST_CA_CERTS` in PEM format for use with the `REQUESTS_CA_BUNDLE` environment variable.

**Key Features:**
- **Appends certificates**: When saving multiple certificates from different sites, each certificate is appended to the file with descriptive headers
- **Duplicate detection**: Automatically detects if a certificate already exists in the file to avoid duplicates
- **Timestamped entries**: Each certificate includes a timestamp of when it was retrieved
- **CA bundle format**: The file format is compatible with standard CA bundle usage

## Usage

```bash
# Basic connectivity test
kospex connectivity

# Test and save certificate
kospex connectivity -save
```

## Command Output

### Successful Connection
```
Testing connectivity to api.deps.dev...
URL: https://api.deps.dev/v3alpha/systems

✓ Successfully connected to api.deps.dev
  Status Code: 404
  SSL Verification: PASSED

============================================================
✓ Connectivity test completed successfully
============================================================
```

### SSL Error Detected
```
Testing connectivity to api.deps.dev...
URL: https://api.deps.dev/v3alpha/systems

✗ SSL Error detected when connecting to api.deps.dev
  Error: [SSL: CERTIFICATE_VERIFY_FAILED]...

Analyzing certificate chain from api.deps.dev...

✓ Retrieved certificate from api.deps.dev
  Subject: deps.dev
  Issuer: Let's Encrypt
  Valid from: Oct  7 21:46:50 2025 GMT
  Valid until: Jan  5 21:46:49 2026 GMT

============================================================
⚠ WARNING: SSL certificate verification failed!

Recommendations:
  1. Check your system's CA certificate bundle
  2. Update your system's CA certificates
  3. Use the -save flag to export the certificate
  4. Check if you're behind a corporate proxy
============================================================
```

### With `-save` Flag
```
Testing connectivity to api.deps.dev...
URL: https://api.deps.dev/v3alpha/systems

✓ Successfully connected to api.deps.dev
  Status Code: 404
  SSL Verification: PASSED

Extracting certificate chain from api.deps.dev...

✓ Retrieved certificate from api.deps.dev

✓ Certificate saved to: /Users/peter/kospex/REQUEST_CA_CERTS
  You can use this file with the REQUESTS_CA_BUNDLE environment variable:
  export REQUESTS_CA_BUNDLE=/Users/peter/kospex/REQUEST_CA_CERTS

============================================================
✓ Connectivity test completed successfully
============================================================
```

## Key Features

- **Automatic SSL Error Detection**: Catches `requests.exceptions.SSLError` and provides clear feedback
- **Certificate Analysis**: When SSL errors occur or `-save` is used, it extracts and displays:
  - Certificate subject (Common Name)
  - Certificate issuer
  - Validity period (notBefore/notAfter)
- **PEM Export**: Saves the certificate in standard PEM format compatible with the `REQUESTS_CA_BUNDLE` environment variable
- **Helpful Output**: Provides actionable recommendations when SSL errors are detected
- **Logging Integration**: Uses the existing `kospex_logging` system via `get_kospex_logger('kospex')` for audit trails

## Technical Details

### Dependencies Added
- `ssl` (standard library)
- `socket` (standard library)
- Uses existing `requests` library

### Certificate Extraction Method
1. Creates an SSL context with verification disabled (for retrieval only)
2. Connects to the server via socket
3. Wraps socket with SSL to retrieve certificate
4. Converts DER format to PEM format
5. Saves to `~/kospex/REQUEST_CA_CERTS`

### File Location
Certificate is saved to `~/kospex/REQUEST_CA_CERTS` (or `$KOSPEX_HOME/REQUEST_CA_CERTS` if the environment variable is set).

### Certificate File Format
The REQUEST_CA_CERTS file uses a standard PEM format with descriptive comments:

```
# Certificate for api.deps.dev
# Retrieved: 2025-10-24 04:46:37 UTC
-----BEGIN CERTIFICATE-----
MIIGVTCCBT2gAwIBAgISBvMuh4vUivMwhgRzwvj0IPlyMA0GCSqGSIb3DQEBCwUA
...
-----END CERTIFICATE-----

# Certificate for another-site.example.com
# Retrieved: 2025-10-24 05:30:00 UTC
-----BEGIN CERTIFICATE-----
MIIFBjCCAu6gAwIBAgIQDovzdw2S0Zbwu2H5PEFmvjANBgkqhkiG9w0BAQsFADB...
...
-----END CERTIFICATE-----
```

### Multiple Certificates
When you modify the command to test different sites, certificates are **appended** to the file rather than overwritten:
- First run: Creates the file with the first certificate
- Subsequent runs: Appends new certificates with headers
- Duplicate protection: If the same certificate already exists, it won't be added again

This allows you to build a custom CA bundle with certificates from multiple sources.

## Testing Results

✓ Command help displays correctly
✓ Connectivity test passes successfully (Status Code: 404 is expected for the test endpoint)
✓ Certificate extraction and save works correctly
✓ Certificate file is valid PEM format (approximately 2.2KB)
✓ Logging integration working properly

## Use Cases

1. **Troubleshooting Corporate Proxies**: Users behind corporate proxies with SSL inspection can diagnose certificate issues
2. **CA Bundle Issues**: Users with outdated or missing CA certificates can export the certificate for manual installation
3. **Network Diagnostics**: Quick check to verify if `api.deps.dev` is accessible (used by dependency analysis features)
4. **Certificate Debugging**: Developers can inspect the certificate chain when experiencing SSL errors

## Related Files

- `src/kospex.py` - Main CLI implementation (lines 1247-1354)
- `src/kospex_utils.py` - Utilities for getting `KOSPEX_HOME` path
- `src/kospex_logging.py` - Centralized logging system

## Future Enhancements

Potential improvements for future iterations:
- Support for testing multiple endpoints
- Full certificate chain extraction (not just server certificate)
- Certificate validation details (SANs, key usage, etc.)
- Support for custom CA bundle testing
- Integration with `kospex deps` to automatically diagnose SSL issues during dependency analysis
