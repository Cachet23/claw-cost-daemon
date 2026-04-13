"""Network rule management – nftables / iptables in pure Python.

Replaces the shell scripts setup-network.sh and teardown-network.sh
with a cleaner, testable, idempotent Python implementation.
"""

from __future__ import annotations

import logging
import os
import pwd
import subprocess
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

TABLE_NAME = "claw-cost-daemon"
TABLE_FILTER_NAME = "claw-cost-daemon-filter"
MITM_UID = 0  # run as root so we skip mitmproxy's own UID

# The bin directory where the Python executable lives (venv).
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


def _run(cmd: list[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command, optionally checking return code."""
    logger.debug("run: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=capture,
        check=False,  # we handle errors ourselves
    )


def _has_nft() -> bool:
    return shutil.which("nft") is not None


def _has_iptables() -> bool:
    return shutil.which("iptables") is not None


def _fix_cert_permissions(cert_dir: Path) -> None:
    """Ensure generated mitmproxy cert files are owned by the real user.

    When `claw-cost-daemon start` runs via sudo, mitmdump may generate the certs
    under the caller's home directory but as root-owned files. That makes later
    non-root runs fail when mitmproxy needs the private CA key.
    """
    if os.geteuid() != 0 or "SUDO_USER" not in os.environ:
        return

    try:
        user_info = pwd.getpwnam(os.environ["SUDO_USER"])
    except KeyError:
        return

    try:
        os.chown(cert_dir, user_info.pw_uid, user_info.pw_gid)
    except FileNotFoundError:
        return

    for path in cert_dir.glob("mitmproxy*"):
        try:
            os.chown(path, user_info.pw_uid, user_info.pw_gid)
            if path.name.endswith(("-ca.pem", "-ca.p12")):
                path.chmod(0o600)
            else:
                path.chmod(0o644)
        except FileNotFoundError:
            continue


def ensure_ca_cert(home: Path | None = None) -> Path:
    """Generate mitmproxy CA cert if missing. Returns cert path."""
    if home is None:
        import os
        if os.geteuid() == 0 and "SUDO_USER" in os.environ:
            import pwd
            home = Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir)
        else:
            home = Path.home()

    cert_dir = home / ".mitmproxy"
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert = cert_dir / "mitmproxy-ca-cert.pem"

    if cert.exists():
        _fix_cert_permissions(cert_dir)
        logger.info("CA cert already exists: %s", cert)
        return cert

    # Generate certs by briefly starting mitmdump
    logger.info("Generating mitmproxy CA certificate...")
    mitmdump = _which("mitmdump")
    if not mitmdump:
        raise RuntimeError("mitmdump not found – install with: pip install mitmproxy")

    r = _run([mitmdump, "--version"])
    if r.returncode != 0:
        raise RuntimeError("mitmdump not found – install with: pip install mitmproxy")

    # Brief start to generate certs — use --confdir so certs land in our chosen dir
    _run(["timeout", "2", mitmdump, "--listen-port", "18080",
          "--set", "connection_strategy=lazy",
          "--set", f"confdir={cert_dir}"], check=False)

    if not cert.exists():
        raise RuntimeError(f"Failed to generate CA cert at {cert}")

    _fix_cert_permissions(cert_dir)

    logger.info("CA cert generated: %s", cert)
    return cert


def trust_ca_cert(cert: Path) -> str | None:
    """Install the CA cert system-wide. Returns a human-readable status message."""
    import os

    # Try Debian/Ubuntu first
    ca_dest = Path("/usr/local/share/ca-certificates/claw-cost-daemon-mitmproxy.crt")
    try:
        shutil.copy2(cert, ca_dest)
        r = _run(["update-ca-certificates"])
        if r.returncode == 0:
            return f"CA cert trusted via update-ca-certificates ({ca_dest})"
    except Exception:
        pass

    # Try trust command (Arch / Fedora)
    r = _run(["trust", "anchor", str(cert)])
    if r.returncode == 0:
        return f"CA cert trusted via trust anchor ({cert})"

    # Try cacertdir_rehash (RHEL/CentOS)
    ca_dir = Path("/etc/pki/ca-trust/source/anchors/")
    if ca_dir.is_dir():
        try:
            shutil.copy2(cert, ca_dir / "claw-cost-daemon-mitmproxy.pem")
            r = _run(["update-ca-trust", "extract"])
            if r.returncode == 0:
                return f"CA cert trusted via update-ca-trust ({ca_dir})"
        except Exception:
            pass

    # Manual fallback
    return (
        f"⚠️  Could not auto-trust CA cert. Please do it manually:\n"
        f"     sudo cp {cert} /usr/local/share/ca-certificates/ && sudo update-ca-certificates\n"
        f"     (or on Arch: sudo trust anchor {cert})"
    )


def setup_network(port: int = 8080) -> str:
    """Set up transparent proxy redirect rules. Idempotent.

    Returns a status message.
    """
    # Remove any stale rules first (idempotent)
    teardown_network()

    if _has_nft():
        return _setup_nft(port)
    elif _has_iptables():
        return _setup_iptables(port)
    else:
        raise RuntimeError("Neither nft nor iptables found – cannot set up network redirect")


def get_active_redirect_port() -> int | None:
    """Return the currently configured nftables redirect port, if present."""
    if not _has_nft():
        return None

    result = _run(["nft", "list", "table", "inet", TABLE_NAME])
    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        if "redirect to :" not in line:
            continue
        try:
            return int(line.rsplit(":", 1)[1].strip())
        except ValueError:
            return None
    return None


def teardown_network() -> str:
    """Remove all network redirect rules. Safe to call multiple times."""
    removed = []

    if _has_nft():
        for table in (TABLE_NAME, TABLE_FILTER_NAME):
            r = _run(["nft", "list", "tables"])
            if table in r.stdout:
                _run(["nft", "delete", "table", "inet", table])
                removed.append(f"nftables inet {table}")

    if _has_iptables():
        cmds = [
            ["iptables", "-t", "nat", "-D", "OUTPUT", "-j", "AI_COST_MONITOR"],
            ["iptables", "-t", "nat", "-F", "AI_COST_MONITOR"],
            ["iptables", "-t", "nat", "-X", "AI_COST_MONITOR"],
            ["iptables", "-D", "OUTPUT", "-p", "udp", "--dport", "443",
             "-j", "REJECT", "--reject-with", "icmp-port-unreachable"],
        ]
        for cmd in cmds:
            r = _run(cmd)
            if r.returncode == 0:
                removed.append("iptables " + " ".join(cmd))

        # ip6tables too
        cmds6 = [
            ["ip6tables", "-t", "nat", "-D", "OUTPUT", "-j", "AI_COST_MONITOR"],
            ["ip6tables", "-t", "nat", "-F", "AI_COST_MONITOR"],
            ["ip6tables", "-t", "nat", "-X", "AI_COST_MONITOR"],
            ["ip6tables", "-D", "OUTPUT", "-p", "udp", "--dport", "443", "-j", "REJECT"],
        ]
        for cmd in cmds6:
            _run(cmd)

    if removed:
        return "Removed: " + ", ".join(removed)
    return "No rules to remove."


def _setup_nft(port: int) -> str:
    """Set up nftables rules."""
    messages = []

    # nat table for redirect
    _run(["nft", "add", "table", "inet", TABLE_NAME])
    _run(["nft", "flush", "chain", "inet", TABLE_NAME, "output"])
    _run(["nft", "add", "chain", "inet", TABLE_NAME,
          "output", "{", "type", "nat", "hook", "output", "priority", "-100", ";", "}"])

    _run(["nft", "add", "rule", "inet", TABLE_NAME, "output",
          "tcp", "dport", "443",
          "meta", "skuid", "!=", str(MITM_UID),
          "redirect", "to", f":{port}"])
    messages.append(f"nftables: TCP 443 → :{port}")

    # filter table for QUIC block
    _run(["nft", "add", "table", "inet", TABLE_FILTER_NAME])
    _run(["nft", "flush", "chain", "inet", TABLE_FILTER_NAME, "output"])
    _run(["nft", "add", "chain", "inet", TABLE_FILTER_NAME,
          "output", "{", "type", "filter", "hook", "output", "priority", "-100", ";", "}"])
    _run(["nft", "add", "rule", "inet", TABLE_FILTER_NAME, "output",
          "udp", "dport", "443", "reject"])
    messages.append("nftables: QUIC (UDP 443) blocked")

    return " | ".join(messages)


def _setup_iptables(port: int) -> str:
    """Set up iptables rules (fallback)."""
    # Create chain
    _run(["iptables", "-t", "nat", "-N", "AI_COST_MONITOR"])

    # Redirect HTTPS
    _run(["iptables", "-t", "nat", "-A", "AI_COST_MONITOR",
          "-p", "tcp", "--dport", "443",
          "-m", "owner", "!", "--uid-owner", str(MITM_UID),
          "-j", "REDIRECT", "--to-port", str(port)])

    # Add to OUTPUT chain
    _run(["iptables", "-t", "nat", "-A", "OUTPUT", "-j", "AI_COST_MONITOR"])

    # Block QUIC
    _run(["iptables", "-A", "OUTPUT", "-p", "udp", "--dport", "443",
          "-j", "REJECT", "--reject-with", "icmp-port-unreachable"])

    return f"iptables: TCP 443 → :{port}, QUIC blocked"
