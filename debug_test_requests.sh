#!/bin/bash
# Test-Requests für Debug — Terminal 2
# Sendet Requests während Terminal 1 mitmproxy Logs zeigt

PROXY="127.0.0.1:8080"
CACERT="${HOME}/.mitmproxy/mitmproxy-ca-cert.pem"
DB_PATH="${HOME}/.ai-cost-monitor/events.db"

echo "🧪 ai-cost-monitor DEBUG — Test Requests (Terminal 2)"
echo "======================================================="
echo ""
echo "ℹ️  Stelle sicher dass Terminal 1 bereits läuft:"
echo "    cd /home/claw/tech/ai-cost-monitor && sudo .venv/bin/ai-cost-monitor start"
echo ""
read -p "👉 Drücke Enter um Tests zu starten..."
echo ""

# Test 1: Direct HTTPS request (transparent proxy)
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Test 1️⃣  — Nackte HTTPS (transparent proxy):"
echo "Command: curl -I https://api.openai.com/v1/models"
echo ""
echo "Expected in Terminal 1 logs:"
echo "  → 'CONNECT api.openai.com:443' then TLS handshake"
echo "  → Optionally: '[ai-cost-monitor]' addon message"
echo ""
read -p "👉 Drücke Enter für Test..."
curl -I https://api.openai.com/v1/models -v 2>&1 | grep -E "(Connected|TLS|HTTP)" || echo "  (Keine interessanten Logs)"
echo ""

# Test 2: Explicit proxy
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Test 2️⃣  — Expliziter Proxy (direct connect to :8080):"
echo "Command: curl -x http://127.0.0.1:8080 --cacert mitmproxy-ca-cert.pem https://api.openai.com..."
echo ""
read -p "👉 Drücke Enter für Test..."
curl -I -x "http://${PROXY}" \
  --cacert "$CACERT" \
  https://api.openai.com/v1/models -v 2>&1 | grep -E "(Connected|HTTP|proxy)" || echo "  (keine interessanten Logs)"
echo ""

# Test 3: Check DB
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Test 3️⃣  — Wurden Events in DB geloggt?"
if [ -f "$DB_PATH" ]; then
  COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM events;" 2>/dev/null || echo "?")
  echo "  Events in DB: $COUNT"
  if [ "$COUNT" -gt 0 ]; then
    echo "  ✅ Tracking funktioniert!"
    echo ""
    echo "  Letzte Events:"
    sqlite3 "$DB_PATH" -header "SELECT ts, provider, model, status_code FROM events ORDER BY ts DESC LIMIT 3;" 2>/dev/null
  else
    echo "  ❌ Keine Events — Addon wird evtl. nicht aufgerufen"
  fi
else
  echo "  ❌ DB existiert nicht: $DB_PATH"
fi
echo ""
echo "======================================================="
echo "Done. Schau in Terminal 1 ob die Requests geloggt wurden"
