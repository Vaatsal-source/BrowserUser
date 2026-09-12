#!/usr/bin/env bash
set -euo pipefail
GUARD_PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$GUARD_PROJECT_DIR"
for tool in uv node npm tesseract; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing $tool. On macOS with Homebrew: brew install uv node tesseract"
    echo "See setup.md for prerequisites."
    exit 1
  fi
done
uv sync --frozen
npm --prefix apps/dashboard ci
npm --prefix apps/dashboard run build
uv run scripts/browser_install.py
echo "Setup complete. Run ./scripts/start.sh and pair the dashboard with the terminal code."
