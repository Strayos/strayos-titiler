#!/bin/bash
set -e

if [ ! -d "venv" ]; then
  echo "Creating virtual environment with uv..."
  uv venv
fi

echo "Upgrading build dependencies..."
uv pip install --upgrade pip build

echo "Installing package in editable mode..."
uv pip install -e .

echo "Running tests..."
uv run pytest src/titiler/core --cov=titiler.core --cov-report=xml --cov-append --cov-report=term-missing
uv run pytest src/titiler/extensions --cov=titiler.extensions --cov-report=xml --cov-append --cov-report=term-missing
uv run pytest src/titiler/xarray --cov=titiler.xarray --cov-report=xml --cov-append --cov-report=term-missing
uv run pytest src/titiler/mosaic --cov=titiler.mosaic --cov-report=xml --cov-append --cov-report=term-missing
uv run pytest src/titiler/application --cov=titiler.application --cov-report=xml --cov-append --cov-report=term-missing

echo "Building package..."
rm -rf dist
mkdir -p dist
uv build

echo "Build complete! Artifacts are in dist/"