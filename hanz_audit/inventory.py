from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml

from hanz_audit.audit import AuditResult


# unit/service name -> metadata (doplněno heuristikou z auditu)
KNOWN_SERVICES: dict[str, dict] = {
    "predicapp.service": {
        "id": "predicapp",
        "name": "Gold Prediction Hub",
        "path": "/opt/PredicApp",
        "default_port": 8084,
        "runtime": "systemd",
    },
    "hanz-agent.service": {
        "id": "hanz-agent",
        "name": "Hanz Agent (FastAPI + llama)",
        "path": "/opt/hanz-agent",
        "default_port": 8087,
        "runtime": "systemd",
    },
    "appmeteo.service": {
        "id": "hanz-meteo",
        "name": "HanzMeteo Dashboard",
        "path": "/opt/HanzMeteo",
        "runtime": "systemd",
    },
    "app-m.service": {
        "id": "meteo-ble",
        "name": "Hanz Meteo BLE Receiver",
        "path": "/opt/meteo3",
        "runtime": "systemd",
    },
    "lora-hub.service": {
        "id": "lora-hub",
        "name": "Hanz LoRa Hub",
        "path": "/opt/lora2",
        "runtime": "systemd",
    },
    "stream-web.service": {
        "id": "stream-server",
        "name": "Stream Server Web UI",
        "path": "/opt/stream_server",
        "default_port": 8088,
        "runtime": "systemd",
    },
    "whiteboard-hub.service": {
        "id": "whiteboard-hub",
        "name": "Whiteboard HUB",
        "path": "/opt/whiteboard-hub",
        "default_port": 8099,
        "runtime": "systemd",
    },
    "planetum_bot.service": {
        "id": "planetum-bot",
        "name": "Planetum Ticket Bot",
        "path": "/opt/bots/planetum",
        "runtime": "systemd",
    },
    "pihole-FTL.service": {
        "id": "pihole",
        "name": "Pi-hole FTL",
        "path": None,
        "default_port": 53,
        "runtime": "systemd",
    },
    "cloudflared.service": {
        "id": "cloudflared",
        "name": "Cloudflare Tunnel",
        "runtime": "systemd",
    },
    "lcd-info.service": {
        "id": "lcd-info",
        "name": "LCD Info Panel",
        "path": "/opt/lcd",
        "runtime": "systemd",
    },
}

DOCKER_SERVICES: dict[str, dict] = {
    "hanzhub_dashboard": {
        "id": "hanzhub-dashboard",
        "name": "HanzHub Dashboard",
        "default_port": 4001,
        "runtime": "docker",
    },
    "hanzhub_health": {
        "id": "hanzhub-health",
        "name": "HanzHub Health",
        "runtime": "docker",
    },
    "influxdb": {
        "id": "influxdb",
        "name": "InfluxDB",
        "default_port": 8086,
        "runtime": "docker",
    },
    "grafana": {
        "id": "grafana",
        "name": "Grafana",
        "runtime": "docker",
    },
    "caddy": {
        "id": "caddy",
        "name": "Caddy reverse proxy",
        "default_port": 8081,
        "runtime": "docker",
    },
}


def _section(result: AuditResult, title: str) -> str:
    for s in result.sections:
        if s.title == title:
            return s.output if not s.skipped else ""
    return ""


def _git_paths(git_output: str) -> set[str]:
    return {p.strip() for p in git_output.splitlines() if p.strip()}


def _has_git(path: str | None, git_paths: set[str]) -> bool:
    if not path:
        return False
    if path in git_paths:
        return True
    explicit = {g for g in git_paths if g not in ("/opt", "")}
    return any(path == g or path.startswith(g + "/") for g in explicit)


def _parse_running_units(systemd_output: str) -> set[str]:
    units: set[str] = set()
    for line in systemd_output.splitlines():
        m = re.match(r"^\s*(\S+\.service)\s+", line)
        if m:
            units.add(m.group(1))
    return units


def _parse_docker_running(docker_output: str) -> list[str]:
    names: list[str] = []
    for line in docker_output.splitlines()[1:]:
        parts = line.split()
        if parts:
            names.append(parts[0])
    return names


def build_inventory(result: AuditResult) -> dict:
    git_paths = _git_paths(_section(result, "Git repozitáře"))
    running_units = _parse_running_units(_section(result, "Systemd — běžící služby"))
    docker_names = _parse_docker_running(_section(result, "Docker — kontejnery"))
    ts = result.timestamp.astimezone().isoformat()

    services: dict = {}

    for unit, meta in KNOWN_SERVICES.items():
        sid = meta["id"]
        path = meta.get("path")
        services[sid] = {
            "name": meta["name"],
            "category": "personal",
            "owner": "hanz",
            "runtime": meta.get("runtime", "systemd"),
            "unit": unit,
            "status": "running" if unit in running_units else "unknown",
            "path": path,
            "port": meta.get("default_port"),
            "has_git": _has_git(path, git_paths),
            "protected": False,
        }

    for cname, meta in DOCKER_SERVICES.items():
        sid = meta["id"]
        services[sid] = {
            "name": meta["name"],
            "category": "personal",
            "owner": "hanz",
            "runtime": "docker",
            "container": cname,
            "status": "running" if cname in docker_names else "unknown",
            "port": meta.get("default_port"),
            "has_git": False,
            "protected": False,
        }

    return {
        "generated_at": ts,
        "host": result.host,
        "note": "Auto-generováno z auditu. Uprav category, owner a protected ručně.",
        "services": services,
    }


def save_inventory(inventory: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# HanzHub inventura služeb\n"
        f"# Vygenerováno: {inventory.get('generated_at', '')}\n"
        "# U work služeb nastav protected: true\n\n"
    )
    body = yaml.dump(
        inventory,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=100,
    )
    path.write_text(header + body, encoding="utf-8")
    return path


def load_existing_inventory(path: Path) -> dict:
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^#.*\n", "", text, flags=re.MULTILINE)
    return yaml.safe_load(text) or {}


def merge_inventory(new: dict, existing: dict) -> dict:
    """Zachová ruční úpravy category/owner/protected z existujícího souboru."""
    if not existing.get("services"):
        return new
    for sid, svc in new.get("services", {}).items():
        old = existing.get("services", {}).get(sid, {})
        for key in ("category", "owner", "protected", "note"):
            if key in old and old[key] is not None:
                svc[key] = old[key]
    return new
