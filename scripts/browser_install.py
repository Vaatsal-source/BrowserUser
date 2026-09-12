"""Install official Playwright Chromium into the app's private data directory."""

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    data = Path(
        os.environ.get("GUARD_DATA_DIR", str(Path.home() / "Library/Application Support/Dev Privacy Guard"))
    ).expanduser()
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(data / "browsers"))
    return subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"], env=env)


if __name__ == "__main__":
    raise SystemExit(main())
