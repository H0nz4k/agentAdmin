from __future__ import annotations

from pathlib import Path

import yaml

from hanz_audit.config import ROOT


def load_agent_config(path: Path | None = None) -> dict:
    cfg_path = path or (ROOT / "config" / "agent.yaml")
    if not cfg_path.is_file():
        return _default_agent_config()
    with cfg_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def _default_agent_config() -> dict:
    return {
        "agent": {"name": "HanzAgent", "language": "cs"},
        "behaviors": [],
        "response_format": {},
        "severity_labels": {
            "critical": "KRITICKÉ",
            "warning": "POZOR",
            "info": "INFO",
            "ok": "OK",
        },
        "overview": {"sections": []},
    }


def build_system_prompt(agent: dict, info_context: str = "") -> str:
    parts: list[str] = []

    role = agent.get("role", "")
    if role:
        parts.append(role.strip())

    meta = agent.get("agent", {})
    if meta.get("server_name"):
        parts.append(f"Server: {meta['server_name']} ({meta.get('server_host', '')})")

    notes = agent.get("context_notes", "")
    if notes:
        parts.append(f"\nKontext:\n{notes.strip()}")

    for beh in agent.get("behaviors", []):
        if not beh.get("enabled", True):
            continue
        prompt = beh.get("prompt", "").strip()
        if prompt:
            label = beh.get("id", "pravidlo")
            parts.append(f"\n[{label}]\n{prompt}")

    fmt = agent.get("response_format", {})
    if fmt:
        parts.append("\nFormát odpovědí:")
        if fmt.get("tone"):
            parts.append(f"- Tón: {fmt['tone']}")
        if fmt.get("language"):
            parts.append(f"- Jazyk: {fmt['language']}")
        structure = fmt.get("structure", [])
        if structure:
            parts.append("- Struktura odpovědi:")
            for i, item in enumerate(structure, 1):
                parts.append(f"  {i}. {item}")
        if fmt.get("max_bullets_per_section"):
            parts.append(
                f"- Max {fmt['max_bullets_per_section']} odrážek v jedné sekci."
            )

    if info_context:
        parts.append(f"\n---\nPoznámky uživatele (info.txt):\n{info_context}")

    return "\n".join(parts)


def enabled_overview_sections(agent: dict) -> list[dict]:
    overview = agent.get("overview", {})
    sections = overview.get("sections", [])
    return [s for s in sections if s.get("enabled", True)]


def severity_label(agent: dict, severity: str) -> str:
    labels = agent.get("severity_labels", {})
    return labels.get(severity, severity.upper())
