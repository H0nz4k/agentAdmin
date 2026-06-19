from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from openai import OpenAI

from hanz_audit.agent_config import build_system_prompt, load_agent_config
from hanz_audit.agent_tools import get_all_tool_schemas
from hanz_audit.custom_tools import default_tools_path
from hanz_audit.memory import format_knowledge_for_prompt, load_knowledge


def load_info_context(root: Path, info_file: str) -> str:
    path = root / info_file
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        return text[:8000]
    return ""


ToolHandler = Callable[[str, dict], str]


class ChatSession:
    def __init__(
        self,
        api_key: str,
        model: str,
        info_context: str = "",
        agent_config: dict | None = None,
        *,
        tools_enabled: bool = False,
        knowledge_path: Path | None = None,
        custom_tools_path: Path | None = None,
        v1_pack_path: Path | None = None,
        remote_tools_root: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError(
                "Chybí OPENAI_API_KEY. Vytvoř soubor .env podle .env.example."
            )
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.messages: list[dict] = []
        self.audit_context: str = ""
        self.agent_config = agent_config or load_agent_config()
        self.tools_enabled = tools_enabled
        self.knowledge_path = knowledge_path
        self.custom_tools_path = custom_tools_path or default_tools_path()
        self.v1_pack_path = v1_pack_path
        self.remote_tools_root = remote_tools_root
        self.info_context = info_context

        self._base_system = self._build_base_system()
        self.messages.append({"role": "system", "content": self._base_system})

    def _build_base_system(self) -> str:
        system = build_system_prompt(self.agent_config, self.info_context)
        return self._append_memory_to_system(system)

    def _append_memory_to_system(self, system: str) -> str:
        mem = format_knowledge_for_prompt(load_knowledge(self.knowledge_path))
        if mem:
            return system + "\n\n---\n" + mem
        return system

    def reload_memory(self) -> None:
        self._base_system = self._build_base_system()
        content = self._base_system
        if self.audit_context:
            content += "\n\n---\nPoslední audit serveru (raw data):\n" + self.audit_context
        if self.messages:
            self.messages[0]["content"] = content

    def set_audit_context(self, markdown_report: str) -> None:
        self.audit_context = markdown_report[:50000]
        self.messages[0]["content"] = (
            self._base_system
            + "\n\n---\nPoslední audit serveru (raw data):\n"
            + self.audit_context
        )

    def reload_custom_tools(self) -> None:
        """Obnoví seznam nástrojů pro další volání API (vestavěné + custom + v1)."""
        _ = get_all_tool_schemas(
            self.custom_tools_path,
            v1_pack_path=self.v1_pack_path,
            remote_tools_root=self.remote_tools_root,
        )

    def ask(self, user_message: str, tool_handler: ToolHandler | None = None) -> tuple[str, bool]:
        self.messages.append({"role": "user", "content": user_message})
        use_tools = self.tools_enabled and tool_handler is not None
        max_rounds = 10 if use_tools else 1
        tools_used = False

        for _ in range(max_rounds):
            kwargs: dict = {
                "model": self.model,
                "messages": self.messages,
                "temperature": 0.2,
            }
            if use_tools:
                kwargs["tools"] = get_all_tool_schemas(
                    self.custom_tools_path,
                    v1_pack_path=self.v1_pack_path,
                    remote_tools_root=self.remote_tools_root,
                )
                kwargs["tool_choice"] = "auto"

            response = self.client.chat.completions.create(**kwargs)
            msg = response.choices[0].message

            if not use_tools or not msg.tool_calls:
                text = msg.content or ""
                self.messages.append({"role": "assistant", "content": text})
                return text, tools_used

            tools_used = True

            assistant_msg: dict = {"role": "assistant", "content": msg.content or ""}
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
            self.messages.append(assistant_msg)

            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = tool_handler(tc.function.name, args)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )

        return (
            "Dosáhnut limit 10 kol nástrojů — zastavuji se.\n"
            "Agent opakovaně volal stejné nástroje bez úspěchu. "
            "Zkus „Zkusit vyřešit“ nebo napiš do chatu konkrétněji, co má udělat.",
            tools_used,
        )

    def reset_conversation(self) -> None:
        content = self._base_system
        if self.audit_context:
            content += "\n\n---\nPoslední audit serveru (raw data):\n" + self.audit_context
        self.messages = [{"role": "system", "content": content}]

    def recent_conversation_text(self, max_messages: int = 12) -> str:
        lines: list[str] = []
        for msg in self.messages[-max_messages:]:
            if msg["role"] == "system":
                continue
            role = msg.get("role", "")
            content = msg.get("content") or ""
            if content:
                lines.append(f"{role}: {content}")
        return "\n\n".join(lines)
