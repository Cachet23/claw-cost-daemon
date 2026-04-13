#!/bin/bash
# Debug Setup Guide — Koordiniert zwei Terminals für MitM-Proxy Debugging

cat << 'EOF'
╔════════════════════════════════════════════════════════════════════╗
║   🔍 ai-cost-monitor DEBUG SETUP                                   ║
║   Findet heraus warum der Proxy LLM-Responses blockiert            ║
╚════════════════════════════════════════════════════════════════════╝

PROBLEM:
  ❌ LLM gibt keine Antworten, wenn Monitor läuft
  → Wahrscheinlich: Proxy durchleitet Responses nicht korrekt

DIAGNOSE-STRATEGIE:
  1. Terminal 1: Start mitmproxy mit Logging
  2. Terminal 2: Mache API-Requests → beobachte Terminal 1 Logs
  3. Analysiere ob/wo Request blockiert wird

════════════════════════════════════════════════════════════════════

SCHRITT 1: Terminal 1 — Proxy mit Logging starten

  cd /home/claw/tech/ai-cost-monitor
  sudo .venv/bin/ai-cost-monitor start

  Das wird:
    ✓ Network rules setzen (nftables redirect to :8080)
    ✓ CA-Cert trusten
    ✓ mitmproxy starten
    ✓ Dashboard zeigen

  Wichtig: Hier stehenbleiben, die Logs beobachten!

════════════════════════════════════════════════════════════════════

SCHRITT 2: Terminal 2 — Test-Requests senden

  Option A - Mit curl durchs Netzwerk (testet transparent proxy):
  
    curl -v https://api.openai.com/v1/models 2>&1 | head -30

  Option B - Direkt durch Proxy (testet mitmproxy setup):
  
    curl -v -x http://127.0.0.1:8080 \
      --cacert ~/.mitmproxy/mitmproxy-ca-cert.pem \
      https://api.openai.com/v1/models

  Option C - Echte LLM-Request mit openclaw:
  
    openclaw "Hallo — teste ob ich antworie bekomme"

════════════════════════════════════════════════════════════════════

WAS IN TERMINAL 1 ZU SEHEN IST:

  ✅ GUTES ZEICHEN — Traffic wird gesehen:
    • "GET https://api.openai.com/..."  
    • Requests/Responses mit Status-Codes
    • Optional: "[ai-cost-monitor]" Addon-Messages

  ❌ SCHLECHTE ZEICHEN — Problem:
    • Keine Requests auftauchen → Transparent proxy kaputt
    • Requests come in aber keine Response → Addon blockiert
    • SSL-Fehler → CA-Cert nicht vertraut

════════════════════════════════════════════════════════════════════

HÄUFIGE PROBLEME + LÖSUNGEN:

  1. "Connection refused" in Terminal 2
     → Proxy läuft nicht auf :8080
     → Check: sudo lsof -i :8080

  2. "SSL: CERTIFICATE_VERIFY_FAILED"
     → CA-Cert nicht vertraut
     → Rerun: sudo update-ca-certificates

  3. Requests ankommen aber Responses nicht weitergeleitet
     → Addon-Bug in ai_capture.py
     → Check Terminal 1 Logs auf Fehler

  4. Requests kommen gar nicht an
     → nftables rules nicht richtig
     → Check: sudo nft list table inet ai-cost-monitor

════════════════════════════════════════════════════════════════════

FORTGESCHRITTENES DEBUGGING:

  Wenn oben nicht hilft, direkter mitmproxy Start (ohne Dashboard):

    cd /home/claw/tech/ai-cost-monitor
    
    # Mit minimalen Dependencies (um Fehler zu sehen):
    sudo .venv/bin/mitmdump \
      -s src/ai_cost_monitor/addons/ai_capture.py \
      --listen-port 8080 \
      --set connection_strategy=lazy \
      --set db_path=~/.ai-cost-monitor/events.db \
      -v  # verbose logging

════════════════════════════════════════════════════════════════════

READY? Starten Sie:

  Terminal 1:  cd /home/claw/tech/ai-cost-monitor && sudo .venv/bin/ai-cost-monitor start
  
  Terminal 2:  curl -v https://api.openai.com/v1/models (oder OpenClaw)

  Drücke Ctrl+C in Terminal 1 zum Stoppen (räumt Network auf)

EOF
