# 💰 claw-cost-daemon

**Real-time AI API cost monitoring for Linux**

Transparently intercepts AI API requests, calculates costs in real-time, attributes them to local processes, and displays everything in a live terminal dashboard.

---

## 🚀 Quick Start

### Installation (nach `git clone`)

```bash
# 1. Repo klonen
git clone https://github.com/Cachet23/claw-cost-daemon.git
cd claw-cost-daemon

# 2. Virtuelle Umgebung erstellen
python3 -m venv .venv

# 3. venv aktivieren
source .venv/bin/activate

# 4. Package installieren (editable für Entwicklung)
pip install -e .
```

### Starten

Es gibt drei sinnvolle Betriebsarten. Der wichtige Unterschied ist:
- `setup --network-scope openclaw` = empfohlen fuer OpenClaw + claw-code + Signal
- `run --mode regular` = nur lokaler Proxy, keine automatische App-Integration
- systemweiter transparenter Redirect = faengt fast alles ab, kann aber lokale Daemons wie `signal-cli` stoeren

### Empfohlen: OpenClaw-Scoped Setup

Das ist der Modus fuer deinen normalen Rechner mit OpenClaw Gateway, `claw-code` und Signal.

```bash
sudo $(pwd)/.venv/bin/claw-cost-daemon setup --network-scope openclaw --non-interactive
```

Was dabei passiert:
- Startet `mitmproxy` auf `127.0.0.1:9090`
- Konfiguriert OpenClaw automatisch per systemd drop-in
- Konfiguriert `claw-code` automatisch per Shell-Env in `~/.bashrc`
- Lässt `signal-cli` auf `127.0.0.1:8080` in Ruhe
- Verwendet keinen systemweiten Redirect

Wichtig:
- OpenClaw-Gateway wird direkt nach dem Setup neu geladen
- Fuer `claw-code` in der aktuellen Shell brauchst du danach einmal `source ~/.bashrc` oder ein neues Terminal

### Regular Mode

Das ist nur ein lokaler Proxy. Er eignet sich fuer Tests oder wenn du selbst ganz gezielt Apps auf den Proxy zeigen lassen willst.

```bash
.venv/bin/claw-cost-daemon run --mode regular
```

Wichtig:
- Dieser Modus faengt nicht automatisch alle Provider-Requests ab
- Er erfasst nur Traffic von Prozessen, die explizit `http://localhost:9090` als Proxy benutzen
- Wenn du nur `run --mode regular` startest, aber OpenClaw und `claw-code` nicht darauf konfiguriert sind, wird ihr Traffic nicht gesehen

### Systemweiter transparenter Modus

Das ist der aggressive Modus fuer maximale automatische Erfassung auf dem gesamten Host.

```bash
sudo $(pwd)/.venv/bin/claw-cost-daemon setup --network-scope system
```

Was dabei passiert:
- Setzt nftables/iptables Redirect fuer ausgehenden TCP/443
- Blockiert QUIC, damit HTTPS ueber den Proxy geht
- Faengt sehr viel Traffic automatisch ab

Risiko:
- Kann lokale TLS/Daemon-Setups stoeren
- Insbesondere `signal-cli` kann dabei brechen
- Deshalb ist dieser Modus nicht der Default fuer OpenClaw-Workstations

### Welche Option sollte ich nehmen?

- OpenClaw + `claw-code` + Signal auf einem Rechner: `setup --network-scope openclaw`
- Nur lokaler Test mit manuell gesetztem Proxy: `run --mode regular`
- Maximale Host-weite Erfassung und Signal ist egal oder isoliert: `setup --network-scope system`

### Stoppen

**Im Terminal:** `Ctrl+C`

**Oder per Command:**
```bash
sudo pkill -f mitmdump
sudo pkill -f claw-cost-daemon
```

### Fehlerbehebung

**Port 9090 bereits belegt:**
```bash
# Alten Prozess killen
sudo pkill -f mitmdump

# Oder anderen Port verwenden
sudo $(pwd)/.venv/bin/claw-cost-daemon run --port 19090 --mode transparent
```

**Wichtig für OpenClaw + Signal:**
- `signal-cli` nutzt oft `127.0.0.1:8080` als Daemon-Port.
- Deshalb verwendet `claw-cost-daemon` standardmäßig `9090`.
- `setup` nutzt standardmäßig `--network-scope openclaw` und setzt `HTTP(S)_PROXY` automatisch für den OpenClaw-Gateway-Service sowie für neue `claw-code`-Shells, statt den gesamten Host transparent umzuleiten.
- Wenn du wirklich systemweit intercepten willst, nutze explizit: `sudo $(pwd)/.venv/bin/claw-cost-daemon setup --network-scope system`

**"Command not found" mit sudo:**
Immer den absoluten Pfad verwenden:
```bash
sudo $(pwd)/.venv/bin/claw-cost-daemon run
# NICHT: sudo claw-cost-daemon run (funktioniert nicht!)
```

---

## 🛠️ Entwicklung

### Dependencies hinzufügen

```bash
# Im aktivierten venv
pip install <package>
pip freeze > requirements.txt
git add requirements.txt
git commit -m "Add: <package>"
```

### Änderungen am Code

Da `pip install -e .` verwendet wird, sind Änderungen sofort aktiv:
```bash
# Edit src/claw_cost_daemon/...
# Dann direkt testen:
claw-cost-daemon --help
```

### Branches

- `main` – Stabile Version
- `dev` – Entwicklung

---

## 📋 Voraussetzungen

- Python 3.10+
- Linux (für transparenten Modus)
- Root-Rechte (nur für transparenten Modus)

---

## 🔗 GitHub

https://github.com/Cachet23/claw-cost-daemon
