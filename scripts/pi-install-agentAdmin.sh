#!/usr/bin/env bash
# Instalace skriptů agentAdmin na Raspberry Pi (HanzHub).
# Spusť na Pi: sudo bash pi-install-agentAdmin.sh

set -euo pipefail

install -d -m 0750 /opt/agentAdmin/tools/scripts
install -d -m 0750 /opt/agentAdmin/scripts
install -d -m 0750 /opt/agentAdmin/config
install -d -m 0750 /etc/agentAdmin
install -d -m 0700 /var/lib/agentAdmin/backups
install -d -m 0700 /var/lib/agentAdmin/pending-tools

echo "Adresáře /opt/agentAdmin a /etc/agentAdmin jsou připravené."
echo "Zkopíruj sem balíček hanz-agent-tools-v1:"
echo "  sudo cp -a scripts /opt/agentAdmin/tools/"
echo "  sudo cp config/*.yaml config/*.txt /etc/agentAdmin/"
echo "  sudo chown -R root:root /opt/agentAdmin /etc/agentAdmin"
echo "  sudo chmod -R go-w /opt/agentAdmin /etc/agentAdmin"
