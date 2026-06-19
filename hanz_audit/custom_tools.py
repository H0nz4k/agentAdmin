from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from hanz_audit.config import ROOT

CUSTOM_PREFIX = "custom_"
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,48}$")
_UNSAFE_PARAM = re.compile(r"[;\|`$()<>\n\r&]")


@dataclass
class ToolParameter:
    name: str
    type: str = "string"
    description: str = ""
    required: bool = False
    default: object = None


@dataclass
class CustomTool:
    id: str
    name: str
    description: str
    level: int = 0
    enabled: bool = True
    command: str = ""
    parameters: list[ToolParameter] = field(default_factory=list)

    @property
    def openai_name(self) -> str:
        return f"{CUSTOM_PREFIX}{self.id}"


def default_tools_path() -> Path:
    return ROOT / "config" / "custom_tools.yaml"


def load_all_tools(
    path: Path | None = None,
    *,
    v1_pack_path: Path | None = None,
    remote_tools_root: str | None = None,
) -> list[CustomTool]:
    from hanz_audit.v1_tools import load_v1_tools

    user_tools = load_custom_tools(path)
    v1_tools = load_v1_tools(v1_pack_path, remote_tools_root)
    by_id = {t.id: t for t in v1_tools}
    for tool in user_tools:
        by_id[tool.id] = tool
    return list(by_id.values())


def load_custom_tools(path: Path | None = None) -> list[CustomTool]:
    cfg_path = path or default_tools_path()
    if not cfg_path.is_file():
        return []
    with cfg_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    tools: list[CustomTool] = []
    for raw in data.get("tools", []):
        if not isinstance(raw, dict):
            continue
        tool_id = str(raw.get("id", "")).strip()
        if not _ID_RE.match(tool_id):
            continue
        params = []
        for p in raw.get("parameters") or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            params.append(
                ToolParameter(
                    name=str(p["name"]),
                    type=str(p.get("type", "string")),
                    description=str(p.get("description", "")),
                    required=bool(p.get("required", False)),
                    default=p.get("default"),
                )
            )
        tools.append(
            CustomTool(
                id=tool_id,
                name=str(raw.get("name", tool_id)),
                description=str(raw.get("description", "")),
                level=int(raw.get("level", 0)),
                enabled=bool(raw.get("enabled", True)),
                command=str(raw.get("command", "")).strip(),
                parameters=params,
            )
        )
    return tools


def save_custom_tools(tools: list[CustomTool], path: Path | None = None) -> None:
    cfg_path = path or default_tools_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "tools": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "level": t.level,
                "enabled": t.enabled,
                "command": t.command,
                "parameters": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "description": p.description,
                        "required": p.required,
                        **({"default": p.default} if p.default is not None else {}),
                    }
                    for p in t.parameters
                ],
            }
            for t in tools
        ],
    }
    header = (
        "# Vlastní nástroje HanzAgent — co je zde, to agent může volat (prefix custom_).\n"
        "# Úroveň: 0=read-only, 1=vratné, 2=potvrzení v GUI, 3=destruktivní\n"
        "# Parametry v příkazu: {{název}}\n\n"
    )
    cfg_path.write_text(header + yaml.dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def get_custom_tool(
    tool_name: str,
    path: Path | None = None,
    *,
    v1_pack_path: Path | None = None,
    remote_tools_root: str | None = None,
) -> CustomTool | None:
    lookup = tool_name
    if lookup.startswith(CUSTOM_PREFIX):
        lookup = lookup[len(CUSTOM_PREFIX) :]
    for tool in load_all_tools(path, v1_pack_path=v1_pack_path, remote_tools_root=remote_tools_root):
        if tool.id == lookup and tool.enabled:
            return tool
    return None


def _param_properties(tool: CustomTool) -> tuple[dict, list[str]]:
    props: dict = {}
    required: list[str] = []
    for p in tool.parameters:
        schema: dict = {"type": p.type if p.type in ("string", "integer", "number", "boolean") else "string"}
        if p.description:
            schema["description"] = p.description
        if p.default is not None:
            schema["default"] = p.default
        props[p.name] = schema
        if p.required:
            required.append(p.name)
    return props, required


def custom_tool_to_schema(tool: CustomTool) -> dict:
    props, required = _param_properties(tool)
    parameters: dict = {"type": "object", "properties": props}
    if required:
        parameters["required"] = required
    desc = tool.description.strip()
    if tool.name and tool.name != tool.id:
        desc = f"{tool.name}: {desc}"
    return {
        "type": "function",
        "function": {
            "name": tool.openai_name,
            "description": desc,
            "parameters": parameters,
        },
    }


def custom_tools_to_schemas(
    path: Path | None = None,
    *,
    v1_pack_path: Path | None = None,
    remote_tools_root: str | None = None,
) -> list[dict]:
    return [
        custom_tool_to_schema(t)
        for t in load_all_tools(path, v1_pack_path=v1_pack_path, remote_tools_root=remote_tools_root)
        if t.enabled and t.command
    ]


def _validate_param(name: str, value: object, param: ToolParameter) -> str:
    if param.type == "integer":
        try:
            return str(int(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Parametr {name} musí být celé číslo.") from exc
    text = str(value).strip()
    if not text:
        raise ValueError(f"Parametr {name} je prázdný.")
    if _UNSAFE_PARAM.search(text):
        raise ValueError(f"Parametr {name} obsahuje nepovolené znaky.")
    if name in ("unit", "service_id") and not re.match(r"^[a-zA-Z0-9@._-]+(\.service)?$", text):
        raise ValueError(f"Parametr {name} má neplatný formát služby.")
    return text


def render_custom_command(tool: CustomTool, arguments: dict) -> str:
    args = dict(arguments or {})
    for p in tool.parameters:
        if p.name not in args and p.default is not None:
            args[p.name] = p.default
    for p in tool.parameters:
        if p.required and p.name not in args:
            raise ValueError(f"Chybí povinný parametr: {p.name}")

    rendered = tool.command
    for p in tool.parameters:
        placeholder = "{{" + p.name + "}}"
        if placeholder not in rendered:
            continue
        if p.name not in args:
            continue
        safe = _validate_param(p.name, args[p.name], p)
        rendered = rendered.replace(placeholder, shlex.quote(safe))

    if "{{" in rendered or "}}" in rendered:
        missing = re.findall(r"\{\{(\w+)\}\}", rendered)
        raise ValueError(f"Chybí parametry pro příkaz: {', '.join(missing)}")

    return rendered.strip()


def add_custom_tool(entry: dict, path: Path | None = None) -> CustomTool:
    tool_id = str(entry.get("id", "")).strip().lower().replace("-", "_")
    if not _ID_RE.match(tool_id):
        raise ValueError("ID nástroje: malá písmena, číslice, podtržítka (3–49 znaků).")
    command = str(entry.get("command", "")).strip()
    if not command:
        raise ValueError("Příkaz (command) je povinný.")
    params = []
    for p in entry.get("parameters") or []:
        if isinstance(p, dict) and p.get("name"):
            params.append(
                ToolParameter(
                    name=str(p["name"]),
                    type=str(p.get("type", "string")),
                    description=str(p.get("description", "")),
                    required=bool(p.get("required", False)),
                    default=p.get("default"),
                )
            )
    new_tool = CustomTool(
        id=tool_id,
        name=str(entry.get("name", tool_id)),
        description=str(entry.get("description", "")),
        level=int(entry.get("level", 1)),
        enabled=True,
        command=command,
        parameters=params,
    )
    tools = load_custom_tools(path)
    if any(t.id == tool_id for t in tools):
        raise ValueError(f"Nástroj custom_{tool_id} už existuje.")
    tools.append(new_tool)
    save_custom_tools(tools, path)
    return new_tool


def builtin_tool_catalog() -> list[dict]:
    return [
        {"id": "get_memory_overview", "name": "Paměť a procesy", "level": 0, "source": "systém"},
        {"id": "get_disk_overview", "name": "Disk a /opt", "level": 0, "source": "systém"},
        {"id": "get_service_status", "name": "Stav služby", "level": 0, "source": "systém"},
        {"id": "read_service_logs", "name": "Log služby", "level": 0, "source": "systém"},
        {"id": "list_old_backups", "name": "Seznam starých záloh", "level": 0, "source": "systém"},
        {"id": "stop_service", "name": "Stop služby", "level": 1, "source": "systém"},
        {"id": "start_service", "name": "Start služby", "level": 1, "source": "systém"},
        {"id": "restart_service", "name": "Restart služby", "level": 1, "source": "systém"},
        {"id": "disable_service", "name": "Disable služby", "level": 2, "source": "systém"},
        {"id": "enable_service", "name": "Enable služby", "level": 1, "source": "systém"},
        {"id": "prune_cache", "name": "Prune cache", "level": 1, "source": "systém"},
        {"id": "prune_old_backups", "name": "Smazat staré zálohy", "level": 2, "source": "systém"},
        {"id": "register_custom_tool", "name": "Přidat nástroj do knihovny", "level": 2, "source": "systém"},
    ]
