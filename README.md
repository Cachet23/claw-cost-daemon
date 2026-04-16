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

**Option A: Transparenter Modus (empfohlen, benötigt root)**
```bash
sudo $(pwd)/.venv/bin/claw-cost-daemon run
```
- Interceptiert allen API-Traffic automatisch
- Network rules werden automatisch gesetzt
- CA-Zertifikat wird systemweit vertraut

**Option B: Setup-Wizard (erster Start)**
```bash
sudo $(pwd)/.venv/bin/claw-cost-daemon setup
```
- Führt durch 5 Schritte: Dependencies, DB, CA, Network Rules, Start
- Fragt nach Integrationen (OpenClaw, claw-code, etc.)

**Option C: Regularer Modus (ohne root)**
```bash
.venv/bin/claw-cost-daemon run --mode regular
```
- Apps müssen Proxy manuell konfigurieren (`http://localhost:8080`)
- Gut für Testing ohne sudo

### Stoppen

**Im Terminal:** `Ctrl+C`

**Oder per Command:**
```bash
sudo pkill -f mitmdump
sudo pkill -f claw-cost-daemon
```

### Fehlerbehebung

**Port 8080 bereits belegt:**
```bash
# Alten Prozess killen
sudo pkill -f mitmdump

# Oder anderen Port verwenden
sudo $(pwd)/.venv/bin/claw-cost-daemon run --mode transparent@8082
```

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
