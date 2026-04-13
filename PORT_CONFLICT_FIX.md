#!/bin/bash
# FIX: Port 8080 Konflikt mit signal-cli

cat << 'EOF'
╔════════════════════════════════════════════════════════════════════╗
║  ⚠️  PROBLEM GEFUNDEN: Port 8080 Konflikt                          ║
║                                                                      ║
║  signal-cli läuft BEREITS auf Port 8080                             ║
║  → mitmproxy kann nicht starten                                     ║
║  → Deshalb funktioniert der Monitor nicht                           ║
╚════════════════════════════════════════════════════════════════════╝

LÖSUNG 1️⃣  — mitmproxy auf anderen Port verschieben (einfach) ✅
═════════════════════════════════════════════════════════════════════

  cd /home/claw/tech/ai-cost-monitor
  
  # Start mit Port 8081 statt 8080
  sudo .venv/bin/ai-cost-monitor start --port 8081

  Das ist sofort fix und "just works"!


LÖSUNG 2️⃣  — signal-cli Port ändern (besser, langfristig)
═════════════════════════════════════════════════════════════════════

  Wenn signal-cli für dich auch nur auf 8080 sein MUSS:

  1. Find signal-cli config:
     find ~/.config -name "*signal*" -type f 2>/dev/null

  2. Change port 8080 → 8081 (oder beliebig)

  3. Restart signal-cli

  4. Dann kann mitmproxy auf 8080 laufen


LÖSUNG 3️⃣  — signal-cli tempor stoppedar (quick test)
═════════════════════════════════════════════════════════════════════

  # Kill signal-cli temporarily
  pkill -f signal-cli

  # Start monitor auf 8080
  cd /home/claw/tech/ai-cost-monitor
  sudo .venv/bin/ai-cost-monitor start

  # Restart signal-cli später
  signal-cli ...

═════════════════════════════════════════════════════════════════════

MEINE EMPFEHLUNG: LÖSUNG 1️⃣
═════════════════════════════════════════════════════════════════════

mitmproxy läuft auf einem beliebigen Port:

  sudo .venv/bin/ai-cost-monitor start --port 8081

Das ist der einfachste Fix!

EOF
