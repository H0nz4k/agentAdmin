# Changelog

Všechny důležité změny v projektu Agent Admin (dříve HanzHub Audit) jsou zaznamenány v tomto souboru.

## [1.4.4] - 2026-06-19
### Přidáno
- **Anti-loop ochrana** — po limitu zápisů blokace nástrojů, vynucená změna strategie, oprava historie tool_calls (chyba 400).
- **Větší tlačítko 👍** — vlastní ttk styl, zelený stav při potvrzení.

### Změněno
- GUI: tlačítko Agent v liště, vyšší tlačítka, hlavička verze, progress jen při SSH.
- Agent prompt: pravidla proti opakování nástrojů (`no_tool_loops`).

## [1.4.3] - 2026-06-19
### Přidáno
- **create_local_document** — agent vytvoří Markdown dokumentaci na PC (`data/docs/`), s potvrzením v GUI.
- **list_local_documents** — přehled lokálních .md souborů.
- Nástroje fungují v chatu i bez SSH (Pi nástroje stále vyžadují připojení).

## [1.4.2] - 2026-06-19
### Přidáno
- **get_docker_disk_overview** — read-only diagnostika docker místa před prune.

### Změněno
- Agent neopakuje `docker_prune_dangling` když vrátí 0B; lepší popisy nástrojů a paměť v knowledge.yaml.
- Zelený progress v liště jen při SSH připojování (1.4.1).

## [1.4.1] - 2026-06-19
### Přidáno
- **Deploy na Pi:** `scripts/deploy_pi.py` a `scripts/deploy-to-pi.ps1` — nahrání balíčku přes SSH a instalace do `/opt/agentAdmin`.
- **`scripts/pi/`** — složka pro vlastní skripty, které deploy zkopíruje na malinu.
- **`pi-install-agentAdmin.sh`** — kompletní instalace ze staging adresáře (volá se z deploye).

## [1.4.0] - 2026-06-19
### Přidáno
- **Souborové nástroje s validací:** `read_file`, `write_file`, `delete_file` — whitelist v `config/permissions.yaml` → `file_paths`; zápis (úroveň 2) a mazání (úroveň 3) vyžadují potvrzení v GUI.
- **Migrace cest na Pi:** `/opt/agentAdmin/` a `/etc/agentAdmin/` místo `/opt/hanz-agent` (hanz-agent je mimo provoz).
- **Instalační skript:** `scripts/pi-install-agentAdmin.sh` pro přípravu adresářů na Raspberry Pi.
- **Osobnost agenta:** tykání, tón „profík + kámoš“ v `config/agent.yaml` (`tone_peer`).

### Změněno
- Balíček `hanz-agent-tools-v1` — všechny cesty přepsány na `/opt/agentAdmin` / `/etc/agentAdmin`.
- Zálohy konfigurace: odstraněn `hanz_agent_env`, přidán `agentadmin_config`.
- GUI texty (Nástroje, uvítání) odkazují na nové cesty.

### Opraveno
- Agent v chatu používá nástroje místo popisu plánů (tools + SSH).
- Tématické dialogy místo bílých Windows messageboxů.
- `format_overview` NameError.

## [1.3.0] - 2026-06-19
### Přidáno
- **Agent konzole** — live log SSH příkazů (tlačítko Agent).
- **Knihovna nástrojů** — editor `config/custom_tools.yaml`, tlačítko Nástroje.
- **Integrace hanz-agent-tools-v1** — 22 skriptů jako `custom_v1_*`.
- **Chat s nástroji** — progress bar při práci agenta.
- **Servisní nástroje:** stop/start/enable/disable služby, `list_old_backups`.

## [1.1.0] - 2026-06-19
### Přidáno
- **Moderní GUI:** Kompletní přepis UI do `ttkbootstrap` (moderní vzhled, barvy, padding).
- **Podpora témat:** Možnost změnit vzhled (světlý/tmavý) v `config/agent.yaml` pomocí `ui.theme`.
- **Progress bar:** Animovaný ukazatel průběhu v horní liště během auditu a komunikace s AI.
- **Zobrazení verze:** Verze aplikace se zobrazuje v záhlaví okna.
- **Changelog:** Zaveden soubor `CHANGELOG.md` pro sledování změn.

### Opraveno
- Opravena barva textu v tmavých tématech (nyní je text vždy čitelný na tmavém pozadí).

## [1.0.0] - 2026-06-19
### Přidáno
- **Základní aplikace:** MVP verze s připojením přes SSH.
- **Read-only audit:** Sběr dat o disku, RAM, Dockeru, systemd a portech.
- **AI Analýza:** Automatické vyhodnocení auditu a návrhy řešení.
- **Chat:** Možnost doptávat se AI na detaily z auditu.
- **Akční tlačítka:** Tlačítka "Zkusit vyřešit" a "Vyřešit" pro každé doporučení.
- **Inventura:** Automatické generování `config/services.yaml`.
