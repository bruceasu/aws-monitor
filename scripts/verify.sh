#!/usr/bin/env bash
set -euo pipefail

echo "[verify] lint"
ruff check .

echo "[verify] format-check"
ruff format --check .

echo "[verify] typecheck"
mypy .

echo "[verify] tests"
pytest