# Agent Admin (HanzHub Audit)

Desktopová aplikace pro audit, diagnostiku a správu domácího Raspberry Pi (HanzHub) pomocí AI.

Běží lokálně na tvém PC, připojuje se přes SSH a využívá OpenAI API. Zápis na Pi (služby, soubory, cache) je vždy řízený whitelistem v `config/permissions.yaml` — citlivé akce potvrzuješ v dialogu.

**Verze:** viz `hanz_audit/version.py` a [CHANGELOG.md](CHANGELOG.md).

## Funkce

- **Audit:** disk, RAM, Docker, systemd, porty.
- **AI chat s nástroji:** agent tyká, mluví jako profík/kámoš; při SSH spouští tools místo popisu příkazů.
- **Soubory na Pi:** `read_file`, `write_file`, `delete_file` jen na whitelistu `file_paths` — zápis a mazání vyžadují tvé potvrzení.
- **Knihovna nástrojů:** `config/custom_tools.yaml` + balíček `hanz-agent-tools-v1` (skripty na Pi v `/opt/agentAdmin/tools`).
- **Agent konzole:** live log SSH příkazů.
- **Inventura služeb:** `config/services.yaml`.

## Instalace skriptů na Pi

```bash
# Na Raspberry Pi — připraví /opt/agentAdmin a /etc/agentAdmin
sudo bash scripts/pi-install-agentAdmin.sh
sudo cp -a hanz-agent-tools-v1/scripts /opt/agentAdmin/tools/
sudo cp hanz-agent-tools-v1/config/* /etc/agentAdmin/
```

Nebo viz [hanz-agent-tools-v1/README.md](hanz-agent-tools-v1/README.md).

**Poznámka:** `/opt/hanz-agent` se nepoužívá — hanz-agent služba je mimo provoz.

## Instalace a spuštění

### 1. Příprava prostředí

Ujisti se, že máš nainstalovaný Python 3.10+.

```powershell
# Vytvoření virtuálního prostředí
python -m venv .venv

# Aktivace (PowerShell)
.venv\Scripts\activate

# Instalace závislostí
pip install -r requirements.txt
```

### 2. Konfigurace

1. Zkopíruj `.env.example` na `.env` a doplň svůj OpenAI API klíč:
   ```env
   OPENAI_API_KEY=sk-tvuj-klic
   OPENAI_MODEL=gpt-4o-mini
   ```

2. Zkontroluj `config.yaml` a ujisti se, že IP adresa a uživatel pro SSH odpovídají tvému Raspberry Pi:
   ```yaml
   ssh:
     host: "192.168.1.5"
     user: "hanz"
     key_path: "C:\\ssh\\id_ed25519" # Cesta k tvému privátnímu klíči
   ```

### 3. Spuštění

```powershell
python run.py
```

## Jak to používat

1. Po spuštění aplikace klikni na **Připojit SSH**.
2. Až se stav změní na zelenou (Připojeno), klikni na **Spustit audit**.
3. Počkej na dokončení auditu (může to trvat 1-2 minuty).
4. V záložce **Přehled auditu** uvidíš srozumitelné shrnutí stavu serveru.
5. V panelu **Akce — co dělat dál** najdeš doporučení od AI. Můžeš použít tlačítka pro další kroky, které se propíšou do chatu.
6. V **Chatu** se můžeš ptát na cokoliv ohledně serveru. AI má kontext z posledního auditu i z tvých poznámek v `info.txt`.

## Přizpůsobení chování AI

Chování agenta, formát odpovědí a vzhled přehledu můžeš upravit v souboru `config/agent.yaml`. Můžeš přidávat vlastní pravidla (např. "vždy upozorni na zálohu") nebo definovat, které služby jsou kritické.

## Struktura projektu

- `hanz_audit/` - Zdrojové kódy aplikace
  - `gui.py` - Hlavní okno aplikace (ttkbootstrap)
  - `audit.py` - SSH příkazy pro sběr dat
  - `analysis.py` - Logika pro vyhodnocení auditu
  - `chat.py` - Komunikace s OpenAI API
  - `inventory.py` - Mapování služeb
- `config/` - Konfigurační soubory
  - `agent.yaml` - Chování AI a formát reportů
  - `services.yaml` - Automaticky generovaná inventura
- `data/audits/` - Zde se automaticky ukládají Markdown reporty z každého auditu
- `config/permissions.yaml` - Whitelist nástrojů, cest pro soubory a cache
- `scripts/pi-install-agentAdmin.sh` - Příprava `/opt/agentAdmin` na Pi
- `hanz-agent-tools-v1/` - Skriptový balíček nástrojů (instalace do `/opt/agentAdmin/tools`)
