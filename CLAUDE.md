# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Kospex Development Guide

## Project Overview

Kospex is a CLI tool for analyzing git repositories and code to understand developer activity, technology landscape, and code complexity. It uses SQLite database structure inspired by Mergestat lite to model git repository data.

## Architecture

### Core Components
- **kospex.py** - Main CLI entry point with Click commands
- **kospex_core.py** - Core business logic and database operations
- **kospex_git.py** - Git repository analysis and metadata extraction
- **kospex_utils.py** - Utility functions and configuration management
- **kospex_query.py** - Database query abstractions
- **kospex_schema.py** - Database schema definitions
- **kospex_dependencies.py** - Dependency analysis and SCA functionality

### Web Interface Components
- **kweb.py** - Original Flask web interface
- **kweb2.py** - Enhanced Flask web interface with FastAPI migration prep
- **kospex_web.py** - Core web functionality
- **kweb_help_service.py** - Help system for web interface
- **kweb_graph_service.py** - Graph visualization services
- **kweb_security.py** - Security utilities for web interface

### Additional Tools
- **kgit.py** - Git operations wrapper with kospex directory structure
- **krunner.py** - Batch processing and automation
- **kreaper.py** - Data deletion
- **kwatch.py** - File system monitoring
- **ksyncer.py** - Synchronization utilities
- **repo_sync.py** - Repository synchronization functionality
- **kospex_agent.py** - Background agent for continuous repository monitoring and synchronization
- **kospex_stats.py** - Statistical analysis and reporting

### Logging System
- **kospex_logging.py** - Centralized logging infrastructure with daily rotation
- **Enhanced init system** - Comprehensive environment setup and validation
- **Per-module loggers** - Dedicated log files for each CLI tool (kospex.log, kgit.log, etc.)

## Key Dependencies

### Python Backend
- **Click** - CLI framework for command-line interface
- **Flask** - Web framework for UI
- **FastAPI** - Modern web framework (migration in progress)
- **SQLite3/sqlite_utils** - Database operations
- **PyGitHub** - GitHub API integration
- **PyYAML** - Configuration file parsing
- **Jinja2** - Template engine for web interface
- **Requests** - HTTP client for API calls

### Frontend Assets
- **TailwindCSS** - Utility-first CSS framework
- **Chart.js** - Data visualization charts
- **D3.js** - Advanced data visualization
- **DataTables** - Interactive table functionality
- **jQuery** - DOM manipulation

### Development Tools
- **pytest** - Testing framework
- **scc** - Source code counter (external binary, optional but recommended)

## Directory Structure

```
~/kospex/          # Config files, SQLite database, and logs
  ├── config.json          # Optional logging and system configuration
  ├── kospex.db            # Main SQLite database
  ├── kospex.env           # Environment variable overrides
  └── logs/                # Centralized logging directory
      ├── kospex.log       # Main CLI tool logs (daily rotation)
      ├── kgit.log         # Git operations logs
      ├── kweb2.log        # Web interface logs
      ├── krunner.log      # Batch processing logs
      ├── kwatch.log       # File monitoring logs
      └── kospex_agent.log # Background agent logs

~/code/            # Git repositories in GIT_SERVER/ORG/REPO format
  github.com/
    kospex/
      kospex/
  bitbucket.org/
    org/
      repo/
```

## Common Commands

### Installation & Setup
- `pip install kospex` - Install from PyPI
- `kospex init --create --verbose` - Initialize directory structure with detailed output
- `kospex init --validate` - Validate current setup without making changes
- `pip install -e .` - Development installation from source

### Core Operations
- `kospex sync PATH/TO/REPO` - Sync repository data to database
- `kgit clone https://github.com/owner/repo` - Clone with kospex structure
- `kospex developers -repo PATH/TO/REPO` - Show active developers
- `kospex tech-landscape -metadata` - Show technology stack overview
- `kospex summary` - Overview of all synced repositories

### Logging Control (Global Options)
- `kospex --debug COMMAND` - Enable debug-level logging for detailed troubleshooting
- `kospex --verbose COMMAND` - Enable verbose output and INFO-level logging
- `kospex --quiet COMMAND` - Reduce output to errors only
- `kospex --log-console COMMAND` - Show log messages on console as well as in files

### Background Agent Operations
- `kospex-agent start` - Start continuous repository monitoring (default 5-minute interval)
- `kospex-agent start --interval 60` - Start with custom interval (60 seconds)
- `kospex-agent --debug start` - Start agent with debug logging enabled
- `kospex-agent status` - Check repository and agent status
- `kospex-agent test` - Test repository update checking functionality

### Web Interface
- `kweb` - Start Flask web interface
- `python run_fastapi.py` - Start FastAPI web interface (development)
- `python kweb2.py` - Start FastAPI web interface (default: localhost:8000)
- `python kweb2.py --host 0.0.0.0 --port 8080` - Start with custom host/port
- `python kweb2.py --debug` - Start with debug mode and auto-reload
- `python kweb2.py --help` - Show web server options

### Development & Testing
- `pytest` - Run all tests (no additional setup required)
- `pytest tests/test_kospex.py` - Run specific test file  
- `pytest -v` - Run tests with verbose output
- `pytest -k "test_name"` - Run specific test by name pattern
- `python -m kospex COMMAND --help` - Get help for specific commands
- `npm run build` - Build frontend assets (runs build-static.js + CSS)
- `npm run build-css` - Build Tailwind CSS only
- `npm run dev` - Watch mode for CSS development (equivalent to build-css-watch)

### Database Operations
- `kospex upgrade-db` - Apply database schema updates
- `kospex system-status` - Check system health
- `kospex list-repos` - List all synced repositories

## Development Workflow

### Setting Up Development Environment
1. Clone repository: `git clone https://github.com/kospex/kospex`
2. Install in dev mode: `pip install -e .`
3. Install scc binary for complexity analysis
4. Run `kospex init --create` to set up directories
5. Install frontend dependencies: `npm install`

### Code Style & Standards
- Use Python 3.11+ features
- Follow existing patterns in codebase
- Use Click for CLI commands with proper help text
- Database operations through kospex_core and kospex_query abstractions 
- Web templates use Jinja2 with TailwindCSS classes
- JavaScript uses JS with jQuery for DOM manipulation

### Testing
- Tests located in `/tests/` directory using pytest framework
- Run `pytest` from project root (no additional setup required)  
- Test modules include: `test_kospex.py`, `test_kgit.py`, `test_kospex_utils.py`, `test_kospex_schema.py`, `test_kweb_help.py`
- Use `pytest -v` for verbose output, `pytest -k "pattern"` for specific tests
- Docker-based testing available via `/tests/Dockerfile` and shell scripts

### Frontend Development
- CSS: TailwindCSS utility classes, compile with `npm run build-css`
- JavaScript: ES6+ with Chart.js, D3.js, DataTables
- Templates: Jinja2 HTML templates in `/src/templates/`
- Static assets: `/src/static/` for CSS/JS files

## Configuration

### Environment Variables
- `KOSPEX_CODE` - Base directory for git repositories (default: ~/code)
- `KOSPEX_HOME` - Config directory (default: ~/kospex)
- `GITHUB_TOKEN` - GitHub API token for enhanced rate limits and private repo queries

### Logging Environment Variables
- `KOSPEX_LOG_LEVEL` - Global logging level: DEBUG, INFO, WARNING, ERROR (default: INFO)
- `KOSPEX_LOG_RETENTION_DAYS` - Number of days to keep log files (default: 30)
- `KOSPEX_CONSOLE_LOGGING` - Enable console output: true/false (default: false)

### Key Files
- `pyproject.toml` - Python project configuration and dependencies
- `package.json` - Node.js dependencies for frontend assets
- `tailwind.config.js` - TailwindCSS configuration
- `requirements.txt` - Python dependencies (legacy, use pyproject.toml)

## Database Schema

### Core Tables
- `repos` - Repository metadata and sync status
- `commits` - Git commit history
- `authors` - Developer information extracted from git
- `files` - File metadata and complexity metrics
- `dependencies` - Software composition analysis results

### Query Patterns
- Most tables include `repo_id` in format `GIT_SERVER~OWNER~REPO`
- Author identification primarily uses `author_email` from git
- Time-based queries often filter by commit date ranges
- Technology landscape aggregates by file extensions and scc metadata, also uses panopticas

## Centralized Logging System

Kospex uses a comprehensive centralized logging system with daily rotation and per-module log files:

### Key Features
- **Daily Log Rotation** - Automatic cleanup prevents disk space issues
- **Per-Module Logs** - Dedicated files for easy troubleshooting (kospex.log, kgit.log, etc.)
- **Flexible Configuration** - Environment variables + JSON config file support
- **Runtime Control** - CLI flags override configuration as needed  
- **Robust Error Handling** - CLI remains functional even with logging issues
- **Development Friendly** - Easy debug logging during development

### Configuration Options

#### Environment Variables
- `KOSPEX_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR` - Global logging level
- `KOSPEX_LOG_RETENTION_DAYS=30` - Number of days to keep old logs
- `KOSPEX_CONSOLE_LOGGING=true|false` - Enable console output

#### JSON Configuration (`~/kospex/config.json`)
```json
{
  "logging": {
    "log_level": "DEBUG",
    "retention_days": 60,
    "console_logging": true,
    "modules": {
      "kospex": {"level": "INFO"},
      "kgit": {"level": "DEBUG"},
      "kweb2": {"level": "WARNING"},
      "krunner": {"level": "INFO"},
      "kwatch": {"level": "INFO"}
    }
  }
}
```

### Usage in Code
```python
from kospex_utils import get_kospex_logger

# Get module-specific logger
logger = get_kospex_logger('kospex')
logger.info("Starting repository analysis")
logger.debug("Found 42 repositories")  
logger.error("Failed to process repository")

# Enable console logging for interactive tools (like kospex-agent)
import os
os.environ['KOSPEX_CONSOLE_LOGGING'] = 'true'
logger = get_kospex_logger('kospex_agent')
logger.info("This will appear in both console and log file")
```

### Log File Format
```
2025-01-09 14:30:15 [    INFO] [kospex] Starting repository analysis
2025-01-09 14:30:16 [   DEBUG] [kospex] Found 42 repositories
2025-01-09 14:30:17 [   ERROR] [kgit] Failed to clone repository: permission denied
```

### Validation Commands
- `kospex init --validate` - Check logging system health
- `kospex init --verbose` - Show detailed initialization status
- Python: `KospexUtils.validate_kospex_setup()` - Programmatic validation

## FastAPI Migration

The project includes FastAPI support alongside the existing Flask web interface:
- `run_fastapi.py` - FastAPI development server with auto-reload and Docker support
- `kweb2.py` - Main FastAPI web interface with Docker-aware host binding
- Templates remain Jinja2-based for compatibility

### Development Usage
- `python run_fastapi.py` for development with hot reload
- `python run_fastapi.py --host 0.0.0.0` for Docker/container environments
- `python run_fastapi.py --no-reload` to disable auto-reload for production-like testing

### Production Usage with kweb2.py
- `python kweb2.py` - Default: localhost:8000
- `python kweb2.py --host 0.0.0.0 --port 8080` - Custom host/port
- `python kweb2.py --debug` - Debug mode with auto-reload
- Automatically detects Docker environment and binds to 0.0.0.0 when running in containers

## Troubleshooting

### Common Issues
- **scc not found**: Install scc binary for file type detection and complexity metrics
- **Database errors**: Run `kospex upgrade-db` to apply schema updates
- **Permission errors**: Ensure proper file permissions in KOSPEX_CODE directory
- **GitHub rate limits**: Use GITHUB_TOKEN environment variable for authentication

### Logging Issues
- **Logs not appearing**: Check `kospex init --validate` to verify logging setup
- **Permission denied on log directory**: Ensure `~/kospex/logs` is writable
- **Log files too large**: Adjust `KOSPEX_LOG_RETENTION_DAYS` environment variable
- **Debug info not showing**: Use `kospex --debug COMMAND` or set `KOSPEX_LOG_LEVEL=DEBUG`
- **Console logging missing**: Use `--log-console` flag or set `KOSPEX_CONSOLE_LOGGING=true`

### Development Issues
- **CSS not updating**: Run `npm run build-css` to recompile TailwindCSS
- **Tests failing**: Check test data setup and database initialization
- **Import errors**: Ensure development installation with `pip install -e .`
- **Logging not working in new code**: Use `from kospex_utils import get_kospex_logger`