# Kospex Agent

A continuous repository monitoring and synchronization tool for the Kospex platform.

## Overview

The Kospex Agent is a CLI tool that runs continuously in the background, periodically checking repositories for updates and automatically synchronizing them to the Kospex database. It provides both scheduled automatic syncing and on-demand manual sync triggering.

## Installation

The agent is installed as part of the Kospex package:

```bash
pip install -e .
```

This creates the `kospex-agent` command that can be used from anywhere in your terminal.

## Basic Usage

### Start the Agent

```bash
# Start with default 5-minute interval
kospex-agent start

# Start with custom interval (60 seconds)
kospex-agent start --interval 60

# Start with verbose logging
kospex-agent start --verbose

# Combine options
kospex-agent start --interval 120 --verbose
```

### Other Commands

```bash
# Check repository status
kospex-agent status

# Test update checking functionality
kospex-agent test

# Show help
kospex-agent --help
kospex-agent start --help
```

## Key Features

### 1. Continuous Monitoring
- Runs in a loop, periodically checking repositories for updates
- Configurable check intervals (default: 5 minutes)
- Robust error handling that continues operation despite individual failures

### 2. Manual Sync Triggering
- **Press Enter at any time** to trigger an immediate sync check
- No need to wait for the next scheduled interval
- Interrupts current sleep period and runs sync immediately
- Continues normal scheduled operation after manual sync

### 3. Graceful Shutdown
- Handles `SIGINT` (Ctrl+C) and `SIGTERM` signals for clean shutdowns
- Stops background threads properly
- Logs shutdown process

### 4. Comprehensive Logging
- Configurable verbosity with `--verbose` flag
- Logs to both console and `kospex-agent.log` file
- Clear messages for all operations including Enter key detection

### 5. Interactive Experience
- Real-time feedback when Enter key is pressed
- Clear status messages during operation
- Responsive to user input without blocking main functionality

## Command Reference

### `kospex-agent start`

Starts the continuous monitoring agent.

**Options:**
- `-i, --interval INTEGER`: Check interval in seconds (default: 300)
- `-v, --verbose`: Enable verbose/debug logging
- `--help`: Show command help

**Interactive Controls:**
- **Enter**: Trigger immediate sync check
- **Ctrl+C**: Stop the agent gracefully

**Examples:**
```bash
# Default 5-minute interval
kospex-agent start

# Check every minute
kospex-agent start --interval 60

# Check every 30 minutes with verbose logging
kospex-agent start -i 1800 -v
```

### `kospex-agent status`

Shows the current status of repositories in the Kospex database.

*Note: This is currently a stub implementation ready for future development.*

### `kospex-agent test`

Tests the update checking functionality without starting the continuous agent.

## Technical Implementation

### Architecture

- **Main Thread**: Runs the primary sync checking loop
- **Background Thread**: Monitors for Enter key presses (daemon thread)
- **Signal Handlers**: Manage graceful shutdown on SIGINT/SIGTERM
- **Thread Coordination**: Uses simple flag-based communication between threads

### Update Checking (Stub Methods)

The following methods are implemented as stubs, ready for you to add your specific logic:

1. **`_check_repos_need_update()`**: Main method to check which repositories need updating
2. **`_repo_needs_update(repo_info)`**: Check if a specific repository needs updating  
3. **`_update_repositories(repos)`**: Update the specified repositories

### Extensibility

You can implement the stub methods to add functionality like:
- Checking remote Git repositories for new commits
- Comparing last sync timestamps with repository modification times
- Monitoring filesystem changes
- Integrating with Git hosting APIs (GitHub, GitLab, Bitbucket)
- Calling existing `kospex.sync_repo()` methods for actual updates

## Integration with Kospex

The agent integrates seamlessly with the existing Kospex ecosystem:
- Uses `kospex_core.Kospex` for core functionality
- Leverages `kospex_query.KospexQuery` for database operations
- Follows the same patterns and conventions as other Kospex CLI tools
- Shares the same SQLite database and configuration

## Summary of Updates

### New Features Added

1. **Enter Key Detection**: Added a background thread that monitors for Enter key presses
2. **Manual Sync Trigger**: When Enter is pressed, the agent immediately triggers a sync check without waiting for the interval
3. **Responsive Sleep**: The sleep function now checks for manual sync requests and can be interrupted
4. **Improved User Experience**: Clear messaging about the Enter key functionality

### Technical Implementation

1. **New Imports**: Added `select` and `threading` for non-blocking input monitoring
2. **New Instance Variable**: `self.manual_sync_requested` to coordinate between threads
3. **Background Thread**: `_monitor_input()` method runs in a daemon thread to watch for Enter key presses
4. **Enhanced Main Loop**: Updated `_run_loop()` to check for manual sync requests
5. **Responsive Sleep**: Modified `_interruptible_sleep()` to wake up on manual sync requests

### User Interface Updates

1. **Startup Message**: Now shows "Press Ctrl+C to stop the agent or Enter to trigger immediate sync"
2. **Sleep Message**: Shows "(press Enter for immediate sync)" during sleep periods
3. **Manual Sync Logging**: Clear log messages when Enter key is detected and manual sync is triggered
4. **Updated Help Text**: The `start` command help now mentions the Enter key functionality

### Key Behavior

- **Non-blocking**: The input monitoring doesn't interfere with the main agent loop
- **Thread-safe**: Uses a simple flag-based coordination between threads
- **Graceful Fallback**: If input monitoring fails (non-interactive mode), it continues normally
- **Immediate Response**: When Enter is pressed, the current sleep is interrupted and sync runs immediately
- **Continuous Operation**: After a manual sync, the agent continues with its normal scheduled intervals

## Development and Testing

### Development Mode

For development, install the package in editable mode:

```bash
pip install -e .
```

### Running Tests

```bash
# Test the update checking logic
kospex-agent test

# Quick functionality test
timeout 10s kospex-agent start --interval 5 --verbose
```

### Log Files

The agent creates a `kospex-agent.log` file in the current working directory with detailed operation logs.

## Future Enhancements

The agent is designed to be easily extensible. Potential future enhancements include:

1. **Web Interface Integration**: Connect with the Kospex web interface for status monitoring
2. **Configuration Files**: Support for configuration files to define repositories and sync rules
3. **Notification System**: Email/Slack notifications for sync results
4. **Metrics and Monitoring**: Detailed metrics on sync performance and repository health
5. **Plugin System**: Extensible plugin architecture for custom sync logic
6. **Distributed Operation**: Support for running multiple agents across different machines

## Troubleshooting

### Common Issues

1. **Input monitoring disabled**: If running in non-interactive mode, Enter key detection may not work. This is expected and the agent will continue to function normally.

2. **Permission errors**: Ensure the agent has read/write access to the Kospex database and log files.

3. **Signal handling**: On some systems, signal handling may behave differently. The agent includes robust signal handling for common scenarios.

### Getting Help

```bash
# Show general help
kospex-agent --help

# Show command-specific help
kospex-agent start --help
kospex-agent status --help
kospex-agent test --help
```

## Contributing

When extending the agent, please:

1. Follow the existing code patterns and conventions
2. Add appropriate logging for new functionality
3. Update this documentation for any new features
4. Ensure thread safety for any new background operations
5. Test both interactive and non-interactive modes
