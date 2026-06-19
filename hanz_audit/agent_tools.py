from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from hanz_audit.custom_tools import (
    CUSTOM_PREFIX,
    custom_tools_to_schemas,
    get_custom_tool,
    render_custom_command,
)
from hanz_audit.file_ops import (
    file_path_ids_for,
    parse_file_paths,
    resolve_remote_path,
    shell_quote,
)
from hanz_audit.local_docs import local_tool_schemas
from hanz_audit.permissions import (
    OperationLevel,
    get_service,
    load_permissions,
    load_services_inventory,
)
from hanz_audit.redact import redact_secrets
from hanz_audit.ssh_client import SSHClient


def _custom_result_level(
    tool_name: str,
    *,
    tools_path=None,
    v1_pack_path=None,
    remote_tools_root=None,
) -> OperationLevel:
    custom = get_custom_tool(
        tool_name,
        tools_path,
        v1_pack_path=v1_pack_path,
        remote_tools_root=remote_tools_root,
    )
    if not custom:
        return OperationLevel.READ
    mapping = {
        0: OperationLevel.READ,
        1: OperationLevel.REVERSIBLE,
        2: OperationLevel.SENSITIVE,
        3: OperationLevel.DESTRUCTIVE,
    }
    return mapping.get(custom.level, OperationLevel.READ)


@dataclass
class ToolResult:
    ok: bool
    output: str
    level: OperationLevel = OperationLevel.READ


OPENAI_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_memory_overview",
            "description": "Read-only: paměť, swap, top procesy podle RAM.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_disk_overview",
            "description": "Read-only: df -h a největší složky v /opt.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service_status",
            "description": "Read-only: stav systemd služby nebo docker kontejneru z inventáře.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {
                        "type": "string",
                        "description": "ID služby z inventáře, např. predicapp, hanz-agent, grafana",
                    }
                },
                "required": ["service_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_service_logs",
            "description": "Read-only: poslední řádky logu služby (max 200).",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "string"},
                    "lines": {"type": "integer", "description": "Počet řádků, default 50"},
                },
                "required": ["service_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_service",
            "description": "Úroveň 1: zastaví systemd službu (systemctl stop).",
            "parameters": {
                "type": "object",
                "properties": {"service_id": {"type": "string"}},
                "required": ["service_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_service",
            "description": "Úroveň 1: spustí systemd službu (systemctl start).",
            "parameters": {
                "type": "object",
                "properties": {"service_id": {"type": "string"}},
                "required": ["service_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "disable_service",
            "description": "Úroveň 2: vypne autostart služby (systemctl disable). S stop_now=true i zastaví běžící instanci.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "string"},
                    "stop_now": {"type": "boolean", "default": True},
                },
                "required": ["service_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enable_service",
            "description": "Úroveň 1: zapne autostart služby (systemctl enable). S start_now=true i spustí službu.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "string"},
                    "start_now": {"type": "boolean", "default": True},
                },
                "required": ["service_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_service",
            "description": "Úroveň 1: restart systemd služby (pouze personal, ne protected).",
            "parameters": {
                "type": "object",
                "properties": {"service_id": {"type": "string"}},
                "required": ["service_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_docker_disk_overview",
            "description": (
                "Read-only: docker system df + největší images a logy kontejnerů. "
                "Volej PŘED jakýmkoli prune — ukáže co skutečně zabírá místo."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_docker_container",
            "description": (
                "Úroveň 1: restart docker kontejneru z inventáře. "
                "container_id = jméno kontejneru (docker ps Names), NE název image (např. lissy93/dashy:latest)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"container_id": {"type": "string"}},
                "required": ["container_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_prune_dangling",
            "description": (
                "Úroveň 1: smaže jen DANGLING (untagged) docker images. "
                "Pokud vrátí Total reclaimed space: 0B, nic neuvolnilo — NEOPAKUJ. "
                "Nejdřív get_docker_disk_overview; větší úspora může být v PredicApp zálohách nebo docker system prune."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_system_prune",
            "description": (
                "Úroveň 2: docker system prune -f — nevyužívané sítě, build cache, stopped kontejnery "
                "(NE maže images používané kontejnery). Vyžaduje potvrzení."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "journal_vacuum",
            "description": "Úroveň 2: journalctl --vacuum-size (vyžaduje potvrzení).",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_size": {"type": "string", "description": "např. 500M", "default": "500M"}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prune_cache",
            "description": "Úroveň 1: smaže cache z whitelistu (pycache, pyc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "cache_id": {
                        "type": "string",
                        "enum": ["predicapp_pycache", "predicapp_pyc"],
                    }
                },
                "required": ["cache_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_old_backups",
            "description": "Read-only: najde soubory z whitelistu starší než N dní — bez mazání. Vrátí seznam a celkovou velikost.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path_id": {"type": "string", "enum": ["predicapp_old_db_backups"]},
                    "min_age_days": {"type": "integer", "default": 30},
                },
                "required": ["path_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prune_old_backups",
            "description": "Úroveň 2: smaže staré DB zálohy PredicApp (>30 dní, vyžaduje potvrzení).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path_id": {"type": "string", "enum": ["predicapp_old_db_backups"]},
                    "min_age_days": {"type": "integer", "default": 30},
                },
                "required": ["path_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backup_service_config",
            "description": "Úroveň 1: tar záloha konfigurace před změnou.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path_id": {
                        "type": "string",
                        "enum": ["predicapp_env", "cloudflared_service", "agentadmin_config"],
                    }
                },
                "required": ["path_id"],
            },
        },
    },
]


def _file_tool_schemas(permissions: dict) -> list[dict]:
    read_ids = file_path_ids_for(permissions, read=True)
    write_ids = file_path_ids_for(permissions, write=True)
    delete_ids = file_path_ids_for(permissions, delete=True)
    schemas: list[dict] = []
    if read_ids:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": (
                        "Read-only: přečte soubor z whitelistu config/permissions.yaml → file_paths. "
                        "U adresáře uveď relative_path (soubor uvnitř kořene)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path_id": {"type": "string", "enum": read_ids},
                            "relative_path": {
                                "type": "string",
                                "description": "Relativní cesta v rámci path_id; u jednoho souboru nech prázdné.",
                            },
                        },
                        "required": ["path_id"],
                    },
                },
            }
        )
    if write_ids:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": (
                        "Úroveň 2: vytvoří nebo přepíše soubor na Pi (vyžaduje potvrzení v GUI). "
                        "Před rizikovou změnou zvaž backup_service_config."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path_id": {"type": "string", "enum": write_ids},
                            "relative_path": {
                                "type": "string",
                                "description": "Relativní cesta; u jednoho souboru nech prázdné.",
                            },
                            "content": {"type": "string", "description": "Celý obsah souboru (UTF-8)."},
                            "create_only": {
                                "type": "boolean",
                                "description": "True = selhat pokud soubor už existuje.",
                                "default": False,
                            },
                        },
                        "required": ["path_id", "content"],
                    },
                },
            }
        )
    if delete_ids:
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": "delete_file",
                    "description": (
                        "Úroveň 3: smaže jeden soubor z whitelistu (vyžaduje potvrzení v GUI)."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path_id": {"type": "string", "enum": delete_ids},
                            "relative_path": {
                                "type": "string",
                                "description": "Relativní cesta k souboru; u jednoho souboru nech prázdné.",
                            },
                        },
                        "required": ["path_id"],
                    },
                },
            }
        )
    return schemas


REGISTER_CUSTOM_TOOL_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "register_custom_tool",
        "description": (
            "Po potvrzení uživatele přidá nový nástroj do knihovny config/custom_tools.yaml. "
            "Použij jen když uživatel výslovně souhlasí se zápisem nového příkazu/skriptu."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "ID bez prefixu, např. hub_my_script"},
                "name": {"type": "string", "description": "Lidský název"},
                "description": {"type": "string", "description": "Popis pro agenta"},
                "level": {
                    "type": "integer",
                    "description": "0=read, 1=vratné, 2=potvrzení, 3=destruktivní",
                    "default": 0,
                },
                "command": {"type": "string", "description": "Shell příkaz na Pi, parametry jako {{name}}"},
                "parameters": {
                    "type": "array",
                    "description": "Volitelné parametry nástroje",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "description": {"type": "string"},
                            "required": {"type": "boolean"},
                            "default": {},
                        },
                    },
                },
            },
            "required": ["id", "name", "description", "command"],
        },
    },
}


def get_all_tool_schemas(
    tools_path=None,
    *,
    v1_pack_path=None,
    remote_tools_root=None,
) -> list[dict]:
    permissions = load_permissions()
    return (
        list(OPENAI_TOOL_SCHEMAS)
        + local_tool_schemas(permissions)
        + _file_tool_schemas(permissions)
        + custom_tools_to_schemas(
            tools_path,
            v1_pack_path=v1_pack_path,
            remote_tools_root=remote_tools_root,
        )
        + [REGISTER_CUSTOM_TOOL_SCHEMA]
    )


# zpětná kompatibilita
OPENAI_TOOL_SCHEMAS_WITH_CUSTOM = get_all_tool_schemas


def _run(ssh: SSHClient, cmd: str, timeout: int = 120) -> tuple[str, int]:
    out, err, code = ssh.run(cmd, timeout=timeout)
    combined = out
    if err:
        combined = f"{out}\n[stderr]\n{err}".strip() if out else err
    return redact_secrets(combined), code


def _resolve_unit(services: dict, service_id: str) -> tuple[str | None, str | None, str]:
    svc = get_service(services, service_id)
    if not svc:
        return None, None, f"Služba {service_id} není v inventáři."
    unit = svc.get("unit")
    container = svc.get("container")
    runtime = svc.get("runtime", "systemd")
    return unit, container, runtime


def _resolve_delete_entry(permissions: dict, path_id: str) -> tuple[dict | None, str]:
    entry = next((d for d in permissions.get("delete_whitelist", []) if d["id"] == path_id), None)
    if not entry:
        return None, f"Neznámý path_id: {path_id}"
    return entry, ""


def _old_backups_cmd(entry: dict, min_days: int, *, delete: bool) -> str:
    path = entry["path"]
    pattern = entry.get("pattern", "*")
    find_base = f"find {path} -name '{pattern}' -type f -mtime +{min_days}"
    if delete:
        return f"{find_base} -print -delete 2>&1 | head -30"
    return (
        f"echo 'Soubory starší než {min_days} dní v {path}:'; "
        f"{find_base} -ls 2>/dev/null | head -80; "
        f"echo '---'; "
        f"{find_base} -exec du -ch {{}} + 2>/dev/null | tail -1 || echo 'Celkem: 0 (žádné soubory)'"
    )


def execute_tool(
    ssh: SSHClient,
    tool_name: str,
    arguments: dict,
    *,
    log_command: Callable[[str], None] | None = None,
    tools_path=None,
    v1_pack_path=None,
    remote_tools_root=None,
) -> ToolResult:
    permissions = load_permissions()
    services = load_services_inventory()
    args = arguments or {}

    def run(cmd: str, timeout: int = 120) -> tuple[str, int]:
        if log_command:
            log_command(cmd)
        return _run(ssh, cmd, timeout=timeout)

    if tool_name.startswith(CUSTOM_PREFIX) or get_custom_tool(
        tool_name,
        tools_path,
        v1_pack_path=v1_pack_path,
        remote_tools_root=remote_tools_root,
    ):
        custom = get_custom_tool(
            tool_name,
            tools_path,
            v1_pack_path=v1_pack_path,
            remote_tools_root=remote_tools_root,
        )
        if not custom:
            return ToolResult(False, f"Neznámý vlastní nástroj: {tool_name}")
        try:
            cmd = render_custom_command(custom, args)
        except ValueError as exc:
            return ToolResult(
                False,
                str(exc),
                _custom_result_level(
                    tool_name,
                    tools_path=tools_path,
                    v1_pack_path=v1_pack_path,
                    remote_tools_root=remote_tools_root,
                ),
            )
        out, code = run(cmd)
        return ToolResult(
            code == 0,
            out,
            _custom_result_level(
                tool_name,
                tools_path=tools_path,
                v1_pack_path=v1_pack_path,
                remote_tools_root=remote_tools_root,
            ),
        )

    if tool_name == "get_memory_overview":
        out, code = run(
            "free -h && echo '---' && ps aux --sort=-%mem 2>/dev/null | head -15",
        )
        return ToolResult(code == 0, out)

    if tool_name == "get_disk_overview":
        out, code = run(
            "df -h && echo '---' && du -xhd1 /opt 2>/dev/null | sort -hr | head -15",
        )
        return ToolResult(code == 0, out)

    if tool_name == "get_docker_disk_overview":
        out, code = run(
            "docker system df 2>&1 && echo '--- IMAGES (size) ---' && "
            "docker images --format 'table {{.Repository}}\\t{{.Tag}}\\t{{.Size}}\\t{{.ID}}' 2>&1 | head -25 && "
            "echo '--- CONTAINERS ---' && "
            "docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Size}}' 2>&1 | head -20"
        )
        return ToolResult(code == 0, out[:8000])

    if tool_name == "get_service_status":
        unit, container, runtime = _resolve_unit(services, args["service_id"])
        if not unit and not container:
            return ToolResult(False, runtime)
        if runtime == "docker" and container:
            out, code = run(f"docker inspect --format '{{{{.State.Status}}}}' {container} 2>&1")
            stats, _ = run(f"docker stats --no-stream {container} 2>/dev/null")
            return ToolResult(code == 0, f"status={out}\n{stats}")
        out, code = run(
            f"systemctl is-active {unit} 2>&1 && systemctl show {unit} "
            f"--property=ActiveState,SubState,MemoryCurrent,MainPID",
        )
        return ToolResult(code == 0, out)

    if tool_name == "read_service_logs":
        unit, container, runtime = _resolve_unit(services, args["service_id"])
        lines = min(int(args.get("lines", 50)), permissions.get("policy", {}).get("log_max_lines", 200))
        if runtime == "docker" and container:
            out, code = run(f"docker logs --tail {lines} {container} 2>&1")
        elif unit:
            out, code = run(f"journalctl -u {unit} -n {lines} --no-pager 2>&1")
        else:
            return ToolResult(False, runtime)
        return ToolResult(code == 0, out[:8000])

    if tool_name == "stop_service":
        unit, _, msg = _resolve_unit(services, args["service_id"])
        if not unit:
            return ToolResult(False, msg)
        out_before, _ = run(f"systemctl is-active {unit} 2>&1")
        out, code = run(f"systemctl stop {unit} 2>&1")
        out_after, _ = run(f"systemctl is-active {unit} 2>&1")
        stopped = out_after.strip() in ("inactive", "failed", "unknown")
        return ToolResult(
            code == 0 and stopped,
            f"Před: {out_before}\nStop: {out}\nPo: {out_after}",
            OperationLevel.REVERSIBLE,
        )

    if tool_name == "start_service":
        unit, _, msg = _resolve_unit(services, args["service_id"])
        if not unit:
            return ToolResult(False, msg)
        out_before, _ = run(f"systemctl is-active {unit} 2>&1")
        out, code = run(f"systemctl start {unit} 2>&1")
        out_after, _ = run(f"systemctl is-active {unit} 2>&1")
        return ToolResult(
            code == 0 and out_after.strip() == "active",
            f"Před: {out_before}\nStart: {out}\nPo: {out_after}",
            OperationLevel.REVERSIBLE,
        )

    if tool_name == "disable_service":
        unit, _, msg = _resolve_unit(services, args["service_id"])
        if not unit:
            return ToolResult(False, msg)
        stop_now = bool(args.get("stop_now", True))
        flag = " --now" if stop_now else ""
        out_before, _ = run(f"systemctl is-enabled {unit} 2>&1; systemctl is-active {unit} 2>&1")
        out, code = run(f"systemctl disable{flag} {unit} 2>&1")
        out_after, _ = run(f"systemctl is-enabled {unit} 2>&1; systemctl is-active {unit} 2>&1")
        return ToolResult(
            code == 0,
            f"Před: {out_before}\nDisable: {out}\nPo: {out_after}",
            OperationLevel.SENSITIVE,
        )

    if tool_name == "enable_service":
        unit, _, msg = _resolve_unit(services, args["service_id"])
        if not unit:
            return ToolResult(False, msg)
        start_now = bool(args.get("start_now", True))
        flag = " --now" if start_now else ""
        out_before, _ = run(f"systemctl is-enabled {unit} 2>&1; systemctl is-active {unit} 2>&1")
        out, code = run(f"systemctl enable{flag} {unit} 2>&1")
        out_after, _ = run(f"systemctl is-enabled {unit} 2>&1; systemctl is-active {unit} 2>&1")
        return ToolResult(
            code == 0,
            f"Před: {out_before}\nEnable: {out}\nPo: {out_after}",
            OperationLevel.REVERSIBLE,
        )

    if tool_name == "restart_service":
        unit, _, msg = _resolve_unit(services, args["service_id"])
        if not unit:
            return ToolResult(False, msg)
        out_before, _ = run(f"systemctl is-active {unit} 2>&1")
        out, code = run(f"systemctl restart {unit} 2>&1")
        out_after, _ = run(f"systemctl is-active {unit} 2>&1")
        return ToolResult(
            code == 0 and out_after.strip() == "active",
            f"Před: {out_before}\nRestart: {out}\nPo: {out_after}",
            OperationLevel.REVERSIBLE,
        )

    if tool_name == "restart_docker_container":
        cid = args["container_id"]
        out, code = run(f"docker restart {cid} 2>&1")
        status, _ = run(f"docker inspect --format '{{{{.State.Status}}}}' {cid}")
        return ToolResult(code == 0 and status.strip() == "running", out, OperationLevel.REVERSIBLE)

    if tool_name == "docker_prune_dangling":
        out, code = run("docker image prune -f 2>&1")
        if code == 0 and ("0B" in out or "0 B" in out):
            out += (
                "\n\n→ Žádné dangling images k odstranění. "
                "Nepokoušej se prune opakovat. "
                "Zavolej get_docker_disk_overview, custom_hub_opt_sizes, list_old_backups "
                "nebo custom_v1_disk_overview — místo obvykle žere PredicApp / zálohy / journal."
            )
        return ToolResult(code == 0, out, OperationLevel.REVERSIBLE)

    if tool_name == "docker_system_prune":
        out, code = run("docker system prune -f 2>&1")
        return ToolResult(code == 0, out, OperationLevel.SENSITIVE)

    if tool_name == "journal_vacuum":
        size = args.get("max_size", "500M")
        out, code = run(f"sudo journalctl --vacuum-size={size} 2>&1")
        return ToolResult(code == 0, out, OperationLevel.SENSITIVE)

    if tool_name == "prune_cache":
        cache_id = args["cache_id"]
        caches = {c["id"]: c for c in permissions.get("cache_paths", [])}
        entry = caches.get(cache_id)
        if not entry:
            return ToolResult(False, f"Neznámá cache: {cache_id}")
        base = entry["path"]
        find_expr = entry.get("find", "")
        if not find_expr:
            return ToolResult(False, "Tato cache vyžaduje jiný nástroj (docker builder prune).")
        cmd = f"find {base} {find_expr} -print -delete 2>&1 | head -50"
        out, code = run(cmd)
        return ToolResult(code == 0, out or "Nic ke smazání.", OperationLevel.REVERSIBLE)

    if tool_name == "list_old_backups":
        path_id = args.get("path_id", "predicapp_old_db_backups")
        min_days = int(args.get("min_age_days", 30))
        entry, err = _resolve_delete_entry(permissions, path_id)
        if not entry:
            return ToolResult(False, err)
        out, code = run(_old_backups_cmd(entry, min_days, delete=False))
        if code != 0 and not out.strip():
            return ToolResult(False, out or f"Adresář {entry['path']} neexistuje nebo je prázdný.")
        return ToolResult(True, out or "Žádné soubory starší než zadaný limit.", OperationLevel.READ)

    if tool_name == "prune_old_backups":
        path_id = args.get("path_id", "predicapp_old_db_backups")
        min_days = int(args.get("min_age_days", 30))
        entry, err = _resolve_delete_entry(permissions, path_id)
        if not entry:
            return ToolResult(False, err)
        out, code = run(_old_backups_cmd(entry, min_days, delete=True))
        return ToolResult(code == 0, out or "Nic ke smazání.", OperationLevel.SENSITIVE)

    if tool_name == "backup_service_config":
        path_id = args["path_id"]
        configs = {c["id"]: c for c in permissions.get("config_backup_paths", [])}
        entry = configs.get(path_id)
        if not entry:
            return ToolResult(False, f"Neznámá config cesta: {path_id}")
        backup_dir = permissions.get("policy", {}).get("backup_dir", "/opt/agentAdmin/backups")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        src = entry["path"]
        dest = f"{backup_dir}/{path_id}_{ts}.tar.gz"
        exists_out, exists_code = run(f"test -e {src}")
        if exists_code != 0:
            return ToolResult(
                False,
                f"Cesta {src} na Pi neexistuje — záloha není možná. Použij jiný nástroj.",
                OperationLevel.REVERSIBLE,
            )
        out, code = run(
            f"mkdir -p {backup_dir} && tar -czf {dest} -C $(dirname {src}) $(basename {src}) "
            f"2>&1 && ls -lh {dest}",
        )
        return ToolResult(code == 0, out, OperationLevel.REVERSIBLE)

    if tool_name == "read_file":
        path_id = args["path_id"]
        entries = parse_file_paths(permissions)
        entry = entries.get(path_id)
        if not entry or not entry.allow_read:
            return ToolResult(False, f"Cesta {path_id} není povolená pro čtení.")
        full, err = resolve_remote_path(entry, args.get("relative_path", ""))
        if not full:
            return ToolResult(False, err)
        q = shell_quote(full)
        max_b = entry.max_bytes
        out, code = run(
            f"if [ -f {q} ]; then head -c {max_b} {q}; "
            f"elif [ -d {q} ]; then echo 'CHYBA: cíl je adresář, uveď relative_path k souboru.'; exit 1; "
            f"else echo 'Soubor neexistuje: {full}'; exit 1; fi"
        )
        return ToolResult(code == 0, out, OperationLevel.READ)

    if tool_name == "write_file":
        path_id = args["path_id"]
        content = args.get("content", "")
        entries = parse_file_paths(permissions)
        entry = entries.get(path_id)
        if not entry or not entry.allow_write:
            return ToolResult(False, f"Cesta {path_id} není povolená pro zápis.", OperationLevel.SENSITIVE)
        full, err = resolve_remote_path(entry, args.get("relative_path", ""))
        if not full:
            return ToolResult(False, err, OperationLevel.SENSITIVE)
        if len(content.encode("utf-8")) > entry.max_bytes:
            return ToolResult(
                False,
                f"Obsah překračuje limit {entry.max_bytes} B pro {path_id}.",
                OperationLevel.SENSITIVE,
            )
        q = shell_quote(full)
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        create_only = bool(args.get("create_only", False))
        if create_only:
            pre = f"test ! -e {q} || {{ echo 'Soubor už existuje: {full}'; exit 1; }}; "
        else:
            pre = f"mkdir -p $(dirname {q}) 2>/dev/null; "
        cmd = (
            f"{pre}printf '%s' '{b64}' | base64 -d > {q} && "
            f"wc -c {q} && ls -la {q}"
        )
        out, code = run(cmd)
        return ToolResult(code == 0, out, OperationLevel.SENSITIVE)

    if tool_name == "delete_file":
        path_id = args["path_id"]
        entries = parse_file_paths(permissions)
        entry = entries.get(path_id)
        if not entry or not entry.allow_delete:
            return ToolResult(False, f"Cesta {path_id} není povolená pro mazání.", OperationLevel.DESTRUCTIVE)
        full, err = resolve_remote_path(entry, args.get("relative_path", ""))
        if not full:
            return ToolResult(False, err, OperationLevel.DESTRUCTIVE)
        q = shell_quote(full)
        out, code = run(
            f"if [ -f {q} ]; then rm -f {q} && echo 'Smazáno: {full}'; "
            f"elif [ -d {q} ]; then echo 'CHYBA: nelze smazat adresář, jen soubor.'; exit 1; "
            f"else echo 'Soubor neexistuje: {full}'; exit 1; fi"
        )
        return ToolResult(code == 0, out, OperationLevel.DESTRUCTIVE)

    return ToolResult(False, f"Neimplementovaný nástroj: {tool_name}")


def format_tool_result(tool_name: str, result: ToolResult) -> str:
    payload = {
        "tool": tool_name,
        "ok": result.ok,
        "level": int(result.level),
        "output": result.output[:6000],
    }
    return json.dumps(payload, ensure_ascii=False)
