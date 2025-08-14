# Testing GitHub Actions Locally

This guide explains how to test GitHub Actions workflows locally before pushing to GitHub.

## Primary Tool: act

[act](https://github.com/nektos/act) is the most popular tool for running GitHub Actions workflows locally using Docker.

### Installation

#### macOS
```bash
brew install act
```

#### Linux
```bash
curl https://raw.githubusercontent.com/nektos/act/master/install.sh | sudo bash
```

#### Windows (via chocolatey)
```bash
choco install act-cli
```

### Basic Usage

```bash
# Run all workflows
act

# Run specific workflow
act -W .github/workflows/python-app.yml

# Run specific job
act -j build

# Run with specific event
act push
act pull_request
```

### Testing the Kospex Python Workflow

For the `python-app.yml` workflow in this repository:

```bash
# Test the entire workflow
act -W .github/workflows/python-app.yml

# Test just the build job
act -j build

# Run with verbose output for debugging
act -v

# Use specific runner image
act -P ubuntu-latest=catthehacker/ubuntu:act-latest
```

### Common act Options

- `-v` - Verbose output
- `-n` - Dry run (don't actually run, just show what would run)
- `-l` - List available workflows
- `-j <job>` - Run specific job
- `-W <workflow>` - Run specific workflow file
- `-P <platform>=<image>` - Use specific Docker image for platform

### Handling Secrets

If your workflow uses secrets, you can provide them via:

1. Environment variables:
```bash
act -s GITHUB_TOKEN=your_token_here
```

2. `.secrets` file in your repository root:
```
GITHUB_TOKEN=your_token_here
```

3. Prompt for secrets:
```bash
act -s GITHUB_TOKEN
```

### Alternative Testing Methods

#### 1. Docker-based Manual Testing

Run the same commands from your workflow in a Docker container:

```bash
# Start Ubuntu container similar to GitHub Actions
docker run -it --rm -v $(pwd):/workspace -w /workspace ubuntu:latest

# Inside container, run workflow steps manually:
apt-get update && apt-get install -y python3 python3-pip
python3 -m pip install --upgrade pip
pip install flake8 pytest
pip install .
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
pytest -v
```

#### 2. Local Environment Testing

Create a test script that mimics your workflow:

```bash
#!/bin/bash
# test-workflow.sh

echo "Testing GitHub Actions workflow locally..."

# Install dependencies
python -m pip install --upgrade pip
pip install flake8 pytest
pip install .

# Run linting
echo "Running flake8..."
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
flake8 . --count --exit-zero --max-complexity=10 --max-line-length=127 --statistics

# Run tests
echo "Running pytest..."
pytest -v

# Run CLI tests
echo "Running CLI tests..."
kospex init -create
kospex
```

### Common act Limitations

1. **GitHub-specific features**: Some GitHub Actions features may not work perfectly locally
2. **Secrets handling**: Requires manual setup for secrets
3. **Environment differences**: Docker environment may behave differently than GitHub's runners
4. **Action versions**: Some actions may not be fully compatible with act
5. **Performance**: Can be slower than GitHub's native runners

### Troubleshooting

#### Common Issues:

1. **Docker not found**: Ensure Docker is installed and running
2. **Permission errors**: Run with appropriate permissions or use sudo
3. **Image pulling issues**: Use `-P` to specify alternative images
4. **Action compatibility**: Some actions may require specific images

#### Debugging Commands:

```bash
# Check available workflows
act -l

# Dry run to see what would execute
act -n

# Use different runner image
act -P ubuntu-latest=catthehacker/ubuntu:act-20.04

# Skip specific steps
act --skip-checks
```

### Best Practices

1. **Test incrementally**: Start with individual jobs before running full workflows
2. **Use verbose output**: Add `-v` flag for detailed debugging
3. **Check action compatibility**: Verify third-party actions work with act
4. **Clean up**: Remove Docker containers and images periodically
5. **Version control**: Keep your workflow files in version control for easy rollback

### Resources

- [act GitHub repository](https://github.com/nektos/act)
- [GitHub Actions documentation](https://docs.github.com/en/actions)
- [act runner images](https://github.com/catthehacker/docker_images)