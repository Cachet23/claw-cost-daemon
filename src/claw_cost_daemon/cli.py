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
DEFAULT_PROXY_PORT = 9090  # Changed from 8080 to avoid conflicts with signal-cli
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
SAFE_PROXY_PORT_CANDIDATES = [9090, 19090, 28080, 10080]

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


def _pick_free_port(candidates: list[int], fallback: int) -> int:
    """Pick first free port from candidates, otherwise use fallback."""
    for candidate in candidates:
        if not _is_port_in_use(candidate):
            return candidate
    return fallback


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


def _upsert_managed_block(path: Path, block_name: str, body: str) -> bool:
    """Insert or replace a named managed block in a text file."""
    start_marker = f"# >>> {block_name} >>>"
    end_marker = f"# <<< {block_name} <<<"
    block = f"{start_marker}\n{body.rstrip()}\n{end_marker}\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    content = path.read_text(encoding="utf-8") if path.exists() else ""

    if start_marker in content and end_marker in content:
        start_idx = content.index(start_marker)
        end_idx = content.index(end_marker, start_idx) + len(end_marker)
        if end_idx < len(content) and content[end_idx:end_idx + 1] == "\n":
            end_idx += 1
        new_content = content[:start_idx] + block + content[end_idx:]
    else:
        new_content = content
        if new_content and not new_content.endswith("\n"):
            new_content += "\n"
        if new_content:
            new_content += "\n"
        new_content += block

    if new_content == content:
        return False

    path.write_text(new_content, encoding="utf-8")
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


def _detect_signal_channel(home: Path) -> dict[str, object]:
    """Inspect local OpenClaw config for Signal channel status."""
    cfg_path = home / ".openclaw" / "openclaw.json"
    result: dict[str, object] = {
        "detected": False,
        "enabled": False,
        "account": None,
        "endpoint": None,
    }

    if not cfg_path.exists():
        return result

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return result

    channels = data.get("channels") if isinstance(data, dict) else None
    signal = channels.get("signal") if isinstance(channels, dict) else None
    if not isinstance(signal, dict):
        return result

    result["detected"] = True
    result["enabled"] = bool(signal.get("enabled"))
    result["account"] = signal.get("account")

    if isinstance(signal.get("httpUrl"), str) and signal.get("httpUrl"):
        result["endpoint"] = signal.get("httpUrl")
    else:
        host = signal.get("httpHost") or "127.0.0.1"
        port = signal.get("httpPort") or 8080
        result["endpoint"] = f"http://{host}:{port}"

    return result


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
@click.option(
    "--network-scope",
    type=click.Choice(["openclaw", "system"]),
    default="openclaw",
    show_default=True,
    help="openclaw=proxy only OpenClaw service traffic, system=transparent redirect for all apps",
)
@click.option(
    "--daemon",
    is_flag=True,
    help="Deprecated alias. Setup already runs proxy in background and dashboard in foreground.",
)
@click.pass_context
def setup_wizard(
    ctx: click.Context,
    port: int | None,
    non_interactive: bool,
    network_scope: str,
    daemon: bool,
):
    """🧭 Interactive Linux-only setup wizard for monitor + client integration.

    Default mode uses app-level proxying for OpenClaw to avoid breaking unrelated
    TLS clients (for example signal-cli). Use --network-scope system only when you
    intentionally want machine-wide transparent interception.
    """
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

    signal_status = _detect_signal_channel(real_home)

    if non_interactive:
        use_openclaw = True
        use_claw = True
    else:
        click.echo("\nCapture scope:")
        click.echo("  - openclaw: proxy OpenClaw and configured tooling only (recommended)")
        click.echo("  - system: host-wide transparent redirect (aggressive)")
        network_scope = click.prompt(
            "Choose capture scope",
            type=click.Choice(["openclaw", "system"]),
            default=network_scope,
            show_choices=False,
        )

        if network_scope == "system":
            click.echo("\n⚠️  System mode enables host-wide TCP/443 redirect.")
            click.echo("   This captures most HTTPS traffic, but non-HTTP protocols can break.")

            if bool(signal_status["enabled"]):
                account = signal_status["account"] or "(unknown account)"
                endpoint = signal_status["endpoint"] or "http://127.0.0.1:8080"
                click.echo(
                    f"   Detected Signal channel: enabled for {account} via {endpoint}."
                )
                click.echo("   Hard redirect may disrupt signal-cli transport.")
                if not click.confirm("Keep system-wide hard redirect anyway?", default=False):
                    network_scope = "openclaw"
                    click.echo("   ✅ Switched to openclaw scope to protect Signal.")

        click.echo("\nWhich integrations should be configured automatically?")
        use_openclaw = click.confirm("Configure OpenClaw gateway integration?", default=True)
        use_claw = click.confirm("Configure claw-code shell integration?", default=True)

        click.echo("\nSetup summary:")
        click.echo(f"  - Capture scope: {network_scope}")
        click.echo(f"  - Configure OpenClaw integration: {'yes' if use_openclaw else 'no'}")
        click.echo(f"  - Configure claw-code integration: {'yes' if use_claw else 'no'}")
        if not click.confirm("Continue with this setup?", default=True):
            raise click.Abort()

    selected_port = port or DEFAULT_PROXY_PORT
    if port is None and _is_port_in_use(selected_port):
        suggested = _pick_free_port(SAFE_PROXY_PORT_CANDIDATES, 10080)
        if non_interactive:
            selected_port = suggested
        else:
            click.echo(f"\n⚠️  Port {selected_port} is in use.")
            if click.confirm(f"Use port {suggested} instead?", default=True):
                selected_port = suggested
            else:
                selected_port = click.prompt("Enter proxy port", type=int, default=suggested)

    # 8080 is commonly used by signal-cli in OpenClaw external-daemon mode.
    if selected_port == 8080:
        msg = (
            "⚠️  Port 8080 is commonly used by signal-cli (OpenClaw Signal channel). "
            "This can break Signal messaging while the cost daemon runs."
        )
        if non_interactive and port is None:
            selected_port = _pick_free_port(SAFE_PROXY_PORT_CANDIDATES, 10080)
            click.echo(f"\n{msg}")
            click.echo(f"   Using safer port {selected_port} instead.")
        elif not non_interactive:
            click.echo(f"\n{msg}")
            if click.confirm("Use a safer port automatically?", default=True):
                selected_port = _pick_free_port(SAFE_PROXY_PORT_CANDIDATES, 10080)
                click.echo(f"   ✅ Using {selected_port}")

    click.echo("\n📦 Step 1/6: Preparing database...")
    storage = Storage(db)
    storage.init()
    click.echo(f"   ✅ {Path(db).expanduser()}")

    click.echo("\n🔒 Step 2/6: Preparing and trusting CA cert...")
    cert = ensure_ca_cert(home=real_home)
    click.echo(f"   ✅ {cert}")
    click.echo(f"   {trust_ca_cert(cert)}")

    click.echo("\n🌐 Step 3/6: Configuring traffic routing...")
    transparent_enabled = network_scope == "system"
    if transparent_enabled:
        net_msg = setup_network(selected_port)
        click.echo(f"   ✅ {net_msg}")
        active_port = get_active_redirect_port()
        if active_port is not None and active_port != selected_port:
            raise click.ClickException(
                f"transparent redirect mismatch: expected :{selected_port}, found :{active_port}"
            )
    else:
        click.echo("   ✅ OpenClaw-only mode: no system-wide redirect rules")
        click.echo("      This avoids interfering with signal-cli and other non-HTTP TLS clients.")

    click.echo("\n🧩 Step 4/6: Applying client integration...")
    openclaw_needs_restart = False
    if use_openclaw:
        dropin_dir = real_home / ".config" / "systemd" / "user" / "openclaw-gateway.service.d"
        dropin_dir.mkdir(parents=True, exist_ok=True)
        dropin_file = dropin_dir / "10-claw-cost-daemon.conf"
        dropin_file.write_text(
            "[Service]\n"
            f"Environment=HTTP_PROXY=http://127.0.0.1:{selected_port}\n"
            f"Environment=HTTPS_PROXY=http://127.0.0.1:{selected_port}\n"
            f"Environment=NODE_EXTRA_CA_CERTS={cert}\n"
            f"Environment=SSL_CERT_FILE={cert}\n"
            "Environment=NO_PROXY=localhost,127.0.0.1,::1\n"
            "Environment=no_proxy=localhost,127.0.0.1,::1\n",
            encoding="utf-8",
        )
        click.echo(f"   ✅ OpenClaw drop-in written: {dropin_file}")
        click.echo(f"   ✅ OpenClaw env uses proxy http://127.0.0.1:{selected_port} + CA trust")
        openclaw_needs_restart = True
    else:
        click.echo("   • OpenClaw integration skipped")

    if use_claw:
        bashrc = real_home / ".bashrc"
        shell_lines = [
            f"export NODE_EXTRA_CA_CERTS={cert}",
            f"export SSL_CERT_FILE={cert}",
        ]
        if not transparent_enabled:
            proxy_url = f"http://127.0.0.1:{selected_port}"
            shell_lines.extend([
                f"export HTTP_PROXY={proxy_url}",
                f"export HTTPS_PROXY={proxy_url}",
                f"export http_proxy={proxy_url}",
                f"export https_proxy={proxy_url}",
                "export NO_PROXY=localhost,127.0.0.1,::1",
                "export no_proxy=localhost,127.0.0.1,::1",
            ])
        changed = _upsert_managed_block(
            bashrc,
            "claw-cost-daemon shell",
            "\n".join(shell_lines),
        )
        if changed:
            click.echo(f"   ✅ Updated shell env in {bashrc} for claw-code")
        else:
            click.echo("   ✅ claw shell env already configured in .bashrc")
        if not transparent_enabled:
            click.echo(f"   ✅ New shells will route claw-code via http://127.0.0.1:{selected_port}")
            click.echo("      Run: source ~/.bashrc  (or open a new shell) before starting claw-code")
    else:
        click.echo("   • claw integration skipped")

    click.echo("\n🚀 Step 5/6: Starting mitmproxy...")
    proc = start_mitmproxy(
        port=selected_port,
        db_path=db,
        confdir=cert.parent,
        transparent=transparent_enabled,
    )
    write_pid(proc.pid)
    mode_name = "transparent" if transparent_enabled else "regular (explicit proxy)"
    click.echo(f"   ✅ mitmproxy running (PID {proc.pid}) on port {selected_port} [{mode_name}]")

    if openclaw_needs_restart:
        click.echo("   Reloading OpenClaw gateway to pick up proxy env...")
        reload_res = _run_user_systemctl(real_user, real_uid, ["daemon-reload"])
        restart_res = _run_user_systemctl(real_user, real_uid, ["restart", "openclaw-gateway.service"])
        if reload_res.returncode == 0 and restart_res.returncode == 0:
            click.echo("   ✅ OpenClaw gateway reloaded and restarted")
        else:
            click.echo("   ⚠️  Could not restart openclaw-gateway.service automatically")
            click.echo("      Run: systemctl --user daemon-reload && systemctl --user restart openclaw-gateway.service")

    # Always launch dashboard – daemon mode just means "don't ask questions"
    click.echo("\n📊 Step 6/6: Launching dashboard...")
    click.echo("   Dashboard runs in foreground, proxy runs in background.")
    if transparent_enabled:
        click.echo("   Press Ctrl+C to stop both and clean up network rules.")
    else:
        click.echo("   Press Ctrl+C to stop both.")
    if daemon:
        click.echo("   ℹ️  --daemon is deprecated and has no effect in this version.")
    dash = Dashboard(db_path=db, refresh_ms=1000)
    cleanup_fn = teardown_network if transparent_enabled else (lambda: "No transparent rules to remove.")
    with GracefulShutdown(proc, cleanup_fn):
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
