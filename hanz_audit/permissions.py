from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

import yaml

from hanz_audit.config import ROOT
from hanz_audit.custom_tools import get_custom_tool
from hanz_audit.file_ops import parse_file_paths
from hanz_audit.local_docs import LOCAL_TOOLS, parse_local_document_paths


class OperationLevel(IntEnum):
    READ = 0
    REVERSIBLE = 1
    SENSITIVE = 2
    DESTRUCTIVE = 3
    FORBIDDEN = 4


@dataclass
class PermissionDecision:
    allowed: bool
    level: OperationLevel
    reason: str = ""


def load_permissions(path: Path | None = None) -> dict:
    cfg_path = path or (ROOT / "config" / "permissions.yaml")
    if not cfg_path.is_file():
        return {}
    with cfg_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_services_inventory(path: Path | None = None) -> dict:
    cfg_path = path or (ROOT / "config" / "services.yaml")
    if not cfg_path.is_file():
        return {}
    with cfg_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("services", {})


def get_service(services: dict, service_id: str) -> dict | None:
    if service_id in services:
        return services[service_id]
    for _key, svc in services.items():
        if svc.get("id") == service_id or svc.get("unit", "").replace(".service", "") == service_id:
            return svc
    return None


def tool_level(permissions: dict, tool_name: str) -> OperationLevel:
    if tool_name == "list_local_documents":
        return OperationLevel.READ
    if tool_name.startswith("get_") or tool_name.startswith("read_") or tool_name.startswith("list_") or tool_name == "run_live_diagnostics":
        return OperationLevel.READ
    if tool_name in permissions.get("level_3_tools", []):
        return OperationLevel.DESTRUCTIVE
    if tool_name in permissions.get("level_2_tools", []):
        return OperationLevel.SENSITIVE
    if tool_name in permissions.get("level_1_tools", []):
        return OperationLevel.REVERSIBLE
    return OperationLevel.FORBIDDEN


def check_service_write(services: dict, service_id: str) -> PermissionDecision:
    svc = get_service(services, service_id)
    if not svc:
        return PermissionDecision(False, OperationLevel.FORBIDDEN, f"Neznámá služba: {service_id}")
    if svc.get("protected"):
        return PermissionDecision(
            False, OperationLevel.FORBIDDEN, f"Služba {service_id} je chráněná (protected)."
        )
    if svc.get("category") == "work":
        return PermissionDecision(
            False, OperationLevel.FORBIDDEN, f"Služba {service_id} je pracovní (work)."
        )
    return PermissionDecision(True, OperationLevel.REVERSIBLE)


def check_tool(
    permissions: dict,
    services: dict,
    tool_name: str,
    arguments: dict,
) -> PermissionDecision:
    if tool_name == "register_custom_tool":
        return PermissionDecision(True, OperationLevel.SENSITIVE)

    if tool_name in LOCAL_TOOLS:
        level = tool_level(permissions, tool_name)
        if tool_name == "create_local_document":
            path_id = arguments.get("path_id", "docs")
            allowed = set(parse_local_document_paths(permissions).keys())
            if path_id not in allowed:
                return PermissionDecision(
                    False, OperationLevel.FORBIDDEN, f"Lokální složka {path_id} není na whitelistu."
                )
        return PermissionDecision(True, level)

    custom = get_custom_tool(tool_name)
    if custom:
        lvl_map = {
            0: OperationLevel.READ,
            1: OperationLevel.REVERSIBLE,
            2: OperationLevel.SENSITIVE,
            3: OperationLevel.DESTRUCTIVE,
        }
        lvl = lvl_map.get(custom.level, OperationLevel.FORBIDDEN)
        if lvl == OperationLevel.FORBIDDEN:
            return PermissionDecision(False, lvl, f"Neplatná úroveň nástroje {tool_name}.")
        return PermissionDecision(True, lvl)

    level = tool_level(permissions, tool_name)
    if level == OperationLevel.FORBIDDEN:
        return PermissionDecision(False, level, f"Nástroj {tool_name} není povolen.")

    if level == OperationLevel.READ:
        return PermissionDecision(True, level)

    if tool_name == "disable_service":
        sid = arguments.get("service_id", "")
        return check_service_write(services, sid)

    if tool_name in ("restart_service", "read_service_logs", "backup_service_config", "stop_service", "start_service", "enable_service"):
        sid = arguments.get("service_id", "")
        return check_service_write(services, sid)

    if tool_name == "restart_docker_container":
        cid = arguments.get("container_id", "")
        for svc in services.values():
            if svc.get("container") == cid:
                if svc.get("protected") or svc.get("category") == "work":
                    return PermissionDecision(
                        False, OperationLevel.FORBIDDEN, f"Kontejner {cid} je chráněný."
                    )
                return PermissionDecision(True, OperationLevel.REVERSIBLE)
        return PermissionDecision(
            False, OperationLevel.FORBIDDEN, f"Kontejner {cid} není v inventáři."
        )

    if tool_name in ("read_file", "write_file", "delete_file"):
        path_id = arguments.get("path_id", "")
        entries = parse_file_paths(permissions)
        entry = entries.get(path_id)
        if not entry:
            return PermissionDecision(False, OperationLevel.FORBIDDEN, f"Cesta {path_id} není na whitelistu.")
        if tool_name == "read_file":
            if not entry.allow_read:
                return PermissionDecision(False, OperationLevel.FORBIDDEN, f"Čtení {path_id} není povoleno.")
            return PermissionDecision(True, OperationLevel.READ)
        if tool_name == "write_file":
            if not entry.allow_write:
                return PermissionDecision(False, OperationLevel.FORBIDDEN, f"Zápis do {path_id} není povolen.")
            return PermissionDecision(True, OperationLevel.SENSITIVE)
        if tool_name == "delete_file":
            if not entry.allow_delete:
                return PermissionDecision(False, OperationLevel.FORBIDDEN, f"Mazání v {path_id} není povoleno.")
            return PermissionDecision(True, OperationLevel.DESTRUCTIVE)

    if tool_name in ("list_old_backups", "prune_old_backups", "delete_files"):
        path_id = arguments.get("path_id", "predicapp_old_db_backups")
        allowed = {d["id"] for d in permissions.get("delete_whitelist", [])}
        if path_id not in allowed:
            return PermissionDecision(False, OperationLevel.FORBIDDEN, f"Cesta {path_id} není na whitelistu.")
        if tool_name == "delete_files":
            return PermissionDecision(True, OperationLevel.DESTRUCTIVE)
        if tool_name == "list_old_backups":
            return PermissionDecision(True, OperationLevel.READ)
        return PermissionDecision(True, OperationLevel.SENSITIVE)

    if tool_name == "prune_cache":
        cache_id = arguments.get("cache_id", "")
        allowed = {c["id"] for c in permissions.get("cache_paths", [])}
        if cache_id not in allowed:
            return PermissionDecision(False, OperationLevel.FORBIDDEN, f"Cache {cache_id} není na whitelistu.")
        return PermissionDecision(True, OperationLevel.REVERSIBLE)

    if level in (OperationLevel.REVERSIBLE, OperationLevel.SENSITIVE):
        return PermissionDecision(True, level)

    return PermissionDecision(False, OperationLevel.FORBIDDEN, "Neznámá operace.")


def contains_forbidden(permissions: dict, text: str) -> str | None:
    import re

    for pattern in permissions.get("forbidden_patterns", []):
        if re.search(pattern, text, re.I):
            return pattern
    return None
