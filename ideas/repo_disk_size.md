# Repository Disk Size Analysis

## Overview

This document describes the approach for analyzing disk usage of git repositories using the `get_repo_size` method in `kospex_git.py`.

## Purpose

Understanding disk usage patterns in git repositories helps with:
- Repository maintenance and cleanup
- Storage planning and optimization
- Identifying repositories with large git histories vs large working files
- Monitoring disk space consumption across development environments

## Implementation

### Method: `KospexGit.get_repo_size(directory=None)`

A static method that analyzes disk usage for git repositories by measuring:

1. **Total Size**: Complete directory size including all files and git metadata
2. **Git Size**: Size of the `.git` directory (history, objects, refs, etc.)
3. **Workspace Size**: Working directory size (total - git size)

### Technical Approach

The implementation uses the Unix `du -sk` (disk usage, summarize in kilobytes) command to get accurate byte counts:

```python
# Get total directory size
subprocess.run(['du', '-sk', str(directory)])

# Get .git directory size
subprocess.run(['du', '-sk', git_dir])
```

### Data Structure

Returns a dictionary with three keys:

```python
{
    "total": int,      # Total repository size in bytes
    "git": int,        # .git directory size in bytes
    "workspace": int   # Workspace size (total - git) in bytes
}
```

### Validation

- Verifies the directory is a valid git repository using `is_git_repo()`
- Raises `ValueError` if not a git repository
- Raises `subprocess.CalledProcessError` if `du` command fails
- Uses 300-second timeout to prevent hanging on large repositories

## Usage Examples

### Basic Usage
```python
from kospex_git import KospexGit

# Analyze current directory
usage = KospexGit.get_repo_size()

# Analyze specific repository
usage = KospexGit.get_repo_size("/path/to/repo")

print(f"Total: {usage['total']} bytes")
print(f"Git metadata: {usage['git']} bytes")
print(f"Working files: {usage['workspace']} bytes")
```

### Error Handling
```python
try:
    usage = KospexGit.get_repo_size("/not/a/git/repo")
except ValueError as e:
    print(f"Error: {e}")
```

## Use Cases

### Repository Analysis
- Identify repositories with large git histories (high git/total ratio)
- Find repositories with large working files (high workspace/total ratio)
- Monitor growth patterns over time

### Storage Management
- Plan storage requirements for git hosting
- Identify candidates for git history cleanup
- Optimize backup strategies based on git vs workspace content

### Development Environment Optimization
- Clean up local development environments
- Identify repositories consuming excessive disk space
- Balance between local storage and remote fetching

## Future Enhancements

### Potential Extensions
1. **Detailed Breakdown**: Analyze git subdirectories (objects, refs, logs)
2. **History Analysis**: Correlate size with commit history depth
3. **File Type Analysis**: Break down workspace usage by file types
4. **Comparison Tools**: Compare usage across multiple repositories
5. **Tracking**: Store historical usage data for trend analysis

### Integration Opportunities
1. **Database Storage**: Store usage metrics in kospex database
2. **Web Interface**: Display usage charts in kospex web UI
3. **CLI Commands**: Add `kospex disk-usage` command, although already in krunner
4. **Monitoring**: Alert on repositories exceeding thresholds
5. **Cleanup Automation**: Suggest cleanup actions based on usage patterns

## Performance Considerations

- `du` command performance scales with directory size and file count
- 300-second timeout prevents indefinite blocking
- Consider caching results for frequently analyzed repositories
- May be I/O intensive on repositories with many small files

## Platform Compatibility

- Requires Unix-like system with `du` command
- Tested on macOS and Linux environments
- May need adaptation for Windows environments
