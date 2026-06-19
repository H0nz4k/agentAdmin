# HanzAgent Tools v1

Základní bezpečná sada nástrojů pro správu HanzHubu.

## Princip

Agent nemá obecný `run_shell(command)`. Každá schopnost je samostatný nástroj s:

- jednoznačným ID a lidským popisem,
- omezenými vstupy,
- rizikovou třídou,
- pravidlem potvrzení,
- timeoutem a limitem výstupu,
- auditem,
- allowlistem,
- následným health checkem u změn.

## Obsah

- `config/tools.yaml` – registry nástrojů pro tlačítko **Nástroje**
- `config/policy.yaml` – globální bezpečnostní pravidla
- `config/allowed-*.txt` – přesné seznamy povolených služeb, kontejnerů a cest
- `config/protected-paths.txt` – cesty, na které obecné nástroje nesmí
- `scripts/read/` – read-only diagnostika
- `scripts/write/` – omezené zapisující operace
- `scripts/lib/common.sh` – společná validace
- `pending/` – návrhy nových, dosud neaktivních nástrojů

## Doporučená instalace

```bash
sudo install -d -m 0750 /opt/agentAdmin/tools
sudo install -d -m 0750 /etc/agentAdmin
sudo install -d -m 0700 /var/lib/agentAdmin/backups
sudo install -d -m 0700 /var/lib/agentAdmin/pending-tools

sudo cp -a scripts /opt/agentAdmin/tools/
sudo cp config/*.yaml config/*.txt /etc/agentAdmin/
sudo chown -R root:root /opt/agentAdmin/tools /etc/agentAdmin
sudo chmod -R go-w /opt/agentAdmin/tools /etc/agentAdmin
```

Cesty ve `tools.yaml` pak buď řeš relativně vůči balíku, nebo je při importu přepiš na `/opt/agentAdmin/tools/scripts/...`.

## Důležité

1. `allowed-services-restart.txt` a `allowed-containers-restart.txt` jsou záměrně prázdné.
2. Nejdřív přidej službu jen do read allowlistu.
3. Ověř diagnostiku a ručně vyzkoušej health check.
4. Teprve potom ji jednotlivě přidej do restart allowlistu.
5. Obecné mazání souborů v této verzi vůbec není.
6. Aktualizace balíčků jsou pouze read-only simulace.
7. Pracovní scope je výchozím pravidlem zablokovaný.

## Životní cyklus nového nástroje

1. Agent připraví návrh přes `tool.propose`.
2. Návrh zůstane v `pending-tools` a není spustitelný.
3. UI zobrazí:
   - přesný kód,
   - popis,
   - riziko,
   - potřebná oprávnění,
   - testovací plán,
   - SHA-256.
4. Uživatel v chatu potvrdí konkrétní `tool_id` a SHA-256.
5. Backend vytvoří krátkodobý podepsaný approval token.
6. `tool.activate_approved` znovu přepočítá SHA-256 a teprve pak přesune nástroj mezi aktivní.
7. Aktivace i první spuštění se zapíší do auditu.

Agent nesmí:
- sám vydat approval token,
- změnit pending kód po schválení,
- aktivovat jiný hash,
- měnit auditní log,
- přidat obecný shell nebo libovolný SQL nástroj.

## Doporučené první testy

```bash
bash scripts/read/system_overview.sh
bash scripts/read/disk_overview.sh
bash scripts/read/memory_overview.sh
bash scripts/read/failed_services.sh
bash scripts/read/network_overview.sh
```

Nástroje používající allowlist otestuj až po instalaci konfiguračních souborů do `/etc/agentAdmin`.
