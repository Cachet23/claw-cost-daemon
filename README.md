# 💰 claw-cost-daemon

**Real-time AI API cost monitoring for Linux**

Transparently intercepts AI API requests, calculates costs in real-time, attributes them to local processes, and displays everything in a live terminal dashboard.

---

## 🚀 Quick Start

### Installation (after `git clone`)

```bash
# 1. Clone the repo
git clone https://github.com/Cachet23/claw-cost-daemon.git
cd claw-cost-daemon

# 2. Create a virtual environment
python3 -m venv .venv

# 3. Activate the venv
source .venv/bin/activate

# 4. Install the package in editable mode
pip install -e .
```

### Starting

There are three useful operating modes. The key difference is:
- `setup --network-scope openclaw` = recommended for OpenClaw + claw-code + Signal
- `setup --network-scope system-ai-only` = transparent redirect only for known AI provider targets
- `run --mode regular` = local proxy only, no automatic app integration
- system-wide transparent redirect = captures almost everything, but can interfere with local daemons such as `signal-cli`

### Recommended: OpenClaw-Scoped Setup

This is the right mode for a normal workstation running the OpenClaw gateway, `claw-code`, and Signal.

```bash
sudo $(pwd)/.venv/bin/claw-cost-daemon setup --network-scope openclaw --non-interactive
```

Interactive setup is also available (recommended for first-time configuration):

```bash
sudo $(pwd)/.venv/bin/claw-cost-daemon setup
```

In interactive mode, setup now asks you to choose the capture scope first (`openclaw`, `system-ai-only`, or `system`) and shows a risk warning before enabling host-wide hard redirect.
If Signal is detected as enabled in your OpenClaw config, setup warns you and offers to switch back to `openclaw` scope automatically.

What this does:
- Starts `mitmproxy` on `127.0.0.1:9090`
- Configures OpenClaw automatically via a systemd drop-in
- Configures `claw-code` automatically via shell environment variables in `~/.bashrc`
- Leaves `signal-cli` alone on `127.0.0.1:8080`
- Does not enable a system-wide redirect

Important:
- The OpenClaw gateway is reloaded immediately after setup
- For `claw-code` in your current shell, you need to run `source ~/.bashrc` once or open a new terminal
- In transparent scopes (`system-ai-only` / `system`), setup keeps OpenClaw `HTTP(S)_PROXY` unset (CA trust only) to avoid permanent breakage when the daemon is not running

### Regular Mode

This is only a local proxy. It is useful for testing or when you want to point specific apps at the proxy yourself.

```bash
.venv/bin/claw-cost-daemon run --mode regular
```

Important:
- This mode does not automatically capture all provider requests
- It only captures traffic from processes that explicitly use `http://localhost:9090` as their proxy
- If you only start `run --mode regular`, but OpenClaw and `claw-code` are not configured to use it, their traffic will not be visible

### System AI-Only Transparent Mode

This mode enables transparent interception while limiting redirects to resolved AI provider destinations.

```bash
sudo $(pwd)/.venv/bin/claw-cost-daemon setup --network-scope system-ai-only
```

What this does:
- Installs nftables/iptables redirect rules only for known AI provider destination IPs
- Blocks QUIC only for those provider destination targets
- Runs in transparent mode with less collateral impact than full system mode
- Keeps OpenClaw `HTTP(S)_PROXY` unset (CA trust only), so OpenClaw is not permanently tied to a local proxy listener

Tradeoff:
- Coverage depends on DNS/IP resolution at setup time
- Provider edge IPs can change; rerun setup if capture coverage drops

### System-Wide Transparent Mode

This is the aggressive mode for maximum automatic capture across the entire host.

```bash
sudo $(pwd)/.venv/bin/claw-cost-daemon setup --network-scope system
```

What this does:
- Installs an nftables/iptables redirect for outbound TCP/443 traffic
- Blocks QUIC so HTTPS flows through the proxy
- Captures a very large amount of traffic automatically

Risk:
- Can interfere with local TLS and daemon-based setups
- In particular, `signal-cli` can break in this mode
- That is why this mode is not the default for OpenClaw workstations

Signal note:
- If interactive setup detects an enabled Signal channel, it prompts for confirmation before keeping hard redirect.
- If you do not explicitly confirm, setup falls back to `openclaw` scope.

### Which Option Should I Use?

- OpenClaw + `claw-code` + Signal on the same machine: `setup --network-scope openclaw`
- Broader transparent capture while reducing non-AI side effects: `setup --network-scope system-ai-only`
- Local testing with a manually configured proxy only: `run --mode regular`
- Maximum host-wide capture where Signal does not matter or is isolated: `setup --network-scope system`

### Stopping

**In the terminal:** `Ctrl+C`

**Or via command:**
```bash
sudo pkill -f mitmdump
sudo pkill -f claw-cost-daemon
```

### Troubleshooting

**Port 9090 is already in use:**
```bash
# Kill the old process
sudo pkill -f mitmdump

# Or use another port
sudo $(pwd)/.venv/bin/claw-cost-daemon run --port 19090 --mode transparent
```

**Important for OpenClaw + Signal:**
- `signal-cli` often uses `127.0.0.1:8080` as its daemon port
- That is why `claw-cost-daemon` defaults to `9090`
- By default, `setup` uses `--network-scope openclaw` and automatically sets `HTTP(S)_PROXY` for the OpenClaw gateway service and for new `claw-code` shells, instead of transparently redirecting the whole host
- For transparent interception that focuses on AI providers only, use: `sudo $(pwd)/.venv/bin/claw-cost-daemon setup --network-scope system-ai-only`
- If you really want system-wide interception, use: `sudo $(pwd)/.venv/bin/claw-cost-daemon setup --network-scope system`

**"Command not found" with sudo:**
Always use the absolute path:
```bash
sudo $(pwd)/.venv/bin/claw-cost-daemon run
# NOT: sudo claw-cost-daemon run (this will not work)
```

---

## 🛠️ Development

### Adding Dependencies

```bash
# In the activated venv
pip install <package>
pip freeze > requirements.txt
git add requirements.txt
git commit -m "Add: <package>"
```

### Code Changes

Because `pip install -e .` is used, changes are active immediately:
```bash
# Edit src/claw_cost_daemon/...
# Then test directly:
claw-cost-daemon --help
```

### Branches

- `main` – Stable version
- `dev` – Development

---

## 📋 Requirements

- Python 3.10+
- Linux (for transparent mode)
- Root privileges (only for transparent mode)

---

## 🔗 GitHub

https://github.com/Cachet23/claw-cost-daemon
