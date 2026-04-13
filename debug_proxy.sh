#!/bin/bash
# Debug-Wrapper für ai-cost-monitor — zeigt mitmproxy Logs mit verbose output

set -e

cd "$(dirname "$0")"

VENV_BIN=".venv/bin"
DB_PATH="${HOME}/.ai-cost-monitor/events.db"
PROXY_PORT="8080"
ADDON_PATH="src/ai_cost_monitor/addons/ai_capture.py"

echo "🔍 ai-cost-monitor DEBUG MODE"
echo "======================================"
echo ""
echo "ℹ️  This script starts mitmproxy directly (without dashboard)"
echo "    to debug traffic capture issues."
echo ""
echo "📌 NOTE: Network rules must be set up first!"
echo "   If not already active, run in another terminal:"
echo "   sudo $VENV_BIN/ai-cost-monitor start"
echo ""
echo "Starting mitmproxy with logging..."
echo "   This shows ALL traffic through the proxy"
echo "   Addon: $ADDON_PATH"
echo "   DB: $DB_PATH"
echo ""
echo "======================================"
echo "Press Ctrl+C to stop and clean up"
echo "======================================"
echo ""

# Run mitmproxy with clear logging
sudo "$VENV_BIN/mitmdump" \
  -s "$ADDON_PATH" \
  --listen-port "$PROXY_PORT" \
  --set connection_strategy=lazy \
  --set "db_path=${DB_PATH}"

echo ""
echo "✅ mitmproxy stopped. Network rules still active (managed by 'start' command)."
