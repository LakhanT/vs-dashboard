#!/usr/bin/env python3
"""
VS Dashboard — single launcher for backend + frontend.

Usage:
    python run.py
    python run.py --setup-only    # install deps, no servers
    python run.py --no-browser    # skip opening browser
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV_PYTHON = BACKEND / ".venv" / "Scripts" / "python.exe"
VENV_PIP = BACKEND / ".venv" / "Scripts" / "pip.exe"
ENV_FILE = BACKEND / ".env"
ENV_EXAMPLE = BACKEND / ".env.example"

BACKEND_URL = "http://127.0.0.1:8000/api/health"
FRONTEND_URL = "http://127.0.0.1:5173"


def log(msg: str) -> None:
    print(f"[VS Dashboard] {msg}")


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    log(f"Running: {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd or ROOT, check=check)


def ensure_backend_venv() -> None:
    if VENV_PYTHON.exists():
        return
    log("Creating backend virtual environment...")
    run([sys.executable, "-m", "venv", str(BACKEND / ".venv")])


def ensure_backend_deps() -> None:
    ensure_backend_venv()
    log("Installing backend dependencies...")
    run([str(VENV_PIP), "install", "-r", "requirements.txt"], cwd=BACKEND)


def ensure_frontend_deps() -> None:
    if not (FRONTEND / "node_modules").exists():
        log("Installing frontend dependencies...")
        npm = shutil.which("npm")
        if not npm:
            raise RuntimeError("npm not found. Install Node.js from https://nodejs.org/")
        run([npm, "install"], cwd=FRONTEND)


def ensure_env_file() -> None:
    if ENV_FILE.exists():
        return
    if ENV_EXAMPLE.exists():
        shutil.copy(ENV_EXAMPLE, ENV_FILE)
        log(f"Created {ENV_FILE.name} from .env.example")
    else:
        ENV_FILE.write_text(
            "DATABASE_URL=sqlite:///./vs_dashboard.db\n"
            "CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173\n",
            encoding="utf-8",
        )
        log(f"Created default {ENV_FILE.name}")


def setup() -> None:
    log("Running first-time setup...")
    ensure_env_file()
    ensure_backend_deps()
    ensure_frontend_deps()
    log("Setup complete.")


def wait_for_url(url: str, timeout: int = 60) -> bool:
    try:
        import urllib.request

        for _ in range(timeout):
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if resp.status == 200:
                        return True
            except OSError:
                pass
            time.sleep(1)
    except Exception:
        return False
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Run VS Dashboard (backend + frontend)")
    parser.add_argument("--setup-only", action="store_true", help="Install dependencies and exit")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically")
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-port", type=int, default=5173)
    args = parser.parse_args()

    os.chdir(ROOT)

    try:
        setup()
    except Exception as exc:
        log(f"Setup failed: {exc}")
        return 1

    if args.setup_only:
        return 0

    ensure_backend_venv()
    npm = shutil.which("npm")
    if not npm:
        log("ERROR: npm not found. Install Node.js first.")
        return 1

    if not VENV_PYTHON.exists():
        log("ERROR: Backend venv missing. Run: python run.py --setup-only")
        return 1

    backend_cmd = [
        str(VENV_PYTHON),
        "-m",
        "uvicorn",
        "app.main:app",
        "--reload",
        "--port",
        str(args.backend_port),
    ]
    frontend_cmd = [npm, "run", "dev", "--", "--port", str(args.frontend_port), "--host", "127.0.0.1"]

    log("Starting backend on port %s..." % args.backend_port)
    backend_proc = subprocess.Popen(
        backend_cmd,
        cwd=BACKEND,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )

    log("Starting frontend on port %s..." % args.frontend_port)
    frontend_proc = subprocess.Popen(
        frontend_cmd,
        cwd=FRONTEND,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )

    processes = [backend_proc, frontend_proc]

    def shutdown(*_: object) -> None:
        log("Shutting down...")
        for proc in processes:
            if proc.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, shutdown)

    log("Waiting for servers...")
    backend_ok = wait_for_url(f"http://127.0.0.1:{args.backend_port}/api/health")
    frontend_ok = wait_for_url(f"http://127.0.0.1:{args.frontend_port}")

    if backend_ok:
        log(f"Backend ready: http://127.0.0.1:{args.backend_port}/api/health")
    else:
        log("WARNING: Backend did not respond in time.")

    if frontend_ok:
        log(f"Frontend ready: http://127.0.0.1:{args.frontend_port}")
    else:
        log("WARNING: Frontend did not respond in time.")

    if not args.no_browser and frontend_ok:
        webbrowser.open(f"http://127.0.0.1:{args.frontend_port}")

    log("")
    log("VS Dashboard is running.")
    log(f"  Dashboard: http://127.0.0.1:{args.frontend_port}")
    log(f"  API docs:  http://127.0.0.1:{args.backend_port}/docs")
    log("Press Ctrl+C to stop.")
    log("")

    try:
        while True:
            if backend_proc.poll() is not None:
                log("Backend exited unexpectedly.")
                shutdown()
            if frontend_proc.poll() is not None:
                log("Frontend exited unexpectedly.")
                shutdown()
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
