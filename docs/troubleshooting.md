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
- **For Bitbucket**: Configure app passwords or OAuth

#### Network and SSL Issues
- **Problem**: Clone fails with SSL certificate or network errors
- **Solution**: 
  ```bash
  # Check Git SSL settings
  git config --global http.sslVerify true
  
  # For corporate networks, you might need to configure proxy
  git config --global http.proxy http://proxy.company.com:8080
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
- **Problem**: Bitbucket API requests fail with authentication errors
- **Solution**: 
  ```bash
  # Set Bitbucket credentials
  export BITBUCKET_USERNAME=your_username
  export BITBUCKET_PASSWORD=your_app_password
  ```

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