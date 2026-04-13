#!/usr/bin/env bash
# ai-cost-monitor – Network setup for transparent MITM proxy
# Redirects outgoing HTTPS (TCP 443) through local mitmproxy
# Requires: root/sudo

set -euo pipefail

PROXY_PORT="${PROXY_PORT:-8080}"
MITM_UID="${MITM_UID:-$(id -u)}"
MARK="${MITM_MARK:-0x1}"

# Resolve the original user's home (before sudo) and venv
if [ -n "${SUDO_USER:-}" ]; then
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    REAL_HOME="$HOME"
fi
VENV_BIN="${REAL_HOME}/tech/ai-cost-monitor/.venv/bin"
if [ -x "${VENV_BIN}/mitmdump" ]; then
    export PATH="${VENV_BIN}:$PATH"
fi

echo "🔧 [ai-cost-monitor] Setting up transparent proxy redirect..."
echo "   Proxy port: $PROXY_PORT"
echo "   Mitmproxy UID: $MITM_UID"

# ── Generate mitmproxy CA certificate if missing ──────────
CERT_DIR="${REAL_HOME}/.mitmproxy"
mkdir -p "$CERT_DIR"
if [ ! -f "$CERT_DIR/mitmproxy-ca-cert.pem" ]; then
    echo "📦 Generating mitmproxy CA certificate..."
    mitmdump --version >/dev/null 2>&1 || { echo "ERROR: mitmproxy not installed"; exit 1; }
    # Generate certs by briefly starting mitmdump
    timeout 2 mitmdump --listen-port "$PROXY_PORT" --set connection_strategy=lazy >/dev/null 2>&1 || true
fi

echo "⚠️  Make sure $CERT_DIR/mitmproxy-ca-cert.pem is trusted by your system!"
echo "   On Ubuntu/Debian: sudo cp $CERT_DIR/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/ && sudo update-ca-certificates"
echo "   On Arch: sudo trust anchor $CERT_DIR/mitmproxy-ca-cert.pem"

# ── Try nftables first ────────────────────────────────────
if command -v nft &>/dev/null; then
    echo "✅ Using nftables..."

    # Create a dedicated table (use nat for redirect instead of tproxy)
    nft add table inet ai-cost-monitor 2>/dev/null || true

    # Use nat output hook with redirect (works on all kernels, unlike tproxy)
    nft 'flush chain inet ai-cost-monitor output' 2>/dev/null || \
        nft 'add chain inet ai-cost-monitor output { type nat hook output priority -100 ; }'

    # Redirect outgoing TCP 443 to proxy (skip proxy's own traffic)
    nft add rule inet ai-cost-monitor output \
        tcp dport 443 \
        meta skuid != "$MITM_UID" \
        redirect to :"$PROXY_PORT"

    # Block QUIC (UDP 443) so traffic falls back to HTTPS/TCP
    echo "🚫 Blocking QUIC (UDP 443) to force TCP fallback..."
    nft add table inet ai-cost-monitor-filter 2>/dev/null || true
    nft 'flush chain inet ai-cost-monitor-filter output' 2>/dev/null || \
        nft 'add chain inet ai-cost-monitor-filter output { type filter hook output priority -100 ; }'
    nft add rule inet ai-cost-monitor-filter output \
        udp dport 443 \
        reject

    echo "✅ nftables rules installed."
    nft list table inet ai-cost-monitor

# ── Fallback: iptables ────────────────────────────────────
elif command -v iptables &>/dev/null; then
    echo "✅ Using iptables (fallback)..."

    # Create a new chain
    iptables -t nat -N AI_COST_MONITOR 2>/dev/null || iptables -t nat -F AI_COST_MONITOR

    # Redirect HTTPS to proxy
    iptables -t nat -A AI_COST_MONITOR \
        -p tcp --dport 443 \
        -m owner ! --uid-owner "$MITM_UID" \
        -j REDIRECT --to-port "$PROXY_PORT"

    # Add to OUTPUT chain
    iptables -t nat -C OUTPUT -j AI_COST_MONITOR 2>/dev/null || \
        iptables -t nat -A OUTPUT -j AI_COST_MONITOR

    # Block QUIC
    iptables -A OUTPUT -p udp --dport 443 -j REJECT --reject-with icmp-port-unreachable

    echo "✅ iptables rules installed."
    iptables -t nat -L AI_COST_MONITOR -v -n
else
    echo "❌ ERROR: Neither nft nor iptables found!"
    exit 1
fi

# ── Enable IP forwarding ──────────────────────────────────
echo 1 > /proc/sys/net/ipv4/ip_forward 2>/dev/null || true

echo ""
echo "✅ Network setup complete."
echo "   Now run: sudo ai-cost-monitor run"
echo "   To undo:  sudo ai-cost-monitor teardown-network"
