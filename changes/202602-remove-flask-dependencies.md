# Remove Flask Dependencies

## Overview

Flask has been removed from the project dependencies as part of the migration to FastAPI. This document outlines the cleanup required to remove all Flask imports and usages from the codebase.

## Background

The project previously used Flask for the web interface (`kweb.py`) but has migrated to FastAPI (`kweb2.py`). Flask was removed from `pyproject.toml` and `requirements.txt`, but several files still contained Flask imports that needed to be cleaned up.

## Files Affected

### 1. `src/kospex_web.py`

**Before:**
```python
from flask import make_response
import csv
import base64
from io import StringIO

def download_csv(dict_data, filename=None):
    # Flask-based CSV download using make_response
    ...
```

**After:**
```python
import kospex_utils as KospexUtils

def get_id_params(request_id, request_params=None):
    # Only utility function remains
    ...
```

**Changes:**
- Removed `flask` import (`make_response`)
- Removed `csv`, `base64`, `StringIO` imports (no longer needed)
- Removed `download_csv()` function - replaced by `download_csv_fastapi()` in `kweb2.py`
- Kept `get_id_params()` function which is still used by multiple modules

### 2. `src/kweb_help_service.py`

**Before:**
```python
from flask import render_template, Response
from jinja2 import TemplateNotFound

def render_help_page(self, page_id: Optional[str]) -> Tuple[Response, int]:
    # Directly rendered templates using Flask
    return render_template('template.html'), 200
```

**After:**
```python
from typing import Optional, Tuple

def get_help_template_response(self, page_id: Optional[str]) -> Tuple[str, int]:
    # Returns template name and status code
    return "help/page.html", 200
```

**Changes:**
- Removed `flask` imports (`render_template`, `Response`)
- Removed `jinja2.TemplateNotFound` import
- Renamed `render_help_page()` to `get_help_template_response()`
- Method now returns `(template_name, status_code)` tuple instead of rendering directly
- Actual template rendering is handled by FastAPI's `Jinja2Templates` in `kweb2.py`

### 3. `tests/test_kweb_help.py`

**Before:**
```python
from flask import Flask, render_template
from unittest.mock import patch

@pytest.fixture
def app(self):
    app = Flask(__name__)
    # Flask test app setup
    ...

@patch("kweb_help_service.render_template")
def test_render_help_page_success(self, mock_render):
    # Tests that mocked Flask's render_template
    ...
```

**After:**
```python
import pytest

@pytest.fixture
def client(self):
    pytest.importorskip("httpx", reason="httpx required for FastAPI TestClient")
    from fastapi.testclient import TestClient
    from kweb2 import app
    return TestClient(app)

def test_get_help_template_response_success(self):
    template, status = self.help_service.get_help_template_response("installation")
    assert template == "help/installation.html"
    assert status == 200
```

**Changes:**
- Removed Flask imports (`Flask`, `render_template`)
- Removed `unittest.mock.patch` (no longer mocking Flask's render_template)
- Replaced Flask test app fixture with FastAPI TestClient
- Added `pytest.importorskip("httpx")` for graceful handling when httpx is not installed
- Updated unit tests to test `get_help_template_response()` method directly
- Integration tests now use FastAPI TestClient instead of Flask test client

## Implementation Notes

### Template Rendering Pattern

The key architectural change is how template rendering works:

**Flask Pattern (Old):**
```python
# In service layer
from flask import render_template
return render_template('template.html'), 200
```

**FastAPI Pattern (New):**
```python
# In service layer - just return template name
return "template.html", 200

# In route handler (kweb2.py)
template_name, status = help_service.get_help_template_response(page_id)
return templates.TemplateResponse(template_name, {"request": request})
```

This separation keeps the service layer framework-agnostic and moves the actual rendering to the route handlers.

### Test Dependencies

The FastAPI TestClient requires `httpx` as a dependency. This was added as a test optional dependency in `pyproject.toml`:

```toml
[project.optional-dependencies]
test = [
    "pytest",
    "httpx",
]
```

The test code uses `pytest.importorskip` for graceful handling when httpx is not installed:

```python
pytest.importorskip("httpx", reason="httpx required for FastAPI TestClient")
```

### GitHub Actions Workflow

Updated `.github/workflows/python-app.yml` to use the test optional dependencies:

**Before:**
```yaml
- name: Install dependencies
  run: |
    python -m pip install --upgrade pip
    pip install flake8 pytest
    pip install .
```

**After:**
```yaml
- name: Install dependencies
  run: |
    python -m pip install --upgrade pip
    pip install flake8
    pip install ".[test]"
```

This ensures pytest and httpx are installed via the package's optional dependencies, keeping test requirements centralized in `pyproject.toml`.

### Local Development

To run tests locally with all test dependencies:

```bash
pip install -e ".[test]"
pytest -v
```

## Verification

All tests pass after the changes:
```
============================== 14 passed in 0.51s ==============================
```

## Related Files (No Changes Required)

These files import `kospex_web` but only use `get_id_params`, not the removed `download_csv`:
- `src/kospex_query.py`
- `src/kweb2.py`
- `src/kweb_graph_service.py`
- `src/krunner.py`
- `src/api_routes.py`

## Summary of All Changes

| File | Change |
|------|--------|
| `src/kospex_web.py` | Removed Flask import and `download_csv()` function |
| `src/kweb_help_service.py` | Removed Flask imports, refactored to return template names |
| `tests/test_kweb_help.py` | Migrated from Flask to FastAPI TestClient |
| `pyproject.toml` | Added `[project.optional-dependencies]` with test dependencies |
| `.github/workflows/python-app.yml` | Updated to use `pip install ".[test]"` |

## Migration Complete

With these changes, Flask is fully removed from the codebase. The project now uses:
- **FastAPI** for the web framework
- **Jinja2Templates** (from FastAPI/Starlette) for template rendering
- **FastAPI TestClient** with **httpx** for integration testing
- **Optional test dependencies** in `pyproject.toml` for centralized test requirement management
