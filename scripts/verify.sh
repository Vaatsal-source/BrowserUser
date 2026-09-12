#!/usr/bin/env bash
set -euo pipefail
GUARD_PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$GUARD_PROJECT_DIR"
uv run --frozen pytest -q
uv run --frozen ruff check privacy_guard tests scripts
npm --prefix apps/dashboard run build
uv run --frozen python scripts/smoke_test.py --product-demo
uv run --frozen python scripts/smoke_test.py --product-demo --no-submit
