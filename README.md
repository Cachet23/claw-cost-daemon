# 💰 claw-cost-daemon

**Real-time AI API cost monitoring for Linux**

Transparently intercepts outgoing AI API requests via mitmproxy, parses responses (including SSE streaming), calculates costs in real-time, attributes requests to local processes (PID/process name), and displays everything in a live terminal dashboard.

## 🚀 Quick Start

### One-command start (recommended)

```bash
# 1. Install
cd claw-cost-daemon
pip install -e .

# 2. Run — does everything: network rules, CA cert, mitmproxy, dashboard
sudo claw-cost-daemon start

#    Press Ctrl+C to stop — network rules clean up automatically.
```

That's it. `start` runs a 5-step setup (prerequisites → database → CA cert → network redirect → mitmproxy) and then launches the live dashboard. On exit it tears everything down.

### Interactive setup wizard

For first-time setup with guided prompts and client integration (OpenClaw, claw-code):

```bash
sudo claw-cost-daemon setup
```

The wizard will:
- Detect your user and home directory
- Ask which apps to integrate (OpenClaw, claw-code)
- Auto-detect port conflicts and suggest alternatives
- Set up transparent proxy, CA cert, and network rules
- Configure `NODE_EXTRA_CA_CERTS` for your chosen apps
- Start mitmproxy and launch the dashboard

### Manual / advanced usage

```bash
# Run diagnostics
claw-cost-daemon doctor

# Start mitmproxy capture only (foreground, transparent mode, needs root)
sudo claw-cost-daemon run

# Start mitmproxy in regular (explicit proxy) mode
claw-cost-daemon run --mode regular

# Open dashboard in a separate terminal
claw-cost-daemon dashboard
claw-cost-daemon dashboard --simple    # non-fullscreen mode

# Export data
claw-cost-daemon export csv
claw-cost-daemon export json
claw-cost-daemon export csv --since 24h

# Stop everything (mitmproxy + network rules)
sudo claw-cost-daemon stop
```

## 📋 CLI Commands

| Command | Description |
|---------|-------------|
| `sudo claw-cost-daemon start` | One-command start: network + CA cert + mitmproxy + dashboard |
| `sudo claw-cost-daemon stop` | Stop mitmproxy and remove network rules |
| `sudo claw-cost-daemon setup` | Interactive setup wizard with client integration |
| `claw-cost-daemon doctor` | Run diagnostic checks |
| `sudo claw-cost-daemon run` | Start mitmproxy capture (foreground) |
| `claw-cost-daemon dashboard` | Live TUI dashboard |
| `claw-cost-daemon dashboard --simple` | Simpler non-fullscreen mode |
| `claw-cost-daemon export csv` | Export events as CSV |
| `claw-cost-daemon export json` | Export events as JSON |
| `claw-cost-daemon export csv --since 24h` | Export last 24h of events |

## 🏗️ Architecture

```
┌──────────────────┐     nftables/iptables      ┌─────────────┐
│   Any Process    │ ──── TCP 443 redirect ──── │  mitmproxy   │
│  (claw-code,…)   │                            │  (port 8080) │
└──────────────────┘                            └──────┬──────┘
                                                      │
                                              ┌───────▼──────┐
                                              │ ai_capture   │
                                              │ addon        │
                                              └───────┬──────┘
                                                      │
                                    ┌─────────────────┼──────────────────┐
                                    ▼                 ▼                  ▼
                              ┌──────────┐    ┌─────────────┐    ┌──────────────┐
                              │ Parser   │    │ Cost Engine │    │ Process      │
                              │ (per     │    │ (pricing    │    │ Attribution  │
                              │ provider)│    │  lookup)    │    │ (PID/cmdline)│
                              └────┬─────┘    └──────┬──────┘    └──────┬───────┘
                                   │                 │                  │
                                   └─────────────────┼──────────────────┘
                                                     ▼
                                              ┌─────────────┐
                                              │   SQLite    │
                                              │  (events,   │
                                              │   pricing,  │
                                              │ dead_letter)│
                                              └──────┬──────┘
                                                     │
                                              ┌──────▼──────┐
                                              │  Dashboard  │
                                              │  (Rich TUI) │
                                              └─────────────┘
```

### Key components

| Module | Purpose |
|--------|---------|
| `addons/ai_capture.py` | mitmproxy addon — intercepts AI API traffic, parses responses, calculates costs, streams SSE capture |
| `parsers/providers.py` | Provider detection + response parsers (OpenAI, Anthropic, Google, OpenRouter + SSE streaming) |
| `cost_engine.py` | Pricing lookup & cost calculation per model |
| `storage.py` | SQLite persistence layer (events, pricing seed data, dead-letter queue) |
| `dashboard.py` | Rich-based live terminal dashboard |
| `network.py` | Transparent proxy setup in pure Python (nftables / iptables), CA cert management |
| `process.py` | Process attribution via `/proc/net/tcp` inode scanning, `ss`, and `fuser` |
| `lifecycle.py` | PID tracking, mitmproxy subprocess management, graceful shutdown |
| `cli.py` | Click CLI entry point |

## 📊 Dashboard

The dashboard shows real-time data across four panels:

```
╭────────────────────── 💰 AI COST MONITOR ──────────────────────╮
│ Session Total: $0.037500  (12 requests)                        │
│ 🌐 OpenRouter Total: $0.025000 (8 reqs)                       │
│ 🦀 OpenRouter × claw-code: $0.012500 (5 reqs)                 │
╰───────────────────────────────────────────────────────────────╯

  💰 Costs by Provider          📊 Costs by Model (Top 15)
  ─────────────────────         ────────────────────────────
  openrouter    $0.0250    8    openai/gpt-4o        $0.0150
  openai        $0.0125    4    claude-3.5-sonnet     $0.0100

  🖥️ Costs by Process           🕐 Recent Events (last 20)
  ─────────────────────         ────────────────────────────
  claw-code     $0.0125    5    3s ago  openrouter  $0.0025  200
  opencode      $0.0100    3    5s ago  openai      $0.0015  200
```

## 🔧 Supported Providers

| Provider | Domain | Parsed Fields |
|----------|--------|---------------|
| **OpenRouter** | `openrouter.ai` | model, prompt/completion tokens, native cost |
| **OpenAI** | `api.openai.com` | model, prompt/completion tokens |
| **Anthropic** | `api.anthropic.com` | model, input/output tokens, cache tokens |
| **Google** | `generativelanguage.googleapis.com` | modelVersion, prompt/candidates tokens |

All providers support **SSE streaming** responses. For OpenAI/OpenRouter, the addon automatically injects `stream_options.include_usage = true` to ensure token counts are reported in stream final chunks.

## 💵 Pricing

Pricing for 30+ popular models is seeded into the database on first run. Unknown models use conservative defaults ($3.00/$15.00 per 1M input/output tokens).

Pricing can be updated programmatically:

```python
from claw_cost_daemon.cost_engine import CostEngine
engine = CostEngine("~/.claw-cost-daemon/events.db")
engine.update_pricing("openrouter", "new-model", input_price=1.0, output_price=3.0)
```

## 🦀 Process Attribution

Every captured request is attributed to the local process that made it, using a three-strategy approach:

1. **`ss`** — fastest, most reliable
2. **`fuser`** — fallback
3. **`/proc/net/tcp` inode scan** — slowest but most universal

The dashboard specifically highlights:
- **OpenRouter Total** — all OpenRouter costs
- **OpenRouter × claw-code** — only OpenRouter requests from claw-code/OpenClaw processes

## 🔧 Network Setup

Transparent proxy rules are managed in pure Python (`network.py`) — no shell scripts needed at runtime.

- **nftables** is preferred (modern Linux)
- **iptables** is used as fallback
- **QUIC (UDP 443)** is blocked to force TLS downgrade through the proxy
- Rules are **idempotent** — safe to run setup/teardown repeatedly
- All rules are cleaned up automatically on `Ctrl+C` or `claw-cost-daemon stop`

### Client integration

The `setup` wizard can automatically configure client apps:

- **OpenClaw** — writes a systemd drop-in to set `NODE_EXTRA_CA_CERTS` and restarts the gateway service
- **claw-code** — adds `NODE_EXTRA_CA_CERTS` to `~/.bashrc`

## 🔒 Privacy & Security

- **No prompt/response body storage** by default
- Only metadata (provider, model, tokens, costs, PID, process name) is persisted
- TLS interception requires trusting the mitmproxy CA certificate (handled automatically by `start`/`setup`)
- CA cert permissions are fixed automatically when running under `sudo`
- Apps with **certificate pinning** will fail — this is a known limitation
- The dead-letter queue captures unparseable events for debugging

## ⚠️ Troubleshooting

### Run diagnostics
```bash
claw-cost-daemon doctor
```

### mitmproxy CA not trusted
```bash
sudo cp ~/.mitmproxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/claw-cost-daemon-mitmproxy.crt
sudo update-ca-certificates
# Or on Arch: sudo trust anchor ~/.mitmproxy/mitmproxy-ca-cert.pem
```

### Certificate pinning errors
Some apps (Chrome, Firefox) pin certificates and will show SSL errors. This is expected with MITM. The captured traffic from apps that don't pin (curl, Python requests, claw-code) will still work.

### No events showing
1. Verify network rules are active: `sudo nft list tables` or `sudo iptables -t nat -L`
2. Check mitmproxy is running: `ps aux | grep mitmdump`
3. Verify CA cert is trusted by the target app
4. Check dead_letter table for parse errors: `sqlite3 ~/.claw-cost-daemon/events.db "SELECT * FROM dead_letter"`

### Test with explicit proxy mode
If transparent mode isn't working, test with explicit proxy:
```bash
claw-cost-daemon run --mode regular
# In another terminal:
export https_proxy=http://127.0.0.1:8080
curl https://api.openai.com/v1/models
```

### Reset everything
```bash
sudo claw-cost-daemon stop
rm -rf ~/.claw-cost-daemon/events.db
```

## 📁 Project Structure

```
claw-cost-daemon/
├── config/
│   └── default.toml          # Default configuration
├── scripts/
│   ├── setup-network.sh      # Legacy (replaced by network.py)
│   └── teardown-network.sh   # Legacy (replaced by network.py)
├── src/claw_cost_daemon/
│   ├── __init__.py
│   ├── cli.py                # Click CLI entry point
│   ├── storage.py            # SQLite persistence + pricing seed
│   ├── cost_engine.py        # Cost calculation engine
│   ├── process.py            # PID/process attribution (ss/fuser/proc)
│   ├── network.py            # Transparent proxy + CA cert (pure Python)
│   ├── lifecycle.py          # PID tracking, graceful shutdown
│   ├── dashboard.py          # Rich-based TUI dashboard
│   ├── addons/
│   │   └── ai_capture.py     # mitmproxy addon (core capture + SSE)
│   └── parsers/
│       └── providers.py      # Provider detection + response parsing
├── tests/
│   ├── test_parsers.py
│   └── test_storage_cost.py
├── pyproject.toml
└── requirements.txt
```

## License

MIT
