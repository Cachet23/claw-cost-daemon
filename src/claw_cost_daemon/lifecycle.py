"""Lifecycle management – PID files, process tracking, graceful shutdown.

Manages the mitmproxy subprocess so that claw-cost-daemon can orchestrate
start/stop/restart with a single CLI command.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
import logging
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

STATE_DIR = Path.home() / ".claw-cost-daemon"
PID_FILE = STATE_DIR / "mitmproxy.pid"
LOG_FILE = STATE_DIR / "mitmproxy.log"


def _ensure_state_dir():
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def write_pid(pid: int):
    _ensure_state_dir()
    PID_FILE.write_text(str(pid) + "\n")


def read_pid() -> int | None:
    try:
        text = PID_FILE.read_text().strip()
        if text:
            return int(text)
    except (FileNotFoundError, ValueError):
        pass
    return None


def remove_pid():
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


def is_running() -> bool:
    """Check if the managed mitmproxy process is alive."""
    pid = read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)  # signal 0 = check existence
        return True
    except (ProcessLookupError, PermissionError):
        remove_pid()
        return False


def stop_mitmproxy() -> bool:
    """Stop the managed mitmproxy process. Returns True if it was running."""
    pid = read_pid()
    if pid is None:
        return False

    try:
        os.kill(pid, signal.SIGTERM)
        # Wait up to 5s for graceful shutdown
        for _ in range(50):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        else:
            # Force kill
            logger.warning("mitmproxy didn't shut down gracefully, killing")
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.5)
    except (ProcessLookupError, PermissionError):
        pass

    remove_pid()
    return True


def start_mitmproxy(
    port: int = 8080,
    db_path: str = "~/.claw-cost-daemon/events.db",
    addon_path: Path | None = None,
    confdir: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.Popen:
    """Start mitmproxy as a background subprocess.

    Returns the Popen object. Caller should use write_pid() to track it.
    """
    import shutil

    mitmdump = shutil.which("mitmdump")
    if not mitmdump:
        # Fallback: check the venv bin directory (under sudo, PATH may not include it)
        _venv_bin = Path(__import__("sys").executable).parent
        if _venv_bin.name == "bin":
            candidate = _venv_bin / "mitmdump"
            if candidate.is_file():
                mitmdump = str(candidate)
    if not mitmdump:
        raise RuntimeError("mitmdump not found – install with: pip install mitmproxy")

    if addon_path is None:
        addon_path = Path(__file__).resolve().parent / "addons" / "ai_capture.py"

    _ensure_state_dir()

    cmd = [
        mitmdump,
        "-s", str(addon_path),
        "--listen-port", str(port),
        "--set", "connection_strategy=lazy",
        "--set", "block_global=false",
        "--set", f"claw_cost_daemon_db_path={db_path}",
        "--set", f"claw_cost_daemon_proxy_port={port}",
        "--mode", "transparent",
    ]

    if confdir:
        cmd.extend(["--set", f"confdir={confdir}"])

    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)

    logger.info("Starting mitmproxy: %s", " ".join(cmd))
    with LOG_FILE.open("ab") as log_handle:
        log_handle.write(("\n=== claw-cost-daemon mitmproxy start ===\n").encode())
        log_handle.write(("CMD: " + " ".join(cmd) + "\n").encode())

    log_handle = LOG_FILE.open("ab", buffering=0)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            preexec_fn=os.setpgrp,  # own process group so we can kill it cleanly
        )
    except Exception:
        log_handle.close()
        raise

    # Give it a moment to fail fast if something's wrong.
    # mitmproxy can crash shortly after binding, for example when loading certs.
    for _ in range(20):
        time.sleep(0.1)
        if proc.poll() is not None:
            break

    if proc.poll() is not None:
        log_handle.close()
        stderr = ""
        try:
            stderr = LOG_FILE.read_text(errors="replace")
        except FileNotFoundError:
            pass
        raise RuntimeError(
            f"mitmproxy exited immediately with code {proc.returncode}: {stderr[-2000:]}"
        )

    log_handle.close()

    return proc


class GracefulShutdown:
    """Context manager for handling Ctrl+C / SIGTERM during `claw-cost-daemon start`.

    On exit (signal or context end), it:
    1. Tears down network rules
    2. Kills the mitmproxy subprocess
    3. Cleans up PID file
    """

    def __init__(self, mitmproxy_proc: subprocess.Popen, cleanup_network: Callable):
        self.proc = mitmproxy_proc
        self.cleanup_network = cleanup_network
        self._shutting_down = False
        self._original_handlers = {}

    def install_handlers(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            self._original_handlers[sig] = signal.signal(sig, self._handler)

    def _handler(self, signum, frame):
        if self._shutting_down:
            # Double Ctrl+C → force exit
            logger.warning("Force exit requested")
            os._exit(1)
        self._shutting_down = True
        signal.signal(signum, signal.SIG_DFL)  # restore default for force-quit
        self._cleanup()

    def _cleanup(self):
        import click

        click.echo("\n\n🛑 Shutting down...")

        # 1. Kill mitmproxy
        if self.proc.poll() is None:
            click.echo("   Stopping mitmproxy...")
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        remove_pid()
        click.echo("   ✅ mitmproxy stopped")

        # 2. Tear down network
        click.echo("   Removing network rules...")
        try:
            msg = self.cleanup_network()
            click.echo(f"   ✅ {msg}")
        except Exception as exc:
            click.echo(f"   ⚠️  Network cleanup error: {exc}")

        click.echo("\n✅ All done. Your network is back to normal.")

    def __enter__(self):
        self.install_handlers()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._shutting_down:
            self._cleanup()
        return False
