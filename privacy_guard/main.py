"""Start both local surfaces; no public binding or automatic access to user tabs."""

import asyncio
import errno
import logging
import os
import signal
import socket
import sys
from contextlib import ExitStack, contextmanager

from .config import DATA_DIR, DEMO_PORT, PORT, ROOT
from .diagnostics import configure_logging, logger


class StartupError(RuntimeError):
    """An actionable startup failure, safe to show without a traceback."""


def bind_loopback(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
        sock.setblocking(False)
        return sock
    except OSError as exc:
        sock.close()
        if exc.errno == errno.EADDRINUSE:
            raise StartupError(
                f"Port {port} is already in use. Stop the existing companion or the process using that port."
            ) from exc
        raise StartupError(f"Cannot open local port {port}: {exc.strerror}.") from exc


async def serve():
    import uvicorn
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    from .api import create_app

    os.umask(0o077)
    for name in ("browser_use", "cdp_use", "httpx", "httpcore", "bubus"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    if not all(1 <= port <= 65535 for port in (PORT, DEMO_PORT)) or PORT == DEMO_PORT:
        raise StartupError("GUARD_PORT and GUARD_DEMO_PORT must be different ports between 1 and 65535.")
    if not (ROOT / "apps/dashboard/dist/index.html").is_file():
        raise StartupError("Dashboard is not built. Run ./scripts/setup.sh first.")

    class CompanionServer(uvicorn.Server):
        @contextmanager
        def capture_signals(self):
            # One handler coordinates both servers and their lifespan cleanup.
            yield

    with ExitStack() as resources:
        # Reserve both ports before opening the vault or announcing a pairing code.
        sockets = [resources.enter_context(bind_loopback(port)) for port in (PORT, DEMO_PORT)]
        demo = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        demo.mount("/", StaticFiles(directory=ROOT / "demo", html=True), name="demo")
        servers = [
            CompanionServer(uvicorn.Config(app, access_log=False, log_level="warning"))
            for app in (create_app(), demo)
        ]

        def stop_servers(*_):
            for server in servers:
                server.should_exit = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            previous = signal.signal(sig, stop_servers)
            resources.callback(signal.signal, sig, previous)
        running = [asyncio.create_task(server.serve(sockets=[sock])) for server, sock in zip(servers, sockets)]
        try:
            # If either surface stops or fails, shut down its companion as well.
            await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop_servers()
            outcomes = await asyncio.gather(*running, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        if not all(server.started for server in servers):
            raise StartupError("A local server could not start. Check the startup error above.")


def main():
    os.umask(0o077)
    configure_logging(DATA_DIR)
    try:
        logger.info("backend.starting api_port=%s demo_port=%s", PORT, DEMO_PORT)
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    except StartupError as exc:
        logger.error("backend.startup_failed", exc_info=True)
        print(f"Dev Privacy Guard could not start: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        logger.error("backend.failed", exc_info=True)
        raise SystemExit(1) from None
    finally:
        logger.info("backend.stopped")


if __name__ == "__main__":
    main()
