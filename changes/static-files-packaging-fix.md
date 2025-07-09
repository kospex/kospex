# Static Files Packaging Fix

## Problem Description

The `kweb` command was failing with a `RuntimeError` when running from a pip-installed version of kospex:

```
RuntimeError: Directory '/Users/username/virtualenvs/env/lib/python3.12/site-packages/static' does not exist
```

### Root Cause

The static files directory (`src/static/`) containing CSS and JavaScript assets was not being included in the Python package when built and distributed via PyPI. This occurred because:

1. The `pyproject.toml` package data configuration was incomplete
2. No `MANIFEST.in` file existed to explicitly include static files
3. The setuptools configuration wasn't properly set to include package data

### Affected Components

- **kweb2.py** - FastAPI web server that mounts static files at line 53:
  ```python
  static_dir = Path(__file__).parent / "static"
  app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
  ```
- **Static assets** - CSS files (tailwind.css, datatables.min.css) and JavaScript files (chart.min.js, d3.min.js, etc.)

### Versions Affected

- **All versions prior to v0.0.19** - Static files were missing from pip installations
- **Fixed in v0.0.19**

## Solution Implemented

### 1. Added MANIFEST.in

Created `/MANIFEST.in` to explicitly include static files:

```
include README.short.md
include LICENSE
recursive-include src/templates *.html
recursive-include src/static *
```

### 2. Updated pyproject.toml

Enhanced the setuptools configuration in `pyproject.toml`:

```toml
[tool.setuptools]
include-package-data = true

[tool.setuptools.package-data]
"*" = [
    "templates/*.html",
    "templates/**/*.html",
    "static/**/*"
]
```

### 3. Verification

Build testing confirmed that both templates and static files are now properly included in the wheel:

```bash
$ unzip -l dist/kospex-0.0.19-py3-none-any.whl | grep static
    22999  static/css/datatables.min.css
     3038  static/css/input.css
    22191  static/css/tailwind.css
   208341  static/js/chart.min.js
   279706  static/js/d3.min.js
    87278  static/js/datatables.min.js
    87533  static/js/jquery.min.js
```

## Impact

### Before Fix
- `kweb` command failed immediately on pip-installed versions
- Users had to install from source or use development installations
- Web interface was completely non-functional

### After Fix
- `kweb` command works correctly from pip installations
- Static assets (CSS/JS) are properly served
- Web interface fully functional out-of-the-box

## Testing

The fix was verified by:
1. Building a wheel package with the updated configuration
2. Confirming static files are included in the package manifest
3. Testing that the directory structure matches the expected layout in site-packages

## Customer Impact

Users who previously experienced the `RuntimeError` when running `kweb` after installing via `pip install kospex` will now be able to use the web interface without issues starting from v0.0.19.