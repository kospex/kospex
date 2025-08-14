# Python Package Migration Plan (Enhanced)

This document outlines the plan to migrate the `src` directory to a proper Python package structure. The goal is to improve maintainability, scalability, and prepare the project for future development.

## Current Structure

The project currently has a flat structure in the `src/` directory, with multiple Python modules residing at the top level. The command-line interfaces (CLIs) are defined in `pyproject.toml` and point directly to these modules.

## Proposed Structure

The proposed structure will be a standard Python package:

```
src/
└── kospex/
    ├── __init__.py
    ├── api_routes.py
    ├── agent.py
    ├── bitbucket.py
    ├── cli.py
    ├── core.py
    ├── dependencies.py
    ├── git.py
    ├── github.py
    ├── mergestat.py
    ├── query.py
    ├── schema.py
    ├── utils.py
    ├── web.py
    ├── reaper.py
    ├── runner_utils.py
    ├── runner.py
    ├── syncer.py
    ├── watch.py
    ├── web_graph_service.py
    ├── web_help_service.py
    ├── web_security.py
    ├── web_legacy.py
    ├── web_main.py
    └── git_cli.py
```

## Migration Strategy

An "atomic" structural change is recommended over a file-by-file migration to avoid inconsistent import paths and potential errors.

---

### **Phase 1: Filesystem Restructuring**

Execute the following shell commands from the project root to restructure the files.

**1. Create the package directory:**
```bash
mkdir -p src/kospex
```

**2. Create `__init__.py`:**
```bash
touch src/kospex/__init__.py
```

**3. Move and Rename Files:**

This table maps the original filenames to their new names within the `src/kospex/` directory.

| Original File (`src/`)      | New File (`src/kospex/`)        | Notes                               |
| --------------------------- | ------------------------------- | ----------------------------------- |
| `kospex.py`                 | `cli.py`                        | Main CLI entry point                |
| `kgit.py`                   | `git_cli.py`                    | Secondary CLI for git operations    |
| `kospex_agent.py`           | `agent.py`                      |                                     |
| `kospex_bitbucket.py`       | `bitbucket.py`                  |                                     |
| `kospex_core.py`            | `core.py`                       |                                     |
| `kospex_dependencies.py`    | `dependencies.py`               |                                     |
| `kospex_git.py`             | `git.py`                        |                                     |
| `kospex_github.py`          | `github.py`                     |                                     |
| `kospex_mergestat.py`       | `mergestat.py`                  |                                     |
| `kospex_query.py`           | `query.py`                      |                                     |
| `kospex_schema.py`          | `schema.py`                     |                                     |
| `kospex_utils.py`           | `utils.py`                      |                                     |
| `kospex_web.py`             | `web.py`                        |                                     |
| `kreaper.py`                | `reaper.py`                     |                                     |
| `krunner.py`                | `runner.py`                     |                                     |
| `krunner_utils.py`          | `runner_utils.py`               |                                     |
| `ksyncer.py`                | `syncer.py`                     |                                     |
| `kwatch.py`                 | `watch.py`                      |                                     |
| `kweb.py`                   | `web_legacy.py`                 | Old web interface                   |
| `kweb2.py`                  | `web_main.py`                   | Main web interface (FastAPI)        |
| `kweb_graph_service.py`     | `web_graph_service.py`          |                                     |
| `kweb_help_service.py`      | `web_help_service.py`           |                                     |
| `kweb_security.py`          | `web_security.py`               |                                     |
| `api_routes.py`             | `api_routes.py`                 |                                     |

**Shell `mv` commands:**
```bash
mv src/kospex.py src/kospex/cli.py
mv src/kgit.py src/kospex/git_cli.py
mv src/kospex_agent.py src/kospex/agent.py
mv src/kospex_bitbucket.py src/kospex/bitbucket.py
mv src/kospex_core.py src/kospex/core.py
mv src/kospex_dependencies.py src/kospex/dependencies.py
mv src/kospex_git.py src/kospex/git.py
mv src/kospex_github.py src/kospex/github.py
mv src/kospex_mergestat.py src/kospex/mergestat.py
mv src/kospex_query.py src/kospex/query.py
mv src/kospex_schema.py src/kospex/schema.py
mv src/kospex_utils.py src/kospex/utils.py
mv src/kospex_web.py src/kospex/web.py
mv src/kreaper.py src/kospex/reaper.py
mv src/krunner.py src/kospex/runner.py
mv src/krunner_utils.py src/kospex/runner_utils.py
mv src/ksyncer.py src/kospex/syncer.py
mv src/kwatch.py src/kospex/watch.py
mv src/kweb.py src/kospex/web_legacy.py
mv src/kweb2.py src/kospex/web_main.py
mv src/kweb_graph_service.py src/kospex/web_graph_service.py
mv src/kweb_help_service.py src/kospex/web_help_service.py
mv src/kweb_security.py src/kospex/web_security.py
mv src/api_routes.py src/kospex/api_routes.py
```

---

### **Phase 2: Update Project Configuration (`pyproject.toml`)**

**1. Add Package Discovery:**

Add this section to `pyproject.toml` to ensure setuptools finds the new package.

```toml
[tool.setuptools.packages.find]
where = ["src"]
```

**2. Update Script Entry Points:**

The `[project.scripts]` section needs to be updated to point to the new module paths.

**Before:**
```toml
[project.scripts]
kgit = "kgit:cli"
kospex = "kospex:cli"
kospex-agent = "kospex_agent:cli"
krunner = "krunner:cli"
kreaper = "kreaper:cli"
kweb = "kweb2:main"
kwatch = "kwatch:cli"
```

**After:**
```toml
[project.scripts]
kgit = "kospex.git_cli:cli"
kospex = "kospex.cli:cli"
kospex-agent = "kospex.agent:cli"
krunner = "kospex.runner:cli"
kreaper = "kospex.reaper:cli"
kweb = "kospex.web_main:main"
kwatch = "kospex.watch:cli"
```

---

### **Phase 3: Code Refactoring (Example)**

All `import` statements within the moved files must be updated.

**Example: `src/kospex/cli.py` (formerly `src/kospex.py`)**

A `diff` of the import section would look something like this:

```diff
- import kospex_core
- import kospex_query
- import kospex_git
- import krunner
- from kospex_utils import execute_sql, format_as_table, get_kospex_db_path, KospexUtils
+ from . import core as kospex_core
+ from . import query as kospex_query
+ from . import git as kospex_git
+ from . import runner as krunner
+ from .utils import execute_sql, format_as_table, get_kospex_db_path, KospexUtils
```
*Note: The exact changes will vary per file. The general rule is to replace `import module_name` with `from . import new_name` or `from kospex import new_name`.*

---

### **Phase 4: Verification & Rollback**

**1. Verification:**

After completing the migration, run the project's tests and linters. Based on the file list, the following command seems appropriate:

```bash
# You may need to reinstall the package in editable mode first
pip install -e .

# Run the tests (assuming pytest is used)
pytest

# Or run the shell script if that is the primary test runner
./test-cli.sh
```

**2. Rollback Plan:**

If the migration fails or tests do not pass, you can revert all changes using `git`:

```bash
# This will discard all uncommitted changes in the working directory
git reset --hard HEAD
```