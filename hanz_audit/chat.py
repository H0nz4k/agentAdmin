from __future__ import annotations

from pathlib import Path

from openai import OpenAI

from hanz_audit.agent_config import build_system_prompt, load_agent_config


def load_info_context(root: Path, info_file: str) -> str:
    path = root / info_file
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        return text[:8000]
    return ""


class ChatSession:
    def __init__(
        self,
        api_key: str,
        model: str,
        info_context: str = "",
        agent_config: dict | None = None,
    ) -> None:
        if not api_key:
            raise ValueError(
                "Chybí OPENAI_API_KEY. Vytvoř soubor .env podle .env.example."
            )
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.messages: list[dict[str, str]] = []
        self.audit_context: str = ""
        self.agent_config = agent_config or load_agent_config()

        system = build_system_prompt(self.agent_config, info_context)
        self._base_system = system
        self.messages.append({"role": "system", "content": system})

    def set_audit_context(self, markdown_report: str) -> None:
        self.audit_context = markdown_report[:50000]
        self.messages[0]["content"] = (
            self._base_system
            + "\n\n---\nPoslední audit serveru (raw data):\n"
            + self.audit_context
        )

    def ask(self, user_message: str) -> str:
        self.messages.append({"role": "user", "content": user_message})
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self.messages,
            temperature=0.3,
        )
        reply = response.choices[0].message.content or ""
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    def reset_conversation(self) -> None:
        system_content = self.messages[0]["content"] if self.messages else self._base_system
        self.messages = [{"role": "system", "content": system_content}]
