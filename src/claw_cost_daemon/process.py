"""Map network connections to local processes (PID, name, cmdline)."""

from __future__ import annotations

import os
import re
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROC_NET_TCP = Path("/proc/net/tcp")
PROC_NET_TCP6 = Path("/proc/net/tcp6")


@dataclass
class ProcessInfo:
    pid: int = 0
    process_name: str = ""
    cmdline: str = ""
    confidence: str = "none"  # none, low, medium, high


def _parse_proc_net_tcp() -> list[dict]:
    """
    Parse /proc/net/tcp and /proc/net/tcp6 to get:
        local_addr, local_port, inode
    """
    entries = []
    for proc_path in (PROC_NET_TCP, PROC_NET_TCP6):
        if not proc_path.exists():
            continue
        try:
            text = proc_path.read_text()
        except PermissionError:
            logger.warning("Cannot read %s – try running as root", proc_path)
            continue
        for line in text.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) < 10:
                continue
            local = parts[1]
            addr, port = local.rsplit(":", 1)
            inode = parts[9]
            if inode == "0":
                continue
            entries.append({
                "local_addr": int(addr, 16),
                "local_port": int(port, 16),
                "inode": int(inode),
            })
    return entries


def _find_pid_by_inode(inode: int) -> Optional[int]:
    """Search /proc/*/fd/ for a socket inode match."""
    if inode == 0:
        return None
    try:
        for pid_dir in Path("/proc").iterdir():
            if not pid_dir.name.isdigit():
                continue
            fd_dir = pid_dir / "fd"
            if not fd_dir.is_dir():
                continue
            try:
                for fd_link in fd_dir.iterdir():
                    try:
                        target = os.readlink(str(fd_link))
                    except (OSError, PermissionError):
                        continue
                    if f"socket:[{inode}]" in target:
                        return int(pid_dir.name)
            except PermissionError:
                continue
    except Exception:
        pass
    return None


def _get_process_info(pid: int, max_cmdline: int = 200) -> ProcessInfo:
    """Read /proc/<pid>/comm and /proc/<pid>/cmdline."""
    info = ProcessInfo(pid=pid)
    try:
        proc_path = Path(f"/proc/{pid}")
        info.process_name = (proc_path / "comm").read_text().strip()
        try:
            raw = (proc_path / "cmdline").read_text()
            cmdline = raw.replace("\x00", " ").strip()
            info.cmdline = cmdline[:max_cmdline]
        except (PermissionError, FileNotFoundError):
            pass

        # Confidence scoring
        if info.process_name and info.cmdline:
            info.confidence = "high"
        elif info.process_name:
            info.confidence = "medium"
        elif pid > 0:
            info.confidence = "low"
    except (PermissionError, FileNotFoundError):
        info.confidence = "low"
    return info


def _find_pid_by_ss(local_port: int) -> Optional[int]:
    """Use `ss` to find the PID owning a local TCP port (faster fallback)."""
    try:
        result = subprocess.run(
            ["ss", "-tlnp", f'sport = :{local_port}'],
            capture_output=True, text=True, timeout=2,
        )
        # ss output format: ... users:(("name",pid=123,fd=3))
        match = re.search(r'pid=(\d+)', result.stdout)
        if match:
            return int(match.group(1))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _find_pid_by_fuser(local_port: int) -> Optional[int]:
    """Fallback: use fuser to find PID."""
    try:
        result = subprocess.run(
            ["fuser", str(local_port) + "/tcp"],
            capture_output=True, text=True, timeout=2,
        )
        match = re.search(r'(\d+)', result.stdout)
        if match:
            return int(match.group(1))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def attribute_connection(
    client_port: int,
    server_host: str,
    server_port: int,
    max_cmdline: int = 200,
) -> ProcessInfo:
    """
    Given the client-side TCP port of an outgoing connection,
    try to find the local process that initiated it.
    """
    # Strategy 1: ss (fastest, most reliable when available)
    pid = _find_pid_by_ss(client_port)

    # Strategy 2: fuser fallback
    if pid is None:
        pid = _find_pid_by_fuser(client_port)

    # Strategy 3: /proc/net/tcp inode scan (slowest)
    if pid is None:
        for entry in _parse_proc_net_tcp():
            if entry["local_port"] == client_port:
                pid = _find_pid_by_inode(entry["inode"])
                if pid:
                    break

    if pid:
        return _get_process_info(pid, max_cmdline)

    return ProcessInfo(confidence="none")
