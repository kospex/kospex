#!/bin/bash

# Pre-commit checks for Kospex
# Runs ruff, flake8, and pytest

echo "Running pre-commit checks..."
echo ""

# Run ruff
echo "→ Running ruff..."
ruff check .
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Ruff check failed"
    exit 1
fi
echo "✓ Ruff passed"
echo ""

# Run flake8
echo "→ Running flake8..."
flake8 .
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Flake8 check failed"
    exit 1
fi
echo "✓ Flake8 passed"
echo ""

# Run pytest
echo "→ Running pytest..."
pytest
if [ $? -ne 0 ]; then
    echo ""
    echo "❌ Tests failed"
    exit 1
fi
echo "✓ Tests passed"
echo ""

echo "✅ All pre-commit checks passed!"
