# Web Endpoint Regression Testing

This document describes how to run web endpoint regression tests for the Kospex web application.

## Overview

The web endpoint tests validate that all endpoints in `kweb2.py` and `api_routes.py` are functional and return expected responses. These tests are designed to:

- Test against a running kweb server on localhost:8000
- Run locally for development and pre-commit validation
- Skip automatically in GitHub Actions CI environment
- Provide comprehensive coverage of all endpoints

## Prerequisites

1. **Running kweb server**: You must have kweb running on localhost:8000
2. **Test dependencies**: pytest and requests (installed via project dependencies)
3. **Optional test data**: Some endpoints work better with data in the kospex database

### Starting the Web Server

```bash
# Option 1: Using the kweb command
kweb

# Option 2: Using the FastAPI development server
python run_fastapi.py

# Option 3: With custom host/port (update BASE_URL in test file accordingly)
python run_fastapi.py --host 0.0.0.0 --port 8080
```

## Running Web Tests

### Basic Usage

```bash
# Run only web endpoint tests (requires kweb running)
pytest -m web

# Run web tests with verbose output
pytest -m web -v

# Run all tests except web tests
pytest -m "not web"

# Run all tests (web tests will be skipped if kweb not running)
pytest
```

### Advanced Usage

```bash
# Run web tests and show coverage
pytest -m web --cov=src

# Run web tests with detailed output
pytest -m web -v -s

# Run specific web test categories
pytest -m web -k "test_html_endpoints"
pytest -m web -k "test_api_endpoints"

# Run web tests and continue on first failure
pytest -m web --tb=short

# Run web tests with custom timeout (modify test file)
pytest -m web --timeout=30
```

## Test Categories

The web endpoint tests are organized into several categories:

### 1. HTML Endpoints - Main Pages
Tests core HTML pages that don't require parameters:
- `/`, `/summary/`, `/help/`, `/developers/`, etc.
- Validates 200 status code and HTML content-type

### 2. HTML Endpoints - With Parameters  
Tests HTML pages that require URL parameters:
- `/summary/{repo_id}`, `/repos/{server}`, etc.
- Accepts both 200 (data found) and 404 (no data) as valid responses

### 3. API Endpoints - JSON Responses
Tests API endpoints that return JSON:
- `/api/servers/`, `/api/developers/`, `/api/health`, etc.
- Validates JSON content-type and structure

### 4. Special Endpoints
Tests endpoints with complex behaviors:
- POST endpoints, file uploads, dynamic parameters
- CORS preflight requests, error handling

## Test Configuration

### Environment Variables

- `CI=true`: Automatically set in GitHub Actions, skips web tests
- `SKIP_WEB_TESTS=true`: Force skip web tests in any environment

### Test Parameters

Edit `tests/test_web_endpoints.py` to customize:

```python
# Base URL for testing
BASE_URL = "http://localhost:8000"

# Request timeout
TIMEOUT = 10  # seconds

# Test data (should exist in your database)
TEST_REPO_ID = "github.com~kospex~kospex"
TEST_SERVER = "github.com"
# ... etc
```

## CI/CD Integration

### GitHub Actions
Web tests are automatically skipped in GitHub Actions. The workflow:

1. Sets `CI=true` and `SKIP_WEB_TESTS=true` environment variables
2. Runs `pytest -m "not web"` to exclude web tests
3. Continues with other tests normally

### Local Development Workflow

1. **Before committing**: Run web tests to catch regressions
   ```bash
   # Start kweb server
   kweb &
   
   # Run web tests
   pytest -m web
   
   # Stop server when done
   kill %1
   ```

2. **During development**: Use in watch mode
   ```bash
   # Terminal 1: Keep kweb running
   kweb
   
   # Terminal 2: Run tests when needed
   pytest -m web -v
   ```

## Test Data Requirements

For optimal test coverage, your kospex database should contain:

- At least one synced repository (for repo-specific endpoints)
- Developer/author data (for developer endpoints)  
- Commit data (for commit-specific endpoints)
- Dependency data (for SCA endpoints)

### Setting Up Test Data

```bash
# Initialize kospex
kospex init --create

# Sync a test repository  
kgit clone https://github.com/kospex/kospex
kospex sync ~/code/github.com/kospex/kospex

# Or sync any existing repository
kospex sync /path/to/your/repo
```

## Troubleshooting

### Common Issues

1. **"kweb server not running"**
   ```bash
   # Check if server is running
   curl http://localhost:8000/health
   
   # Start server if needed
   kweb
   ```

2. **"Tests timing out"**
   - Increase `TIMEOUT` in test file
   - Check server performance
   - Verify database isn't locked

3. **"Unexpected status codes"**
   - Some endpoints return 404 when no data exists (this is expected)
   - Check that test parameters match your database content
   - Review server logs for errors

4. **"Tests failing in CI"**
   - Web tests should be skipped in CI
   - Verify `CI=true` environment variable is set
   - Check GitHub Actions workflow configuration

### Debug Mode

Run with debug output:
```bash
pytest -m web -v -s --tb=long
```

Add debug logging to test file:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Adding New Tests

When adding new endpoints to kweb2.py:

1. **Update test file**: Add endpoint to appropriate test category
2. **Add test parameters**: Include necessary test data
3. **Handle edge cases**: Account for endpoints that may return 404
4. **Test locally**: Verify tests pass with your changes

### Example: Adding a new endpoint test

```python
# Add to appropriate parametrize decorator
@pytest.mark.parametrize("endpoint", [
    # ... existing endpoints ...
    "/your-new-endpoint/",  # Add your endpoint here
])
def test_html_endpoints_main_pages(self, endpoint: str):
    # Test implementation already handles it
    pass
```

## Performance Considerations

- Tests run sequentially to avoid overwhelming the server
- Each test has a 10-second timeout by default
- Server should respond to health check within 5 seconds
- Performance test validates response times < 5 seconds

For load testing or performance benchmarks, use dedicated tools like:
- Apache Bench (ab)
- wrk
- locust

## Integration with Development Tools

### Pre-commit Hooks
Add to `.pre-commit-config.yaml`:
```yaml
- repo: local
  hooks:
    - id: web-tests
      name: Web endpoint tests
      entry: bash -c 'if pgrep -f "kweb\|run_fastapi" > /dev/null; then pytest -m web --tb=short; else echo "Skipping web tests - kweb not running"; fi'
      language: system
      pass_filenames: false
```

### IDE Integration
Most IDEs support pytest markers:
- IntelliJ/PyCharm: Run configuration with `-m web`
- VS Code: Test discovery with pytest markers
- Vim/Neovim: Test runners with marker support