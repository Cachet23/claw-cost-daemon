#!/usr/bin/env bash
# ai-cost-monitor – Teardown: remove all network redirect rules
# Requires: root/sudo

set -euo pipefail

echo "🔧 [ai-cost-monitor] Tearing down network redirect..."

# ── Remove nftables rules ─────────────────────────────────
if command -v nft &>/dev/null; then
    if nft list tables 2>/dev/null | grep -q "ai-cost-monitor"; then
        echo "🗑️  Removing nftables table 'ai-cost-monitor'..."
        nft delete table inet ai-cost-monitor 2>/dev/null || true
        nft delete table inet ai-cost-monitor-filter 2>/dev/null || true
        echo "✅ nftables rules removed."
    else
        echo "   No nftables rules to remove."
    fi
fi

# ── Remove iptables rules ─────────────────────────────────
if command -v iptables &>/dev/null; then
    # Remove from OUTPUT chain
    iptables -t nat -D OUTPUT -j AI_COST_MONITOR 2>/dev/null || true
    # Flush and delete custom chain
    iptables -t nat -F AI_COST_MONITOR 2>/dev/null || true
    iptables -t nat -X AI_COST_MONITOR 2>/dev/null || true
    # Remove QUIC block
    iptables -D OUTPUT -p udp --dport 443 -j REJECT --reject-with icmp-port-unreachable 2>/dev/null || true
    echo "✅ iptables rules removed."
fi

# ── Remove ip6tables rules ────────────────────────────────
if command -v ip6tables &>/dev/null; then
    ip6tables -t nat -D OUTPUT -j AI_COST_MONITOR 2>/dev/null || true
    ip6tables -t nat -F AI_COST_MONITOR 2>/dev/null || true
    ip6tables -t nat -X AI_COST_MONITOR 2>/dev/null || true
    ip6tables -D OUTPUT -p udp --dport 443 -j REJECT 2>/dev/null || true
fi

echo "✅ Network teardown complete."
