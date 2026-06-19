from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from hanz_audit.config import ROOT

VALID_CATEGORIES = {
    "service", "path", "issue", "fix", "relation", "decision",
}


def knowledge_path(custom: Path | None = None) -> Path:
    return custom or (ROOT / "config" / "knowledge.yaml")


def load_knowledge(path: Path | None = None) -> dict:
    p = knowledge_path(path)
    if not p.is_file():
        return {"updated_at": "", "facts": []}
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("facts", [])
    return data


def save_knowledge(data: dict, path: Path | None = None) -> Path:
    p = knowledge_path(path)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return p


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower())[:40].strip("_")
    return s or "fact"


def next_fact_id(data: dict, subject: str) -> str:
    base = _slug(subject)
    existing = {f.get("id", "") for f in data.get("facts", [])}
    candidate = base
    n = 2
    while candidate in existing:
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def add_facts(data: dict, new_facts: list[dict]) -> int:
    added = 0
    facts = data.setdefault("facts", [])
    existing_ids = {f.get("id") for f in facts}
    existing_texts = {f.get("text", "").strip() for f in facts}

    for raw in new_facts:
        category = raw.get("category", "decision")
        if category not in VALID_CATEGORIES:
            category = "decision"
        text = (raw.get("text") or "").strip()
        if not text or text in existing_texts:
            continue
        subject = (raw.get("subject") or "hanzhub").strip()
        fid = raw.get("id") or next_fact_id(data, subject)
        if fid in existing_ids:
            fid = next_fact_id(data, f"{subject}_{added}")

        entry = {
            "id": fid,
            "category": category,
            "subject": subject,
            "text": text,
            "related": raw.get("related") or [],
            "source": raw.get("source", "chat"),
            "learned": raw.get("learned") or datetime.now(timezone.utc).date().isoformat(),
        }
        facts.append(entry)
        existing_ids.add(fid)
        existing_texts.add(text)
        added += 1
    return added


def format_knowledge_for_prompt(data: dict, max_chars: int = 5000) -> str:
    facts = data.get("facts", [])
    if not facts:
        return ""

    lines = [
        "Globální paměť HanzHub (config/knowledge.yaml) — ověřené poznatky:",
        "",
    ]
    by_category: dict[str, list[dict]] = {}
    for f in facts:
        by_category.setdefault(f.get("category", "decision"), []).append(f)

    labels = {
        "service": "Služby",
        "path": "Cesty a soubory",
        "issue": "Známé problémy",
        "fix": "Ověřená řešení",
        "relation": "Souvislosti",
        "decision": "Rozhodnutí",
    }
    for cat in ("service", "path", "issue", "fix", "relation", "decision"):
        items = by_category.get(cat, [])
        if not items:
            continue
        lines.append(f"### {labels.get(cat, cat)}")
        for f in items:
            subj = f.get("subject", "")
            text = " ".join(f.get("text", "").split())
            rel = f.get("related") or []
            rel_s = f" → souvisí: {', '.join(rel)}" if rel else ""
            lines.append(f"- [{subj}] {text}{rel_s}")
        lines.append("")

    out = "\n".join(lines).strip()
    if len(out) > max_chars:
        out = out[: max_chars - 3] + "..."
    return out


EXTRACT_SYSTEM = """Jsi extraktor poznatků pro domácí server HanzHub.
Z konverzace vyber 0–5 nových faktů, které stojí za uložení do dlouhodobé paměti.
Vrať POUZE JSON pole objektů (bez markdown):
[
  {
    "category": "service|path|issue|fix|relation|decision",
    "subject": "krátký identifikátor služby/cesty",
    "text": "stručný poznatek v češtině",
    "related": ["volitelné související entity"]
  }
]
Neopakuj obecnosti. Ukládej jen konkrétní zjištění, rozhodnutí nebo souvislosti."""


def extract_facts_from_conversation(client, model: str, conversation: str) -> list[dict]:
    if not conversation.strip():
        return []
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": EXTRACT_SYSTEM},
            {
                "role": "user",
                "content": f"Konverzace k extrakci:\n\n{conversation[-12000:]}",
            },
        ],
        temperature=0.1,
    )
    raw = (response.choices[0].message.content or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    return []
