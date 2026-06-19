# HanzHub Audit & Agent

Aplikace pro bezpečný audit, diagnostiku a správu domácího Raspberry Pi serveru (HanzHub) pomocí AI.

Aplikace běží lokálně na tvém počítači, připojuje se k serveru přes SSH (pouze pro čtení) a využívá OpenAI API pro analýzu dat, vysvětlení problémů a návrhy řešení.

## Funkce

- **Bezpečný read-only audit:** Sbírá data o disku, RAM, Docker kontejnerech, systemd službách a síťových portech bez provádění změn.
- **AI Analýza:** Automaticky vyhodnocuje výsledky auditu, hledá anomálie (např. obří logy, memory leaky) a navrhuje konkrétní kroky k nápravě.
- **Moderní GUI:** Postavené na `ttkbootstrap` pro čistý a přehledný vzhled.
- **Interaktivní chat:** Můžeš se AI ptát na detaily auditu nebo požádat o plán opravy.
- **Akční tlačítka:** U každého doporučení můžeš jedním kliknutím spustit hlubší diagnostiku nebo požádat o plán řešení ("Zkusit vyřešit" / "Vyřešit").
- **Inventura služeb:** Automaticky mapuje běžící služby a ukládá je do `config/services.yaml`.

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
- `info.txt` - Tvoje osobní poznámky, které AI čte pro lepší kontext
