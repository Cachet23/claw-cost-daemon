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
sudo claw-cost-daemon run
```
- Interceptiert allen API-Traffic automatisch
- Keine Konfiguration der Apps nötig
- Network rules werden automatisch gesetzt

**Option B: Regularer Modus (ohne root)**
```bash
claw-cost-daemon run --mode regular
```
- Apps müssen Proxy manuell konfigurieren (`http://localhost:8080`)
- Gut für Testing ohne sudo

### Stoppen

Im transparenten Modus: `Ctrl+C` im Terminal  
Oder:
```bash
pkill -f mitmproxy
pkill -f claw-cost-daemon
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
