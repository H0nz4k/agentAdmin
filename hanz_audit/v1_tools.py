from __future__ import annotations

from pathlib import Path

import yaml

from hanz_audit.config import ROOT
from hanz_audit.custom_tools import CustomTool, ToolParameter

V1_PREFIX = "v1_"
RISK_TO_LEVEL = {
    "read_only": 0,
    "reversible": 1,
    "destructive": 3,
    "forbidden": 4,
}


def default_v1_pack_path() -> Path:
    return ROOT / "hanz-agent-tools-v1"


def default_remote_scripts_base() -> str:
    return "/opt/agentAdmin/tools"


def _risk_level(entry: dict) -> int:
    base = RISK_TO_LEVEL.get(str(entry.get("risk", "read_only")), 2)
    approval = str(entry.get("approval", "never"))
    if approval in ("always", "exact"):
        return max(base, 2)
    if base >= 4:
        return 4
    return base


def _convert_input(raw: dict) -> ToolParameter:
    ptype = str(raw.get("type", "string"))
    if ptype in ("allowlist", "allowlist_path", "path"):
        ptype = "string"
    elif ptype == "enum":
        ptype = "string"
    elif ptype not in ("string", "integer", "number", "boolean"):
        ptype = "string"
    desc = str(raw.get("description", "") or "")
    if raw.get("source"):
        desc = f"{desc} (allowlist: {raw['source']})".strip()
    if raw.get("values"):
        desc = f"{desc} Hodnoty: {', '.join(str(v) for v in raw['values'])}".strip()
    default = raw.get("default")
    return ToolParameter(
        name=str(raw["name"]),
        type=ptype,
        description=desc,
        required=bool(raw.get("required", False)),
        default=default,
    )


def v1_entry_to_custom(entry: dict, remote_scripts_base: str) -> CustomTool | None:
    handler = entry.get("handler") or {}
    if handler.get("type") != "script":
        return None
    script_rel = str(handler.get("path", "")).strip()
    if not script_rel:
        return None

    tool_id = str(entry.get("id", "")).replace(".", "_")
    if not tool_id:
        return None

    level = _risk_level(entry)
    if level >= 4 or not entry.get("enabled", True):
        return None

    params = [_convert_input(p) for p in entry.get("inputs") or [] if isinstance(p, dict) and p.get("name")]
    full_script = f"{remote_scripts_base.rstrip('/')}/{script_rel}"
    parts = ["bash", full_script]
    for p in params:
        parts.append("{{" + p.name + "}}")
    command = " ".join(parts)
    if entry.get("requires_sudo"):
        command = "sudo " + command

    label = str(entry.get("label", tool_id))
    desc = str(entry.get("description", "")).strip()
    category = str(entry.get("category", ""))
    if category:
        desc = f"[{category}] {desc}"

    return CustomTool(
        id=f"{V1_PREFIX}{tool_id}",
        name=label,
        description=desc,
        level=level,
        enabled=True,
        command=command,
        parameters=params,
    )


def load_v1_tools(
    pack_path: Path | None = None,
    remote_scripts_base: str | None = None,
) -> list[CustomTool]:
    pack = pack_path or default_v1_pack_path()
    registry = pack / "config" / "tools.yaml"
    if not registry.is_file():
        return []
    with registry.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    remote = remote_scripts_base or default_remote_scripts_base()
    tools: list[CustomTool] = []
    for entry in data.get("tools") or []:
        if not isinstance(entry, dict):
            continue
        converted = v1_entry_to_custom(entry, remote)
        if converted:
            tools.append(converted)
    return tools


def v1_tool_catalog(pack_path: Path | None = None) -> list[dict]:
    return [
        {
            "id": t.id,
            "name": t.name,
            "level": t.level,
            "source": "v1 balíček",
            "openai_name": f"custom_{t.id}",
        }
        for t in load_v1_tools(pack_path)
    ]


def v1_internal_tools(pack_path: Path | None = None) -> list[dict]:
    """Nástroje z v1 registry, které zatím nemají script handler (internal)."""
    pack = pack_path or default_v1_pack_path()
    registry = pack / "config" / "tools.yaml"
    if not registry.is_file():
        return []
    with registry.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    items: list[dict] = []
    for entry in data.get("tools") or []:
        handler = (entry or {}).get("handler") or {}
        if handler.get("type") == "internal":
            items.append(
                {
                    "id": entry.get("id"),
                    "label": entry.get("label"),
                    "internal": handler.get("name"),
                    "note": "Zatím neimplementováno v desktop GUI — plánováno.",
                }
            )
    return items
