#!/bin/bash
# Diagnose-Skript — findet warum Transparent Proxy nicht funktioniert

cat << 'EOF'
╔════════════════════════════════════════════════════════════════════╗
║   🔧 DIAGNOSE: Warum LLM-Responses blockiert?                      ║
╚════════════════════════════════════════════════════════════════════╝

EOF

echo ""
echo "1️⃣  Checking Network Rules..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check nftables
if command -v nft &> /dev/null; then
  TABLES=$(sudo nft list tables 2>/dev/null | grep -c "ai-cost-monitor" || echo "0")
  if [ "$TABLES" -gt 0 ]; then
    echo "  ✅ nftables rules aktiv:"
    sudo nft list table inet ai-cost-monitor 2>/dev/null | head -5
    echo "  ... (siehe oben)"
  else
    echo "  ❌ nftables rules NICHT aktiv"
    echo "     → Run: sudo .venv/bin/ai-cost-monitor start (first step)"
  fi
else
  echo "  ⚠️  nft not found (iptables fallback wird verwendet)"
fi

echo ""
echo "2️⃣  Checking mitmproxy Process..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

MITM_PID=$(pgrep -f "mitmdump.*ai_capture" || echo "")
if [ -n "$MITM_PID" ]; then
  echo "  ✅ mitmproxy läuft (PID $MITM_PID)"
  echo "     Listening on:"
  sudo netstat -tlnp 2>/dev/null | grep mitmdump || sudo ss -tlnp 2>/dev/null | grep mitmdump || echo "     (port scanning failed)"
else
  echo "  ❌ mitmproxy läuft NICHT"
  echo "     → Start: sudo .venv/bin/ai-cost-monitor start"
fi

echo ""
echo "3️⃣  Checking CA Certificate..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

CACERT="${HOME}/.mitmproxy/mitmproxy-ca-cert.pem"
if [ -f "$CACERT" ]; then
  echo "  ✅ CA cert existiert: $CACERT"
  TRUSTED=$(grep "ai-cost-monitor-mitmproxy" /etc/ca-certificates/extracted/tls-ca-bundle.pem 2>/dev/null || echo "")
  if [ -n "$TRUSTED" ]; then
    echo "  ✅ CA cert ist vertraut (system-wide)"
  else
    echo "  ⚠️  CA cert könnte nicht vertraut sein"
    echo "     Try: sudo update-ca-certificates"
  fi
else
  echo "  ❌ CA cert existiert nicht"
fi

echo ""
echo "4️⃣  Checking Port 8080..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if sudo lsof -i :8080 2>/dev/null | grep -q LISTEN; then
  echo "  ✅ Port :8080 LISTEN"
  sudo lsof -i :8080 2>/dev/null | grep -v COMMAND
else
  echo "  ❌ Port :8080 nicht in use"
  echo "     → mitmproxy startet nicht korrekt"
fi

echo ""
echo "5️⃣  Checking Database..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

DB_PATH="${HOME}/.ai-cost-monitor/events.db"
if [ -f "$DB_PATH" ]; then
  COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM events;" 2>/dev/null || echo "?")
  ERRORS=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM dead_letters;" 2>/dev/null || echo "?")
  echo "  ✅ Database existiert: $DB_PATH"
  echo "     Events captured: $COUNT"
  echo "     Parsing errors: $ERRORS"
  
  if [ "$COUNT" -gt 0 ]; then
    echo "  ✅ TRACKING FUNKTIONIERT!"
    echo ""
    echo "     Letzte Events:"
    sqlite3 "$DB_PATH" "SELECT ts, provider, model, status_code FROM events ORDER BY ts DESC LIMIT 3;" 2>/dev/null
  fi
else
  echo "  ❌ Database nicht erstellt: $DB_PATH"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo ""
echo "NÄCHSTER SCHRITT:"
echo "  Falls Tests alle ✅ sind:"
echo "    → Problem liegt in Response-Handling (mitmproxy blockiert Antworten)"
echo ""
echo "  Falls Tests ❌ zeigen:"
echo "    → Run: sudo .venv/bin/ai-cost-monitor start"
echo "    → Dann dieses Script nochmal ausführen"
echo ""
