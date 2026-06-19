#!/usr/bin/env bash
# Instalace / aktualizace agentAdmin na Raspberry Pi.
# Spouští se automaticky z deploy_pi.py, nebo ručně:
#   sudo bash pi-install-agentAdmin.sh /home/hanz/agentAdmin-staging
#
# Očekává strukturu staging:
#   tools/scripts/   — skripty z hanz-agent-tools-v1
#   etc/             — config/*.yaml a *.txt z balíčku
#   custom-scripts/  — volitelné vlastní skripty (→ /opt/agentAdmin/scripts)

set -euo pipefail

STAGING="${1:-}"
if [[ -z "$STAGING" || ! -d "$STAGING" ]]; then
  echo "Použití: sudo bash $0 /cesta/k/staging" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Spusť jako root: sudo bash $0 $STAGING" >&2
  exit 1
fi

TOOLS_SRC="$STAGING/tools/scripts"
ETC_SRC="$STAGING/etc"
CUSTOM_SRC="$STAGING/custom-scripts"

if [[ ! -d "$TOOLS_SRC" ]]; then
  echo "Chybí $TOOLS_SRC — nejdřív spusť deploy z PC." >&2
  exit 1
fi

install -d -m 0750 /opt/agentAdmin/tools
install -d -m 0770 /opt/agentAdmin/scripts
install -d -m 0750 /opt/agentAdmin/config
install -d -m 0750 /etc/agentAdmin
install -d -m 0700 /var/lib/agentAdmin/backups
install -d -m 0700 /var/lib/agentAdmin/pending-tools

PI_USER="${SUDO_USER:-hanz}"

echo "-> Kopiruji nastroje do /opt/agentAdmin/tools/scripts …"
rm -rf /opt/agentAdmin/tools/scripts
cp -a "$TOOLS_SRC" /opt/agentAdmin/tools/scripts

if [[ -d "$ETC_SRC" ]]; then
  echo "-> Kopiruji konfiguraci do /etc/agentAdmin …"
  cp -a "$ETC_SRC"/. /etc/agentAdmin/
fi

if [[ -d "$CUSTOM_SRC" ]] && [[ -n "$(ls -A "$CUSTOM_SRC" 2>/dev/null || true)" ]]; then
  echo "-> Vlastni skripty do /opt/agentAdmin/scripts …"
  cp -a "$CUSTOM_SRC"/. /opt/agentAdmin/scripts/
fi

chown -R root:"$PI_USER" /opt/agentAdmin/tools /etc/agentAdmin
chown -R "$PI_USER":"$PI_USER" /opt/agentAdmin/scripts
chmod -R u=rwX,g=rX,o= /opt/agentAdmin/tools
chmod -R u=rwX,g=,o= /opt/agentAdmin/scripts
find /opt/agentAdmin/tools/scripts -type f -name '*.sh' -exec chmod 750 {} \;
find /opt/agentAdmin/scripts -type f -name '*.sh' -exec chmod 770 {} \; 2>/dev/null || true
find /etc/agentAdmin -type f -exec chmod 640 {} \;
find /etc/agentAdmin -type d -exec chmod 750 {} \;

# Nouzové vypnutí zápisu — soubor vytvoříš ručně: sudo touch /etc/agentAdmin/disable-writes
if [[ -f /etc/agentAdmin/disable-writes ]]; then
  echo "WARN: /etc/agentAdmin/disable-writes existuje — zapis z GUI je vypnuty."
fi

echo "-> Overeni: system_overview.sh …"
bash /opt/agentAdmin/tools/scripts/read/system_overview.sh 2>&1 | head -5 || true
echo ""
echo "OK agentAdmin nainstalovan."
echo "  Nastroje:  /opt/agentAdmin/tools/scripts"
echo "  Config:    /etc/agentAdmin"
echo "  Vlastni:   /opt/agentAdmin/scripts"
