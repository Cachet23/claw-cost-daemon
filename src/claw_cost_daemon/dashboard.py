"""Live terminal dashboard using Rich for real-time cost monitoring."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.layout import Layout
from rich.columns import Columns

from claw_cost_daemon.storage import Storage


def _fmt_cost(cost: float) -> str:
    """Format cost with appropriate precision."""
    if cost == 0:
        return "$0.00"
    elif cost < 0.01:
        return f"${cost:.6f}"
    elif cost < 1:
        return f"${cost:.4f}"
    else:
        return f"${cost:.2f}"


def _fmt_tokens(n: int) -> str:
    """Format token counts with K/M suffixes."""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def _ts_ago(ts: float) -> str:
    """Human-readable 'time ago' string."""
    delta = time.time() - ts
    if delta < 60:
        return f"{delta:.0f}s ago"
    elif delta < 3600:
        return f"{delta/60:.0f}m ago"
    else:
        return f"{delta/3600:.1f}h ago"


class Dashboard:
    """Rich-based live dashboard for AI API cost monitoring."""

    def __init__(
        self,
        db_path: str = "~/.claw-cost-daemon/events.db",
        refresh_ms: int = 1000,
    ):
        self.db_path = Path(db_path).expanduser()
        self.refresh_ms = refresh_ms
        self.console = Console()
        self.storage = Storage(self.db_path)
        self.storage.init()
        self._session_start = time.time()

    def _build_header(self) -> Panel:
        summary = self.storage.session_summary(self._session_start)
        total = summary["total_cost"]
        reqs = summary["total_requests"]
        or_total = summary.get("openrouter_total", {})
        or_claw = summary.get("openrouter_clawcode", {})

        lines = [
            Text.assemble(
                ("💰 AI COST MONITOR", "bold cyan"),
                ("  │  ", "dim"),
                (f"Session Total: ", "bold"),
                (_fmt_cost(total), "bold green" if total > 0 else "dim"),
                (f"  ({reqs} requests)", "dim"),
            ),
            Text.assemble(
                ("🌐 OpenRouter Total: ", "bold"),
                (_fmt_cost(or_total.get("total", 0)), "yellow"),
                (f" ({or_total.get('req_count', 0)} reqs)   ", "dim"),
                ("🦀 OpenRouter × claw-code: ", "bold"),
                (_fmt_cost(or_claw.get("total", 0)), "bold magenta"),
                (f" ({or_claw.get('req_count', 0)} reqs)", "dim"),
            ),
        ]
        return Panel(
            Text("\n").join(lines),
            title="[bold]claw-cost-daemon[/bold]",
            border_style="cyan",
            padding=(0, 1),
        )

    def _build_provider_table(self) -> Table:
        table = Table(
            title="💰 Costs by Provider",
            title_style="bold",
            border_style="blue",
            show_lines=False,
        )
        table.add_column("Provider", style="bold")
        table.add_column("Cost", justify="right", style="green")
        table.add_column("Requests", justify="right")

        rows = self.storage.costs_by_provider(self._session_start)
        for row in rows:
            table.add_row(
                row["provider"],
                _fmt_cost(row["total"]),
                str(row["req_count"]),
            )
        if not rows:
            table.add_row("[dim]No data yet[/dim]", "", "")
        return table

    def _build_model_table(self) -> Table:
        table = Table(
            title="📊 Costs by Model (Top 15)",
            title_style="bold",
            border_style="green",
            show_lines=False,
        )
        table.add_column("Model", style="bold", max_width=40)
        table.add_column("Provider", style="dim", max_width=12)
        table.add_column("Cost", justify="right", style="green")
        table.add_column("In", justify="right", style="cyan")
        table.add_column("Out", justify="right", style="magenta")
        table.add_column("Reqs", justify="right")

        rows = self.storage.costs_by_model(self._session_start, limit=15)
        for row in rows:
            table.add_row(
                row["model"],
                row.get("provider", ""),
                _fmt_cost(row["total"]),
                _fmt_tokens(row.get("inp", 0)),
                _fmt_tokens(row.get("out", 0)),
                str(row["req_count"]),
            )
        if not rows:
            table.add_row("[dim]No data yet[/dim]", "", "", "", "", "")
        return table

    def _build_process_table(self) -> Table:
        table = Table(
            title="🖥️  Costs by Process",
            title_style="bold",
            border_style="yellow",
            show_lines=False,
        )
        table.add_column("Process", style="bold", max_width=50)
        table.add_column("Cost", justify="right", style="green")
        table.add_column("Requests", justify="right")

        rows = self.storage.costs_by_process(self._session_start, limit=10)
        for row in rows:
            proc = row["process_name"] or "unknown"
            table.add_row(
                proc,
                _fmt_cost(row["total"]),
                str(row["req_count"]),
            )
        if not rows:
            table.add_row("[dim]No data yet[/dim]", "", "")
        return table

    def _build_recent_table(self) -> Table:
        table = Table(
            title="🕐 Recent Events (last 20)",
            title_style="bold",
            border_style="red",
            show_lines=False,
        )
        table.add_column("When", style="dim", max_width=10)
        table.add_column("Provider", style="bold", max_width=12)
        table.add_column("Model", max_width=35)
        table.add_column("Cost", justify="right", style="green")
        table.add_column("Status", justify="center")
        table.add_column("Process", max_width=20, style="dim")
        table.add_column("Latency", justify="right", style="dim")

        events = self.storage.recent_events(limit=20)
        for ev in events:
            ts_str = _ts_ago(ev["ts"])
            status = str(ev.get("status_code", ""))
            status_style = "green" if status.startswith("2") else "red"
            latency = f"{ev.get('latency_ms', 0):.0f}ms" if ev.get("latency_ms") else ""
            table.add_row(
                ts_str,
                ev.get("provider", ""),
                ev.get("model", ""),
                _fmt_cost(ev.get("total_cost_usd", 0)),
                Text(status, style=status_style),
                ev.get("process_name", "")[:20],
                latency,
            )
        if not events:
            table.add_row("[dim]Waiting for events...[/dim]", "", "", "", "", "", "")
        return table

    def _build_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=5),
            Layout(name="tables"),
        )
        layout["tables"].split_row(
            Layout(name="left"),
            Layout(name="right"),
        )
        layout["left"].split_column(
            Layout(name="provider", ratio=1),
            Layout(name="process", ratio=1),
        )
        layout["right"].split_column(
            Layout(name="model", ratio=1),
            Layout(name="recent", ratio=2),
        )

        layout["header"].update(self._build_header())
        layout["provider"].update(self._build_provider_table())
        layout["process"].update(self._build_process_table())
        layout["model"].update(self._build_model_table())
        layout["recent"].update(self._build_recent_table())
        return layout

    def run(self):
        """Start the live dashboard."""
        self.console.print(
            "\n[bold cyan]🚀 AI Cost Monitor Dashboard[/bold cyan]\n"
            "[dim]Press Ctrl+C to exit[/dim]\n"
        )
        try:
            with Live(
                self._build_layout(),
                console=self.console,
                refresh_per_second=1000 // self.refresh_ms,
                screen=True,
            ) as live:
                while True:
                    live.update(self._build_layout())
                    time.sleep(self.refresh_ms / 1000)
        except KeyboardInterrupt:
            self.console.print("\n[dim]Dashboard stopped.[/dim]")

    def run_simple(self):
        """Simpler non-fullscreen version using table printing."""
        self.console.print(
            "\n[bold cyan]🚀 AI Cost Monitor[/bold cyan]  "
            "[dim]Press Ctrl+C to exit[/dim]\n"
        )
        try:
            while True:
                self.console.clear()
                self.console.print(self._build_header())
                self.console.print()
                self.console.print(self._build_provider_table())
                self.console.print()
                self.console.print(self._build_model_table())
                self.console.print()
                self.console.print(self._build_process_table())
                self.console.print()
                self.console.print(self._build_recent_table())
                time.sleep(self.refresh_ms / 1000)
        except KeyboardInterrupt:
            self.console.print("\n[dim]Dashboard stopped.[/dim]")
