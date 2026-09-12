"""Configuration does not read or persist credentials in the source tree."""

import os
from pathlib import Path

# Set before importing any Browser Use module.
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["BROWSER_USE_CLOUD_SYNC"] = "false"
os.environ["BROWSER_USE_LOGGING_LEVEL"] = "error"
os.environ["BROWSER_USE_SETUP_LOGGING"] = "false"

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(
    os.environ.get("GUARD_DATA_DIR", "~/Library/Application Support/Dev Privacy Guard")
).expanduser()
PORT = int(os.environ.get("GUARD_PORT", "8765"))
DEMO_PORT = int(os.environ.get("GUARD_DEMO_PORT", "8766"))
ORIGIN = f"http://127.0.0.1:{PORT}"

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_MODEL_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
