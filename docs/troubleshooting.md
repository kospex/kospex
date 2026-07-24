# Troubleshooting Guide

This guide covers common issues you might encounter when using Kospex and their solutions.

## kgit Clone Issues

### kgit failed to clone repository

If `kgit clone` fails, here are the most common causes and solutions:

#### Authentication Issues
- **Problem**: Clone fails with authentication errors for private repositories
- **Solution**: Ensure Git Credential Manager is installed and configured:
  ```bash
  # Install via Homebrew (recommended)
  brew install git-credential-manager

  # Or configure Git credentials manually
  git config --global credential.helper manager-core
  ```
- **For GitHub**: Set up a Personal Access Token with repository access
- **For Bitbucket**: Set a `BITBUCKET_API_TOKEN` (see [Bitbucket Authentication](#bitbucket-authentication) below)

#### Network and SSL Issues
- **Problem**: Clone fails with SSL certificate or network errors
- **Solution**:
  ```bash
  # Check Git SSL settings
  git config --global http.sslVerify true

  # For corporate networks, you might need to configure proxy
  git config --global http.proxy http://proxy.company.com:8080
  ```

#### Private Certificate Authority (CA) Issues
- **Problem**: Clone fails with SSL certificate verification errors in corporate environments using private CAs
- **Solutions**:

  **Option 1: Add Private CA Certificate to Git (Recommended)**
  ```bash
  # Find your private CA certificate file (usually .crt or .pem)
  # Add the CA certificate to Git's certificate bundle
  git config --global http.sslCAInfo /path/to/your/private-ca.crt

  # Or append to system CA bundle (macOS example)
  cat /path/to/your/private-ca.crt >> /usr/local/etc/openssl/cert.pem
  ```

  **Option 2: Configure Git to Use System Certificate Store**
  ```bash
  # On macOS, use system keychain
  git config --global http.sslCAInfo /etc/ssl/cert.pem

  # On Linux systems
  git config --global http.sslCAInfo /etc/ssl/certs/ca-certificates.crt
  ```

**Option 3: Git Credential Manager, Private CAs and CURL failures**
  Git Credential Manager can call Bitbucket or git servers to detect the server type.
  When it can't reach them, it defaults to launching the UI for the credentials.

  If curl doesn't have a CA bundle set, it will error, even if you've configured your Git SSL certificates.
  The following lets curl use the CA bundle to stop the error.

  ```bash
  # Environment variable for CURL
  #
  export CURL_CA_BUNDLE=/path/to/your/ca-bundle.crt
  ```


  **Option 4: Add CA Certificate to System Store**
  ```bash
  # macOS - Add to system keychain
  sudo security add-trusted-cert -d -r trustRoot -k /System/Library/Keychains/SystemRootCertificates.keychain /path/to/your/private-ca.crt

  # Or add to login keychain
  security add-trusted-cert -d -r trustRoot -k ~/Library/Keychains/login.keychain /path/to/your/private-ca.crt

  # Linux (Ubuntu/Debian)
  sudo cp /path/to/your/private-ca.crt /usr/local/share/ca-certificates/
  sudo update-ca-certificates

  # Linux (RHEL/CentOS)
  sudo cp /path/to/your/private-ca.crt /etc/pki/ca-trust/source/anchors/
  sudo update-ca-trust
  ```

  **Option 5: Repository-Specific CA Configuration**
  ```bash
  # Configure CA certificate for specific repository URL
  git config --global http."https://your-git-server.company.com/".sslCAInfo /path/to/your/private-ca.crt

  # Or disable SSL verification for specific host (less secure)
  git config --global http."https://your-git-server.company.com/".sslVerify false
  ```

  **Temporary Workaround (Not Recommended for Production)**
  ```bash
  # Disable SSL verification globally (INSECURE - use only for testing)
  git config --global http.sslVerify false

  # Re-enable SSL verification after fixing CA issues
  git config --global http.sslVerify true
  ```

  **Verifying CA Certificate Installation**
  ```bash
  # Test SSL connection to your git server
  openssl s_client -connect your-git-server.company.com:443 -servername your-git-server.company.com

  # Check current Git SSL configuration
  git config --list | grep ssl

  # Test clone with verbose output
  GIT_CURL_VERBOSE=1 git clone https://your-git-server.company.com/repo.git
  ```

#### Directory Permission Issues
- **Problem**: Cannot write to the clone destination directory
- **Solution**:
  ```bash
  # Check KOSPEX_CODE directory permissions
  ls -la ~/code/

  # Fix permissions if needed
  chmod -R 755 ~/code/
  ```

#### Invalid Repository URL
- **Problem**: Repository URL is malformed or repository doesn't exist
- **Solution**: Verify the URL format and repository accessibility:
  ```bash
  # Test clone directly with git first
  git clone https://github.com/owner/repo /tmp/test-repo
  ```

## Kospex Common Issues

### Database Issues

#### Database Schema Outdated
- **Problem**: Database operations fail with schema-related errors
- **Solution**:
  ```bash
  kospex upgrade-db
  ```

#### Database Permission Errors
- **Problem**: Cannot write to `~/kospex/kospex.db`
- **Solution**:
  ```bash
  # Check permissions
  ls -la ~/kospex/

  # Fix permissions
  chmod 644 ~/kospex/kospex.db
  chmod 755 ~/kospex/
  ```

### Sync Issues

#### Repository Sync Failures
- **Problem**: `kospex sync` fails on a repository
- **Solution**:
  ```bash
  # Check if directory is a valid git repository
  cd /path/to/repo && git status

  # Try syncing with verbose logging
  kospex --debug sync /path/to/repo

  # Check logs for detailed error information
  tail -f ~/kospex/logs/kospex.log
  ```

#### scc Binary Not Found
- **Problem**: File type analysis and complexity metrics are missing
- **Solution**:
  ```bash
  # Install scc via Homebrew
  brew install scc

  # Or check if scc is in PATH
  which scc
  ```

### Authentication and Rate Limiting

#### GitHub Rate Limits
- **Problem**: GitHub API requests are being rate limited
- **Solution**:
  ```bash
  # Set GitHub token for higher rate limits
  export GITHUB_TOKEN=your_github_token_here

  # Or add to your shell profile
  echo 'export GITHUB_TOKEN=your_token' >> ~/.bashrc
  ```

#### Bitbucket Authentication
- **Problem**: Bitbucket API requests (`kgit bitbucket`) fail with authentication errors
- **Solution**: Authenticate with a **Bitbucket API token** (the app-password replacement). Set `BITBUCKET_API_TOKEN` plus **one** of `BITBUCKET_EMAIL` (recommended) or `BITBUCKET_USERNAME` — they are mutually exclusive:
  ```bash
  # Recommended: API token + Atlassian account email
  export BITBUCKET_API_TOKEN=your_api_token
  export BITBUCKET_EMAIL=you@example.com

  # Verify credentials and scopes against a workspace
  kgit bitbucket -test-auth -workspace YOUR_WORKSPACE
  ```

  Two Atlassian token types work:
  - **Atlassian account API token** (`id.atlassian.com` → Security → API tokens) — no scopes required, account-wide.
  - **Bitbucket-scoped API token** (Bitbucket settings → API tokens) — must include all of `read:project:bitbucket`, `read:repository:bitbucket`, `read:workspace:bitbucket`.

- **Problem**: `-test-auth` returns HTTP 403 (not 401)
- **Solution**: 401 means bad credentials; **403 means the token authenticated but is missing scopes**. Add the three scopes above to the token, or switch to an unscoped Atlassian account API token.

> **Deprecated — app passwords (`BITBUCKET_APP_PASSWORD`)**
>
> The legacy path used `BITBUCKET_USERNAME` + `BITBUCKET_APP_PASSWORD` (the Bitbucket username from account settings, **not** your email). **Atlassian permanently disabled all existing app passwords on 2026-06-09**, so this method no longer authenticates — every request using it will fail. It is documented here only for historical reference; migrate to `BITBUCKET_API_TOKEN` as shown above. See [Atlassian API tokens](https://support.atlassian.com/bitbucket-cloud/docs/api-tokens).

## Web Interface Issues

### Web Server Won't Start

#### Port Already in Use
- **Problem**: Cannot bind to port 8000 or 5000
- **Solution**:
  ```bash
  # Use a different port
  python kweb2.py --port 8080

  # Or find what's using the port
  lsof -i :8000
  ```

#### Missing Static Assets
- **Problem**: Web interface loads but styling/JavaScript is broken
- **Solution**:
  ```bash
  # Rebuild frontend assets
  npm install
  npm run build
  ```

## Environment and Setup Issues

### Directory Structure Problems

#### KOSPEX_CODE Directory Issues
- **Problem**: Repositories aren't being found in expected locations
- **Solution**:
  ```bash
  # Check current configuration
  kospex init --validate

  # Recreate directory structure
  kospex init --create

  # Set custom code directory
  export KOSPEX_CODE=/path/to/your/code
  ```

### Logging Issues

#### No Log Output
- **Problem**: Expected log messages aren't appearing
- **Solution**:
  ```bash
  # Enable debug logging
  kospex --debug COMMAND

  # Enable console logging
  kospex --log-console COMMAND

  # Check log files directly
  ls -la ~/kospex/logs/
  tail -f ~/kospex/logs/kospex.log
  ```

#### Log Directory Permission Denied
- **Problem**: Cannot write to log directory
- **Solution**:
  ```bash
  # Create and fix log directory permissions
  mkdir -p ~/kospex/logs
  chmod 755 ~/kospex/logs
  ```

## Getting Help

If you continue to experience issues:

1. **Check logs**: Always check `~/kospex/logs/` for detailed error information
2. **Run with debug**: Use `--debug` flag for verbose output
3. **Validate setup**: Run `kospex init --validate` to check your configuration
4. **Test components**: Use individual test commands like `kgit status` or `kospex system-status`
5. **Check environment**: Verify environment variables and directory permissions

For additional support, check the [GitHub Issues](https://github.com/kospex/kospex/issues) or create a new issue with:
- Operating system details
- Kospex version (`kospex --version`)
- Full error messages and logs
- Steps to reproduce the issue
