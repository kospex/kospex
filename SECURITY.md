# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

**Note**: Kospex is currently in beta, and active development. Security updates are provided for the latest release only.

## Reporting Security Vulnerabilities

If you discover a security vulnerability in Kospex, please help us maintain a secure environment by reporting it responsibly.

### How to Report

- **Email**: peter@kospex.io
- **GitHub Security Advisories**: Use [Report a vulnerability](https://github.com/kospex/kospex/security/advisories/new) on the repository's "Security" tab to report privately
- **Response Time**: We aim to acknowledge reports within 5 business days

### Scope

Kospex reads and parses the content of the repositories it analyses — dependency
manifests and lock files (JSON, YAML, TOML, XML and others), git metadata, and source
files passed to `scc`. **Treat repository content as potentially untrusted input.** You
may trust your own repositories, but anything cloned from a public source — or from a
contributor you don't control — can contain deliberately crafted files.

In scope:

- Vulnerabilities triggered by analysing a crafted repository or manifest (e.g. code
  execution, path traversal, XML entity expansion, resource exhaustion)
- Vulnerabilities in the CLI tools (`kospex`, `kgit`, `krunner`, `kreaper`, `kospex-agent`)
- Vulnerabilities in the web interface (`kweb`) when bound to loopback, e.g. XSS or
  injection reachable from repository data

Out of scope:

- The absence of authentication or TLS on the web interface — these are documented
  limitations (see [Known Limitations](#known-limitations))
- Issues that require the web interface to be exposed on a routable network interface

### What to Include

- Description of the vulnerability
- Steps to reproduce the issue
- Potential impact assessment
- Suggested remediation (if available)

### Our Commitment

- We will acknowledge receipt of your report
- We will investigate and validate the issue
- We will work with you on coordinated disclosure
- We will credit you in our security advisories (unless you prefer to remain anonymous)

## Security Features & Practices

### Built-in Security Testing

Kospex employs multiple layers of automated security testing:

- **GitHub Secrets Detection**: Automatically scans for exposed secrets and API keys
- **CodeQL (SAST)**: Static Application Security Testing for code vulnerability detection
- **Dependabot alerts (SCA)**: Software Composition Analysis alerts for vulnerable dependencies

### Web Interface Security

- **Network Binding**: Web interface runs on loopback interface (127.0.0.1) by default
- **No SSL/TLS**: Kospex Beta does not implement SSL/TLS (justified by loopback-only operation)

> ⚠️ **Warning: the web interface can listen on routable interfaces.** The `--host`
> option binds to any address you give it, and when kweb detects it is running inside a
> Docker container it binds to all interfaces (`0.0.0.0`) automatically. The web
> interface has **no authentication**, so anyone who can reach that port can read
> everything in your kospex database. Never expose it to a network without a reverse
> proxy that adds TLS and authentication in front of it, and when running in Docker,
> publish the port to loopback only (e.g. `-p 127.0.0.1:8000:8000`).

### Data Security

- **Local Storage**: All repository data and analysis results stored locally in user's filesystem
- **Database Security**: SQLite database files stored in user-controlled directory (`~/kospex/`)
- **No Cloud Storage**: No data is stored on external services by default
- **Outbound Lookups**: Some commands contact external services. Repository content
  and commit history are never uploaded, but the following are sent:
  - **deps.dev** (`api.deps.dev`) — dependency package names and versions, for
    version, licence and advisory lookups
  - **PyPI** (`pypi.org`) — Python package names, for package metadata
  - **GitHub API** (`api.github.com`) — organisation, user and repository names, with
    your `GITHUB_TOKEN` if set, when listing or cloning repositories
  - **Bitbucket API** (`api.bitbucket.org`) — workspace and repository names, with
    your Bitbucket credentials, when using `kgit bitbucket`
  - **Git remotes** — the hosts you clone from or pull from via `kgit`

## Security Considerations

### Installation & Configuration

- **Default Installation**: Installs to user's home directory (`~/kospex/` and `~/code/`)
- **File Permissions**: User responsible for setting appropriate filesystem permissions
- **Configuration Files**: Stored in `~/kospex/` directory with user-level access

### GitHub Integration

- **API Tokens**: GitHub Personal Access Tokens stored in environment variables or configuration
- **Token Scope**: Recommend minimal required scopes for GitHub API access
- **Rate Limiting**: Respects GitHub API rate limits to avoid service disruption

### Bitbucket Integration

- **API Tokens**: `kgit bitbucket` uses `BITBUCKET_API_TOKEN` together with
  `BITBUCKET_EMAIL` (or `BITBUCKET_USERNAME`), read from environment variables
- **Token Scope**: Atlassian account API tokens work without scopes; for
  Bitbucket-scoped tokens grant only `read:project:bitbucket`,
  `read:repository:bitbucket` and `read:workspace:bitbucket`
- **App Passwords**: `BITBUCKET_APP_PASSWORD` is legacy — Atlassian disabled app
  passwords on 2026-06-09 — and support for it will be removed

### Dependency Management

- **Dependabot Alerts**: Dependabot monitors dependencies and raises alerts for known vulnerabilities
- **Vulnerability Scanning**: Regular dependency vulnerability assessments
- **Pin Versions**: Critical dependencies pinned or ranged to specific versions in `pyproject.toml`

### Web Application Security

- **Input Validation**: Development practice is to validate data before it is used —
  request parameters, repository identifiers and data read from analysed repositories
  are checked or constrained before they reach queries, file paths or templates
- **SQL Injection**: Uses parameterized queries and SQLite-utils for database operations
- **Cross-Site Scripting (XSS)**: Jinja2 templates with automatic escaping enabled
- **Authentication**: Kospex Beta does not have authentication, the web UI runs on loopback. 

## Deployment Security Recommendations

### Local Development

1. **File Permissions**: Set restrictive permissions on `~/kospex/` directory:
   ```bash
   chmod 700 ~/kospex
   chmod 600 ~/kospex/*.db
   ```

2. **Environment Variables**: Store sensitive tokens in environment variables, not configuration files:
   ```bash
   export GITHUB_TOKEN=your_token_here
   ```

3. **Network Access**: Web interface accessible only via `http://127.0.0.1:8000` by default

### Production Considerations

⚠️ **Important**: Kospex Beta is designed for local development and analysis. Production deployment requires additional security measures:

- **Reverse Proxy**: Use nginx/Apache with SSL/TLS termination
- **Authentication**: Implement proper authentication and authorization
- **Network Security**: Configure firewalls and network access controls
- **Monitoring**: Implement logging and monitoring for security events

## Known Limitations

### Current Security Limitations

- **No Authentication**: Web interface has no built-in authentication mechanism, relying on 127.0.0.1 connection only
- **No SSL/TLS**: Direct HTTPS support not implemented in MVP
- **File System Security**: Relies on operating system file permissions
- **Session Management**: The application does not use sessions currently.

### Future Security Enhancements

- [ ] Web interface authentication system
- [ ] SSL/TLS support for production deployments
- [ ] Enhanced audit logging
- [ ] Role-based access controls

## Third-Party Dependencies

### Security Monitoring

- **Dependabot Alerts**: Automated dependency vulnerability monitoring
- **GitHub Security Advisories**: Receive notifications for dependency vulnerabilities
- **Regular Updates**: Dependencies reviewed and updated as alerts are raised

### Key Security-Relevant Dependencies

- **FastAPI**: Web framework security features
- **SQLite**: Local database with file-system level security
- **Jinja2**: Template engine with XSS protection

## Security Best Practices for Users

### For Developers

1. **Keep Updated**: Always use the latest version of Kospex
2. **Token Security**: Use GitHub tokens with minimal required scopes
3. **File Permissions**: Set appropriate permissions on kospex directories
4. **Network Security**: Keep web interface on loopback interface for local use

### For Repository Analysis

1. **Sensitive Data**: Be cautious when analyzing repositories containing sensitive information
2. **Token Management**: Rotate GitHub tokens regularly
3. **Local Storage**: Understand that analyzed data is stored locally in SQLite databases
4. **Cleanup**: Remove analysis data when no longer needed

## Security Resources

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [GitHub Security Advisories](https://github.com/advisories)
- [Python Security Guidelines](https://python.org/dev/security/)
- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)

## Contact Information

- **Security Issues**: peter@kospex.io, or [report a vulnerability privately](https://github.com/kospex/kospex/security/advisories/new)
- **General Support**: [GitHub Issues](https://github.com/kospex/kospex/issues)
- **Project Website**: [https://kospex.io](https://kospex.io)

---

*This security policy is updated regularly. Last updated: 2026-10-19*
