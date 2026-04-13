#!/usr/bin/env python3
"""claw-cost-daemon – CLI entry point.

Usage:
    claw-cost-daemon start            One-command start: network + capture + dashboard
    claw-cost-daemon stop             Stop everything
    claw-cost-daemon run              Start mitmproxy capture (foreground)
    claw-cost-daemon dashboard         Show live TUI dashboard
    claw-cost-daemon doctor            Run diagnostic checks
    claw-cost-daemon export [format]   Export data (csv/json)
"""

from __future__ import annotations

import os
import sys
import json
import csv
import time
import socket
import platform
import subprocess
import shutil
from pathlib import Path

import click

# Ensure the package is importable when running from source
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from claw_cost_daemon import __version__
from claw_cost_daemon.storage import Storage
from claw_cost_daemon.dashboard import Dashboard
from claw_cost_daemon.network import (
    setup_network,
    teardown_network,
    ensure_ca_cert,
    trust_ca_cert,
    get_active_redirect_port,
)
from claw_cost_daemon.lifecycle import (
    start_mitmproxy, stop_mitmproxy, write_pid, is_running, GracefulShutdown,
)

DEFAULT_DB = "~/.claw-cost-daemon/events.db"
DEFAULT_PROXY_PORT = 8080
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"

# The bin directory where this script (or the entry-point wrapper) lives.
# Under `sudo` the venv is NOT on PATH, so we need to look here explicitly.
_VENV_BIN = Path(sys.executable).parent if Path(sys.executable).parent.name == "bin" else None


def _which(name: str) -> str | None:
    """Like shutil.which but also searches the venv bin directory."""
    found = shutil.which(name)
    if found:
        return found
    if _VENV_BIN:
        candidate = _VENV_BIN / name
        if candidate.is_file():
            return str(candidate)
    return None


def _real_user_context() -> tuple[str, Path, int]:
    """Resolve invoking user's identity under sudo or direct execution."""
    import pwd

    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            info = pwd.getpwnam(sudo_user)
            return sudo_user, Path(info.pw_dir), info.pw_uid
        except KeyError:
            pass

    user = os.environ.get("USER", "root")
    return user, Path.home(), os.getuid()


def _is_port_in_use(port: int) -> bool:
    """Return True when a local TCP port is already bound."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        sock.close()


def _ensure_line_in_file(path: Path, line: str) -> bool:
    """Append line to file only when missing. Returns True if changed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        content = path.read_text(encoding="utf-8")
    else:
        content = ""

    if line in content:
        return False

    with path.open("a", encoding="utf-8") as f:
        if content and not content.endswith("\n"):
            f.write("\n")
        f.write(line + "\n")
    return True


def _run_user_systemctl(user: str, uid: int, args: list[str]) -> subprocess.CompletedProcess:
    """Run a user-level systemctl command as the invoking non-root user."""
    cmd = [
        "sudo", "-u", user,
        "env",
        f"XDG_RUNTIME_DIR=/run/user/{uid}",
        f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus",
        "systemctl", "--user",
        *args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


@click.group()
@click.version_option(__version__, prog_name="claw-cost-daemon")
@click.option("--db", default=DEFAULT_DB, help="SQLite database path")
@click.pass_context
def cli(ctx: click.Context, db: str):
    """💰 AI Cost Monitor – Real-time AI API cost tracking for Linux."""
    ctx.ensure_object(dict)
    ctx.obj["db"] = db


@cli.command()
@click.option("--port", default=DEFAULT_PROXY_PORT, help="Proxy listen port")
@click.pass_context
def start(ctx: click.Context, port: int):
    """🚀 One-command start: setup everything and launch the dashboard.

    Sets up network rules, generates & trusts CA cert, starts mitmproxy
    in the background, then launches the dashboard. Ctrl+C cleans up everything.
    """
    import pwd

    if os.geteuid() != 0:
        click.echo("❌ This command requires root. Use: sudo claw-cost-daemon start")
        sys.exit(1)

    db = ctx.obj["db"]

    # Resolve real user's home (before sudo)
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            real_home = Path(pwd.getpwnam(sudo_user).pw_dir)
            real_uid = pwd.getpwnam(sudo_user).pw_uid
        except KeyError:
            real_home = Path.home()
            real_uid = os.getuid()
    else:
        real_home = Path.home()
        real_uid = os.getuid()

    click.echo()
    click.echo("💰 claw-cost-daemon — starting up...")
    click.echo("=" * 50)

    # 1. Prerequisites
    click.echo("\n📦 Step 1/5: Checking prerequisites...")
    if not _which("mitmdump"):
        click.echo("   ❌ mitmdump not found. Install with: pip install mitmproxy")
        sys.exit(1)
    click.echo("   ✅ mitmdump")

    if not shutil.which("nft") and not shutil.which("iptables"):
        click.echo("   ❌ Neither nft nor iptables found!")
        sys.exit(1)
    click.echo(f"   ✅ {'nft' if shutil.which('nft') else 'iptables'}")

    # 2. Database
    click.echo("\n🗄️  Step 2/5: Initialising database...")
    storage = Storage(db)
    storage.init()
    click.echo(f"   ✅ {Path(db).expanduser()}")

    # 3. CA certificate
    click.echo("\n🔒 Step 3/5: Setting up CA certificate...")
    try:
        cert = ensure_ca_cert(home=real_home)
        click.echo(f"   ✅ {cert}")
    except Exception as exc:
        click.echo(f"   ❌ {exc}")
        sys.exit(1)

    click.echo("   Trusting CA cert system-wide...")
    trust_msg = trust_ca_cert(cert)
    click.echo(f"   {trust_msg}")

    # 4. Network rules
    click.echo("\n🌐 Step 4/5: Setting up network redirect...")
    try:
        net_msg = setup_network(port)
        click.echo(f"   ✅ {net_msg}")
        active_port = get_active_redirect_port()
        if active_port is not None and active_port != port:
            raise RuntimeError(
                f"transparent redirect mismatch: expected :{port}, found :{active_port}"
            )
    except Exception as exc:
        click.echo(f"   ❌ {exc}")
        sys.exit(1)

    # 5. Start mitmproxy + dashboard
    click.echo("\n🚀 Step 5/5: Starting mitmproxy...")
    try:
        proc = start_mitmproxy(port=port, db_path=db, confdir=cert.parent)
    except Exception as exc:
        click.echo(f"   ❌ {exc}")
        teardown_network()
        sys.exit(1)

    write_pid(proc.pid)
    click.echo(f"   ✅ mitmproxy running (PID {proc.pid}) on port {port}")

    click.echo()
    click.echo("=" * 50)
    click.echo("✅ claw-cost-daemon is running!")
    click.echo(f"   Proxy: localhost:{port} (transparent mode)")
    click.echo(f"   DB:    {Path(db).expanduser()}")
    click.echo(f"   Logs:  {Path.home() / '.claw-cost-daemon' / 'mitmproxy.log'}")
    click.echo()
    click.echo("   Press Ctrl+C to stop — network rules will be cleaned up automatically.")
    click.echo()

    # Launch dashboard (blocks until Ctrl+C)
    dash = Dashboard(db_path=db, refresh_ms=1000)
    with GracefulShutdown(proc, teardown_network):
        try:
            dash.run()
        except KeyboardInterrupt:
            pass


@cli.command("setup")
@click.option("--port", default=None, type=int, help="Proxy listen port (auto-prompt if omitted)")
@click.option("--non-interactive", is_flag=True, help="Use defaults without prompts")
@click.pass_context
def setup_wizard(ctx: click.Context, port: int | None, non_interactive: bool):
    """🧭 Interactive Linux-only setup wizard for monitor + client integration."""
    if platform.system().lower() != "linux":
        click.echo("❌ setup is Linux-only.")
        sys.exit(1)

    if os.geteuid() != 0:
        click.echo("❌ setup requires root. Use: sudo claw-cost-daemon setup")
        sys.exit(1)

    db = ctx.obj["db"]
    real_user, real_home, real_uid = _real_user_context()

    click.echo()
    click.echo("🧭 claw-cost-daemon setup wizard")
    click.echo("=" * 50)
    click.echo(f"Detected user: {real_user} ({real_home})")

    if non_interactive:
        use_openclaw = True
        use_claw = True
    else:
        click.echo("\nWhich apps should be integrated?")
        use_openclaw = click.confirm("Use OpenClaw?", default=True)
        use_claw = click.confirm("Use claw-code?", default=True)

    selected_port = port or DEFAULT_PROXY_PORT
    if port is None and _is_port_in_use(selected_port):
        if non_interactive:
            selected_port = 8081
        else:
            click.echo(f"\n⚠️  Port {selected_port} is in use.")
            suggested = 8081
            if click.confirm(f"Use port {suggested} instead?", default=True):
                selected_port = suggested
            else:
                selected_port = click.prompt("Enter proxy port", type=int, default=suggested)

    click.echo("\n📦 Step 1/6: Preparing database...")
    storage = Storage(db)
    storage.init()
    click.echo(f"   ✅ {Path(db).expanduser()}")

    click.echo("\n🔒 Step 2/6: Preparing and trusting CA cert...")
    cert = ensure_ca_cert(home=real_home)
    click.echo(f"   ✅ {cert}")
    click.echo(f"   {trust_ca_cert(cert)}")

    click.echo("\n🌐 Step 3/6: Setting transparent redirect...")
    net_msg = setup_network(selected_port)
    click.echo(f"   ✅ {net_msg}")
    active_port = get_active_redirect_port()
    if active_port is not None and active_port != selected_port:
        raise click.ClickException(
            f"transparent redirect mismatch: expected :{selected_port}, found :{active_port}"
        )

    click.echo("\n🧩 Step 4/6: Applying client integration...")
    openclaw_needs_restart = False
    if use_openclaw:
        dropin_dir = real_home / ".config" / "systemd" / "user" / "openclaw-gateway.service.d"
        dropin_dir.mkdir(parents=True, exist_ok=True)
        dropin_file = dropin_dir / "10-claw-cost-daemon.conf"
        dropin_file.write_text(
            "[Service]\n"
            f"Environment=NODE_EXTRA_CA_CERTS={cert}\n"
            f"Environment=SSL_CERT_FILE={cert}\n"
            "Environment=NO_PROXY=localhost,127.0.0.1,::1\n"
            "Environment=no_proxy=localhost,127.0.0.1,::1\n",
            encoding="utf-8",
        )
        click.echo(f"   ✅ OpenClaw drop-in written: {dropin_file}")
        click.echo("   ✅ OpenClaw env includes CA trust for transparent mode")
        openclaw_needs_restart = True
    else:
        click.echo("   • OpenClaw integration skipped")

    if use_claw:
        bashrc = real_home / ".bashrc"
        changed = _ensure_line_in_file(
            bashrc,
            f"export NODE_EXTRA_CA_CERTS={cert}",
        )
        if changed:
            click.echo(f"   ✅ Added NODE_EXTRA_CA_CERTS to {bashrc}")
        else:
            click.echo("   ✅ claw env already configured in .bashrc")
    else:
        click.echo("   • claw integration skipped")

    click.echo("\n🚀 Step 5/6: Starting mitmproxy...")
    proc = start_mitmproxy(port=selected_port, db_path=db, confdir=cert.parent)
    write_pid(proc.pid)
    click.echo(f"   ✅ mitmproxy running (PID {proc.pid}) on port {selected_port}")

    if openclaw_needs_restart:
        click.echo("   Reloading OpenClaw gateway to pick up proxy env...")
        reload_res = _run_user_systemctl(real_user, real_uid, ["daemon-reload"])
        restart_res = _run_user_systemctl(real_user, real_uid, ["restart", "openclaw-gateway.service"])
        if reload_res.returncode == 0 and restart_res.returncode == 0:
            click.echo("   ✅ OpenClaw gateway reloaded and restarted")
        else:
            click.echo("   ⚠️  Could not restart openclaw-gateway.service automatically")
            click.echo("      Run: systemctl --user daemon-reload && systemctl --user restart openclaw-gateway.service")

    click.echo("\n📊 Step 6/6: Launching dashboard...")
    click.echo("   Press Ctrl+C to stop and clean up network rules.")
    dash = Dashboard(db_path=db, refresh_ms=1000)
    with GracefulShutdown(proc, teardown_network):
        try:
            dash.run()
        except KeyboardInterrupt:
            pass


@cli.command()
@click.pass_context
def stop(ctx: click.Context):
    """🛑 Stop everything: mitmproxy + network rules."""
    if os.geteuid() != 0:
        click.echo("❌ This command requires root. Use: sudo claw-cost-daemon stop")
        sys.exit(1)

    if is_running():
        click.echo("🛑 Stopping claw-cost-daemon...")
        stopped = stop_mitmproxy()
        if stopped:
            click.echo("   ✅ mitmproxy stopped")

    click.echo("   Removing network rules...")
    msg = teardown_network()
    click.echo(f"   ✅ {msg}")
    click.echo("\n✅ Done. Your network is back to normal.")


@cli.command()
@click.option("--port", default=DEFAULT_PROXY_PORT, help="Proxy listen port")
@click.option("--mode", default="transparent", type=click.Choice(["transparent", "regular"]))
@click.pass_context
def run(ctx: click.Context, port: int, mode: str):
    """Start the mitmproxy capture process."""
    db = ctx.obj["db"]

    if mode == "transparent" and os.geteuid() != 0:
        click.echo("⚠️  Transparent mode requires root. Use: sudo claw-cost-daemon run")
        click.echo("   Or use regular mode: claw-cost-daemon run --mode regular")
        sys.exit(1)

    mitmdump = _which("mitmdump")
    if not mitmdump:
        click.echo("❌ mitmdump not found. Install with: pip install mitmproxy")
        sys.exit(1)

    # Ensure DB is initialised
    storage = Storage(db)
    storage.init()
    click.echo(f"✅ Database ready at {Path(db).expanduser()}")

    addon_path = Path(__file__).resolve().parent / "addons" / "ai_capture.py"

    click.echo(f"🚀 Starting mitmproxy on port {port} ({mode} mode)...")
    click.echo(f"   Addon: {addon_path}")
    click.echo(f"   DB: {db}")
    click.echo("   Press Ctrl+C to stop\n")

    cmd = [
        mitmdump,
        "-s", str(addon_path),
        "--listen-port", str(port),
        "--set", "connection_strategy=lazy",
        "--set", "block_global=false",
        "--set", f"claw_cost_daemon_db_path={db}",
        "--set", f"claw_cost_daemon_proxy_port={port}",
    ]

    if mode == "transparent":
        cmd.extend(["--mode", "transparent"])

    os.execv(mitmdump, cmd)


@cli.command()
@click.option("--refresh", default=1000, help="Refresh interval in ms")
@click.option("--simple", is_flag=True, help="Use simpler non-fullscreen mode")
@click.pass_context
def dashboard(ctx: click.Context, refresh: int, simple: bool):
    """Show the live cost monitoring dashboard."""
    db = ctx.obj["db"]
    dash = Dashboard(db_path=db, refresh_ms=refresh)
    if simple:
        dash.run_simple()
    else:
        dash.run()


@cli.command()
@click.pass_context
def doctor(ctx: click.Context):
    """Run diagnostic checks."""
    db = ctx.obj["db"]
    click.echo("🔍 [claw-cost-daemon] Running diagnostics...\n")

    issues = 0

    # 1. mitmproxy
    if shutil.which("mitmdump"):
        result = subprocess.run(["mitmdump", "--version"], capture_output=True, text=True)
        version = result.stdout.strip().split("\n")[0]
        click.echo(f"  ✅ mitmproxy: {version}")
    else:
        click.echo("  ❌ mitmproxy: NOT FOUND")
        issues += 1

    # 2. Python version
    click.echo(f"  ✅ Python: {sys.version.split()[0]}")

    # 3. Root check
    if os.geteuid() == 0:
        click.echo("  ✅ Running as root")
    else:
        click.echo("  ⚠️  Not running as root (needed for transparent mode)")

    # 4. nftables / iptables
    if shutil.which("nft"):
        click.echo("  ✅ nftables: available")
    elif shutil.which("iptables"):
        click.echo("  ✅ iptables: available (fallback)")
    else:
        click.echo("  ❌ nftables/iptables: NOT FOUND")
        issues += 1

    # 5. ss or fuser (for process attribution)
    if shutil.which("ss"):
        click.echo("  ✅ ss: available (process attribution)")
    elif shutil.which("fuser"):
        click.echo("  ✅ fuser: available (process attribution)")
    else:
        click.echo("  ⚠️  ss/fuser: NOT FOUND (process attribution limited)")

    # 6. CA certificate
    cert = Path("~/.mitmproxy/mitmproxy-ca-cert.pem").expanduser()
    if cert.exists():
        click.echo(f"  ✅ mitmproxy CA cert: {cert}")
    else:
        click.echo(f"  ⚠️  mitmproxy CA cert: NOT FOUND at {cert}")
        click.echo("     Run 'setup-network' to generate, then trust it system-wide")
        issues += 1

    # 7. Database
    storage = Storage(db)
    try:
        storage.init()
        click.echo(f"  ✅ Database: {Path(db).expanduser()}")
    except Exception as exc:
        click.echo(f"  ❌ Database error: {exc}")
        issues += 1

    # 8. Rich
    try:
        from rich.console import Console
        click.echo("  ✅ rich: available")
    except ImportError:
        click.echo("  ❌ rich: NOT FOUND (pip install rich)")
        issues += 1

    click.echo()
    if issues == 0:
        click.echo("✅ All checks passed! Ready to run.")
    else:
        click.echo(f"⚠️  {issues} issue(s) found. Fix before running.")


@cli.command()
@click.argument("format", default="csv", type=click.Choice(["csv", "json"]))
@click.option("--output", "-o", default="-", help="Output file (- for stdout)")
@click.option("--since", default=None, help="Start timestamp (unix epoch) or '1h', '24h', '7d'")
@click.pass_context
def export(ctx: click.Context, format: str, output: str, since: str | None):
    """Export captured events."""
    db = ctx.obj["db"]
    storage = Storage(db)
    storage.init()

    # Parse --since
    ts_start = 0
    if since:
        if since == "1h":
            ts_start = time.time() - 3600
        elif since == "24h":
            ts_start = time.time() - 86400
        elif since == "7d":
            ts_start = time.time() - 604800
        else:
            try:
                ts_start = float(since)
            except ValueError:
                click.echo(f"❌ Invalid --since value: {since}")
                sys.exit(1)

    events = storage.recent_events(limit=10000)

    if output == "-":
        out_file = sys.stdout
    else:
        out_file = open(output, "w")

    try:
        if format == "json":
            json.dump(events, out_file, indent=2, default=str)
        elif format == "csv":
            if events:
                writer = csv.DictWriter(out_file, fieldnames=events[0].keys())
                writer.writeheader()
                writer.writerows(events)
    finally:
        if output != "-":
            out_file.close()

    click.echo(f"Exported {len(events)} events ({format})", err=True)


def main():
    cli(obj={})


if __name__ == "__main__":
    main()
