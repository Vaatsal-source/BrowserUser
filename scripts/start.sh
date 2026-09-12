#!/usr/bin/env bash
set -euo pipefail
GUARD_PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$GUARD_PROJECT_DIR"
if [ ! -x .venv/bin/python ]; then
  echo "Project Python environment is missing. Run ./scripts/setup.sh first."
  exit 1
fi
if [ ! -f apps/dashboard/dist/index.html ]; then
  echo "Dashboard has not been built. Run ./scripts/setup.sh first."
  exit 1
fi
exec .venv/bin/python -m privacy_guard.main
