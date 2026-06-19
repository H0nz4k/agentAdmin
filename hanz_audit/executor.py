from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from hanz_audit.agent_tools import ToolResult, execute_tool, format_tool_result
from hanz_audit.custom_tools import add_custom_tool, get_custom_tool
from hanz_audit.local_docs import (
    LOCAL_TOOLS,
    create_local_document,
    list_local_documents,
)
from hanz_audit.permissions import (
    OperationLevel,
    check_tool,
    contains_forbidden,
    load_permissions,
    load_services_inventory,
)
from hanz_audit.ssh_client import SSHClient


ApprovalCallback = Callable[[str, dict, OperationLevel, str], bool]
ConsoleCallback = Callable[[str, str, dict, str, bool], None]
ToolsReloadCallback = Callable[[], None]


def _all_write_tool_names(permissions: dict) -> frozenset[str]:
    names: set[str] = set()
    for key in ("level_1_tools", "level_2_tools", "level_3_tools"):
        names.update(permissions.get(key, []) or [])
    return frozenset(names)


@dataclass
class ExecutorSession:
    writes_done: int = 0
    local_writes_done: int = 0
    restarts_done: int = 0
    writes_exhausted: bool = False
    strategy_pause: bool = False
    fail_streak: dict[str, int] = field(default_factory=dict)
    blocked_signatures: set[str] = field(default_factory=set)
    blocked_tool_names: set[str] = field(default_factory=set)
    action_ids: list[str] = field(default_factory=list)


def _tool_signature(tool_name: str, arguments: dict) -> str:
    return f"{tool_name}|{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"


_WRITE_LIMIT_HINT = (
    " Limit změn za relaci je vyčerpán — NEOPAKUJ stejný zápisový nástroj. "
    "Pokračuj read-only diagnostikou, navrhni ruční krok uživateli, "
    "create_local_document s návodem, nebo řekni uživateli ať restartuje aplikaci "
    "(Reset chatu) pro novou relaci s limitem."
)


class AgentExecutor:
    def __init__(
        self,
        ssh: SSHClient | None,
        project_root: Path,
        audit_log_path: Path,
        approval: ApprovalCallback | None = None,
        on_console: ConsoleCallback | None = None,
        tools_path: Path | None = None,
        on_tools_reload: ToolsReloadCallback | None = None,
        v1_pack_path: Path | None = None,
        remote_tools_root: str | None = None,
    ) -> None:
        self.ssh = ssh
        self.project_root = project_root
        self.audit_log_path = audit_log_path
        self.approval = approval
        self.on_console = on_console
        self.tools_path = tools_path
        self.on_tools_reload = on_tools_reload
        self.v1_pack_path = v1_pack_path
        self.remote_tools_root = remote_tools_root
        self.permissions = load_permissions()
        self.services = load_services_inventory()
        self._write_tools = _all_write_tool_names(self.permissions)
        self.session = ExecutorSession()
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)

    def reset_session(self) -> None:
        self.session = ExecutorSession()

    def should_force_text_response(self) -> bool:
        return self.session.strategy_pause

    def _pause_strategy(self) -> None:
        self.session.strategy_pause = True
        self.session.writes_exhausted = True
        self.session.blocked_tool_names.update(self._write_tools)

    def _blocked_tool_name(self, tool_name: str) -> ToolResult | None:
        if tool_name not in self.session.blocked_tool_names:
            return None
        return ToolResult(
            False,
            f"Nástroj {tool_name} je pro tuto relaci zablokován (limit nebo opakované selhání)."
            f"{_WRITE_LIMIT_HINT}",
            OperationLevel.FORBIDDEN,
        )

    def _blocked_repeat(self, tool_name: str, args: dict) -> ToolResult | None:
        sig = _tool_signature(tool_name, args)
        if sig not in self.session.blocked_signatures:
            return None
        return ToolResult(
            False,
            f"Nástroj {tool_name} s těmito parametry už 2× selhal — neopakuj. "
            "Zkus jiný nástroj nebo jiný postup (read-only diagnostika, dokumentace, ruční krok).",
            OperationLevel.FORBIDDEN,
        )

    def _record_tool_failure(self, tool_name: str, args: dict, output: str = "") -> None:
        sig = _tool_signature(tool_name, args)
        self.session.fail_streak[sig] = self.session.fail_streak.get(sig, 0) + 1
        limit_hit = "limit změn" in output.lower()
        threshold = 1 if limit_hit else 2
        if self.session.fail_streak[sig] >= threshold:
            self.session.blocked_signatures.add(sig)
            self.session.blocked_tool_names.add(tool_name)
        if limit_hit or self.session.writes_exhausted:
            self._pause_strategy()

    def _write_limit_result(self, tool_name: str, max_writes: int) -> ToolResult:
        self._pause_strategy()
        return ToolResult(
            False,
            f"Dosažen limit změn za relaci ({max_writes}).{_WRITE_LIMIT_HINT}",
            OperationLevel.FORBIDDEN,
        )

    def _log(self, tool_name: str, arguments: dict, result: ToolResult) -> None:
        entry = {
            "id": str(uuid.uuid4())[:8],
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "arguments": arguments,
            "ok": result.ok,
            "level": int(result.level),
            "output_preview": result.output[:500],
        }
        with self.audit_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.session.action_ids.append(entry["id"])

    def _emit(self, event: str, tool_name: str, arguments: dict, detail: str = "", ok: bool = True) -> None:
        if self.on_console:
            self.on_console(event, tool_name, arguments, detail, ok)

    def _remote_writes_disabled(self) -> bool:
        if not self.ssh or not self.ssh.connected:
            return False
        disable_file = self.permissions.get("policy", {}).get(
            "remote_disable_file", "/etc/agentAdmin/disable-writes"
        )
        try:
            out, _, code = self.ssh.run(f"test -f {disable_file} && echo DISABLED || echo OK", timeout=15)
            return "DISABLED" in out and code == 0
        except Exception:
            return False

    def _run_local_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "create_local_document":
            ok, output = create_local_document(self.project_root, self.permissions, args)
            return ToolResult(ok, output, OperationLevel.SENSITIVE)
        if tool_name == "list_local_documents":
            ok, output = list_local_documents(self.project_root, self.permissions)
            return ToolResult(ok, output, OperationLevel.READ)
        return ToolResult(False, f"Neznámý lokální nástroj: {tool_name}", OperationLevel.FORBIDDEN)

    def run_tool(self, tool_name: str, arguments: dict | None = None) -> str:
        args = arguments or {}
        policy = self.permissions.get("policy", {})
        is_local = tool_name in LOCAL_TOOLS
        self._emit("start", tool_name, args)

        forbidden = contains_forbidden(self.permissions, json.dumps(args, ensure_ascii=False))
        if forbidden:
            result = ToolResult(False, f"Zakázaný vzor v parametrech: {forbidden}", OperationLevel.FORBIDDEN)
            self._emit("denied", tool_name, args, result.output, False)
            return format_tool_result(tool_name, result)

        decision = check_tool(self.permissions, self.services, tool_name, args)
        if not decision.allowed:
            result = ToolResult(False, decision.reason, OperationLevel.FORBIDDEN)
            self._emit("denied", tool_name, args, result.output, False)
            return format_tool_result(tool_name, result)

        level = decision.level

        blocked_name = self._blocked_tool_name(tool_name)
        if blocked_name:
            self._emit("denied", tool_name, args, blocked_name.output, False)
            return format_tool_result(tool_name, blocked_name)

        blocked = self._blocked_repeat(tool_name, args)
        if blocked:
            self._pause_strategy()
            self._emit("denied", tool_name, args, blocked.output, False)
            return format_tool_result(tool_name, blocked)

        def _deny(result: ToolResult) -> str:
            if not result.ok:
                self._record_tool_failure(tool_name, args, result.output)
            self._emit("denied", tool_name, args, result.output, False)
            return format_tool_result(tool_name, result)

        if tool_name == "register_custom_tool":
            description = self._describe_action(tool_name, args)
            if self.approval:
                self._emit("confirm", tool_name, args, description)
                if not self.approval(tool_name, args, OperationLevel.SENSITIVE, description):
                    result = ToolResult(False, "Operace zrušena uživatelem.", OperationLevel.SENSITIVE)
                    self._emit("cancel", tool_name, args, result.output, False)
                    return format_tool_result(tool_name, result)
            try:
                new_tool = add_custom_tool(args, self.tools_path)
                if self.on_tools_reload:
                    self.on_tools_reload()
                result = ToolResult(
                    True,
                    f"Nástroj {new_tool.openai_name} uložen do config/custom_tools.yaml.",
                    OperationLevel.SENSITIVE,
                )
            except ValueError as exc:
                result = ToolResult(False, str(exc), OperationLevel.SENSITIVE)
            self._log(tool_name, args, result)
            self._emit("done", tool_name, args, result.output, result.ok)
            return format_tool_result(tool_name, result)

        if is_local:
            if level == OperationLevel.SENSITIVE:
                max_local = policy.get("max_local_writes_per_session", 10)
                if tool_name == "create_local_document" and self.session.local_writes_done >= max_local:
                    result = ToolResult(
                        False,
                        f"Dosažen limit lokálních dokumentů za relaci ({max_local}).",
                        OperationLevel.FORBIDDEN,
                    )
                    self._emit("denied", tool_name, args, result.output, False)
                    return format_tool_result(tool_name, result)
                description = self._describe_action(tool_name, args)
                if self.approval:
                    self._emit("confirm", tool_name, args, description)
                    if not self.approval(tool_name, args, level, description):
                        result = ToolResult(False, "Operace zrušena uživatelem.", level)
                        self._emit("cancel", tool_name, args, result.output, False)
                        return format_tool_result(tool_name, result)

            detail = self._describe_action(tool_name, args)
            self._emit("cmd", tool_name, args, f"[local PC] {detail}")
            result = self._run_local_tool(tool_name, args)
            result.level = level
            if tool_name == "create_local_document" and result.ok:
                self.session.local_writes_done += 1
            self._log(tool_name, args, result)
            self._emit("done", tool_name, args, result.output, result.ok)
            return format_tool_result(tool_name, result)

        if not self.ssh or not self.ssh.connected:
            result = ToolResult(
                False,
                "SSH není připojeno — tento nástroj běží jen na Pi. Pro dokumentaci na PC použij create_local_document.",
                OperationLevel.FORBIDDEN,
            )
            self._emit("denied", tool_name, args, result.output, False)
            return format_tool_result(tool_name, result)

        if level.value >= OperationLevel.REVERSIBLE.value:
            if self.session.writes_exhausted:
                return _deny(self._write_limit_result(
                    tool_name, policy.get("max_writes_per_session", 5)
                ))
            if self._remote_writes_disabled():
                result = ToolResult(
                    False,
                    "Zápis na Pi je vypnutý (/etc/agentAdmin/disable-writes).",
                    OperationLevel.FORBIDDEN,
                )
                self._emit("denied", tool_name, args, result.output, False)
                return format_tool_result(tool_name, result)
            max_writes = policy.get("max_writes_per_session", 5)
            if self.session.writes_done >= max_writes:
                return _deny(self._write_limit_result(tool_name, max_writes))

        description = self._describe_action(tool_name, args)
        if level == OperationLevel.SENSITIVE and self.approval:
            self._emit("confirm", tool_name, args, description)
            if not self.approval(tool_name, args, level, description):
                result = ToolResult(False, "Operace zrušena uživatelem.", level)
                self._emit("cancel", tool_name, args, result.output, False)
                return format_tool_result(tool_name, result)
        if level == OperationLevel.DESTRUCTIVE and self.approval:
            self._emit("confirm", tool_name, args, description)
            if not self.approval(tool_name, args, level, description):
                result = ToolResult(False, "Operace zrušena uživatelem.", level)
                self._emit("cancel", tool_name, args, result.output, False)
                return format_tool_result(tool_name, result)

        def log_command(cmd: str) -> None:
            self._emit("cmd", tool_name, args, cmd)

        result = execute_tool(
            self.ssh,
            tool_name,
            args,
            log_command=log_command,
            tools_path=self.tools_path,
            v1_pack_path=self.v1_pack_path,
            remote_tools_root=self.remote_tools_root,
        )
        result.level = level

        if level.value >= OperationLevel.REVERSIBLE.value:
            self.session.writes_done += 1
            if tool_name in (
                "restart_service",
                "restart_docker_container",
                "stop_service",
                "start_service",
                "enable_service",
                "disable_service",
            ):
                self.session.restarts_done += 1

        self._log(tool_name, args, result)
        if not result.ok:
            self._record_tool_failure(tool_name, args, result.output)
        self._emit("done", tool_name, args, result.output, result.ok)
        return format_tool_result(tool_name, result)

    def _describe_action(self, tool_name: str, args: dict) -> str:
        if tool_name == "create_local_document":
            fn = args.get("filename") or "(auto)"
            preview = (args.get("content") or "")[:60].replace("\n", " ")
            return (
                f"Lokální dokument [{args.get('path_id', 'docs')}]: "
                f"„{args.get('title')}“ → {fn} — {preview}…"
            )
        if tool_name == "list_local_documents":
            return "Seznam lokálních .md dokumentů na PC"
        if tool_name == "restart_service":
            return f"Restart systemd služby: {args.get('service_id')}"
        if tool_name == "stop_service":
            return f"Zastavení služby: {args.get('service_id')}"
        if tool_name == "start_service":
            return f"Spuštění služby: {args.get('service_id')}"
        if tool_name == "disable_service":
            stop = " + stop" if args.get("stop_now", True) else ""
            return f"Vypnutí autostartu služby{stop}: {args.get('service_id')}"
        if tool_name == "enable_service":
            start = " + start" if args.get("start_now", True) else ""
            return f"Zapnutí autostartu služby{start}: {args.get('service_id')}"
        if tool_name == "restart_docker_container":
            return f"Restart docker kontejneru: {args.get('container_id')}"
        if tool_name == "docker_prune_dangling":
            return "Smazání dangling docker images"
        if tool_name == "docker_system_prune":
            return "Docker system prune — uvolní nevyužívané objekty"
        if tool_name == "journal_vacuum":
            return f"Zmenšení systemd journalu na {args.get('max_size', '500M')}"
        if tool_name == "prune_cache":
            return f"Smazání cache: {args.get('cache_id')}"
        if tool_name == "prune_old_backups":
            return f"Smazání starých záloh: {args.get('path_id')}"
        if tool_name == "backup_service_config":
            return f"Záloha konfigurace: {args.get('path_id')}"
        if tool_name == "read_file":
            rel = args.get("relative_path") or "(kořen)"
            return f"Čtení souboru [{args.get('path_id')}]: {rel}"
        if tool_name == "write_file":
            rel = args.get("relative_path") or "(kořen)"
            preview = (args.get("content") or "")[:80].replace("\n", " ")
            return f"Zápis souboru [{args.get('path_id')}]: {rel} — náhled: {preview}…"
        if tool_name == "delete_file":
            rel = args.get("relative_path") or "(kořen)"
            return f"Smazání souboru [{args.get('path_id')}]: {rel}"
        custom = get_custom_tool(tool_name)
        if custom:
            return f"Vlastní nástroj: {custom.name} — {custom.description[:120]}"
        if tool_name == "register_custom_tool":
            return f"Přidat nástroj do knihovny: {args.get('id')} — {args.get('name')}"
        return f"{tool_name}({args})"
