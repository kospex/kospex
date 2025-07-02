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
- **kospex_agent.py** - Agent (stub) to sync repos in the background and generate summaries

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
~/kospex/          # Config files and SQLite database
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
- `kospex init --create` - Initialize default directory structure
- `pip install -e .` - Development installation from source

### Core Operations
- `kospex sync PATH/TO/REPO` - Sync repository data to database
- `kgit clone https://github.com/owner/repo` - Clone with kospex structure
- `kospex developers -repo PATH/TO/REPO` - Show active developers
- `kospex tech-landscape -metadata` - Show technology stack overview
- `kospex summary` - Overview of all synced repositories

### Web Interface
- `kweb` - Start Flask web interface
- `python run_fastapi.py` - Start FastAPI web interface (development)

### Development & Testing
- `pytest` - Run test suite
- `python -m kospex COMMAND --help` - Get help for specific commands
- `npm run build` - Build frontend assets
- `npm run build-css` - Build Tailwind CSS
- `npm run dev` - Watch mode for CSS development

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
- Tests located in `/tests/` directory
- Use pytest for Python testing
- Test data in `/tests/data/` directory
- Docker-based testing available via `/tests/Dockerfile`

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

## FastAPI Migration

The project is migrating from Flask to FastAPI:
- `FASTAPI_MIGRATION.md` - Migration progress and notes
- `run_fastapi.py` - FastAPI development server
- `kweb2.py` - Hybrid Flask/FastAPI implementation
- Templates remain Jinja2-based for compatibility

## Troubleshooting

### Common Issues
- **scc not found**: Install scc binary for file type detection and complexity metrics
- **Database errors**: Run `kospex upgrade-db` to apply schema updates
- **Permission errors**: Ensure proper file permissions in KOSPEX_CODE directory
- **GitHub rate limits**: Use GITHUB_TOKEN environment variable for authentication

### Development Issues
- **CSS not updating**: Run `npm run build-css` to recompile TailwindCSS
- **Tests failing**: Check test data setup and database initialization
- **Import errors**: Ensure development installation with `pip install -e .`