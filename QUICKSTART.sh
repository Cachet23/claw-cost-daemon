#!/bin/bash
# QUICK START für Debug — Schritt-für-Schritt

cat << 'EOF'
╔══════════════════════════════════════════════════════════════════════╗
║  🚀 QUICK START: Debug warum LLM-Responses blockiert                ║
║                                                                      ║
║  Problem: Wenn Monitor läuft, keine LLM-Antworten                   ║
█    Vermutung: MitM Proxy durchleitet Responses nicht                  ║
╚══════════════════════════════════════════════════════════════════════╝

SCHRITT 1️⃣  — Diagnose laufen lassen
═════════════════════════════════════════════════════════════════════

  cd /home/claw/tech/ai-cost-monitor
  bash DIAGNOSE.sh

  Das zeigt ob:
  ✓ Network rules aktiv sind
  ✓ mitmproxy läuft
  ✓ Database Events loggt
  ✓ CA-Cert vertraut ist

═════════════════════════════════════════════════════════════════════

SCHRITT 2️⃣  — Falls Diagnose ❌ zeigt, Setup starten
═════════════════════════════════════════════════════════════════════

  Terminal 1 (stehen lassen):
  ─────────────────────────────
  sudo .venv/bin/ai-cost-monitor start

  Das wird:
  • Netzwerk-Rules setzen (transparent proxy redirect)
  • CA-Cert trusten
  • mitmproxy auf Port 8080 starten
  • Dashboard zeigen


  Terminal 2 (gleichzeitig):
  ─────────────────────────────
  bash debug_test_requests.sh

  Das sendet Test-API-Requests → beobachte Terminal 1!

═════════════════════════════════════════════════════════════════════

SCHRITT 3️⃣  — Beobachte Terminal 1 logs
═════════════════════════════════════════════════════════════════════

  ✅ GUT:
     • "CONNECT api.openai.com:443" → TLS handshake → response
     • Optionally: "[ai-cost-monitor] OpenAI | gpt-..." Addon logs
     • Dashboard zeigt neue Events
     → TRACKING FUNKTIONIERT, Problem woanders

  ❌ SCHLECHT:
     • Keine Requests/Responses
     → Transparent proxy funktioniert nicht
     → Check: nft list table inet ai-cost-monitor

     • Requests kommen an, aber blockieren
     → mitmproxy addon issue
     → Check: /tmp/mitmproxy_debug.log oder Addon error

═════════════════════════════════════════════════════════════════════

SCHRITT 4️⃣  — Falls immer noch Probleme
═════════════════════════════════════════════════════════════════════

  A) Transparent Proxy funktioniert nicht?
     
     → Teste direkten Proxy-Zugang (nicht transparent):
     
     export https_proxy=http://127.0.0.1:8080
     export http_proxy=http://127.0.0.1:8080
     curl https://api.openai.com/v1/models
     
     Falls das funktioniert:
     → nftables rules sind kaputt
     → Lösung: sudo PYTHONPATH=/home/claw/tech/ai-cost-monitor/src \
       python3 -c "from ai_cost_monitor.network import setup_network; setup_network()"

  B) mitmproxy zeigt SSL-Fehler?
  
     → CA-Cert nicht vertraut
     → Fix: sudo update-ca-certificates
     → Verifizieren: openssl verify /path/to/cert.pem

  C) Addon wird nicht aufgerufen?
  
     → Check Addon Path: ls -la src/ai_cost_monitor/addons/ai_capture.py
     → Test mit: sudo python3 -c "import ai_cost_monitor.addons.ai_capture"

═════════════════════════════════════════════════════════════════════

FORTGESCHRITTENES DEBUGGING:
═════════════════════════════════════════════════════════════════════

  mitmproxy mit maximalen Logs starten:

    cd /home/claw/tech/ai-cost-monitor
    sudo .venv/bin/mitmdump \
      -s src/ai_cost_monitor/addons/ai_capture.py \
      --listen-port 8080 \
      --set connection_strategy=lazy \
      --set db_path=~/.ai-cost-monitor/events.db \
      -v


  Oder mit Python-Tracing:

    export PYTHONUNBUFFERED=1
    cd /home/claw/tech/ai-cost-monitor
    sudo -u root -E python3 -m pdb -c continue \
      .venv/bin/mitmdump \
        -s src/ai_cost_monitor/addons/ai_capture.py ...

═════════════════════════════════════════════════════════════════════

NOCH FRAGEN?

  1. Schau in DEBUG_GUIDE.md für detaillierte Erklärungen
  2. Check die Logs: /tmp/mitmproxy_debug.log (falls existiert)
  3. Schau in network.py um zu verstehen wie rules gesetzt werden

═════════════════════════════════════════════════════════════════════

EOF
