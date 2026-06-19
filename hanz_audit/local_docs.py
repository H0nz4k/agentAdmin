from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from hanz_audit.config import ROOT


LOCAL_TOOLS = frozenset({"create_local_document", "list_local_documents"})


@dataclass
class LocalPathEntry:
    id: str
    path: Path
    max_bytes: int
    description: str


def parse_local_document_paths(permissions: dict, project_root: Path | None = None) -> dict[str, LocalPathEntry]:
    root = project_root or ROOT
    entries: dict[str, LocalPathEntry] = {}
    for raw in permissions.get("local_document_paths") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        rel = str(raw.get("path", "data/docs")).replace("\\", "/").strip("/")
        base = (root / rel).resolve()
        try:
            base.relative_to(root.resolve())
        except ValueError:
            continue
        entries[str(raw["id"])] = LocalPathEntry(
            id=str(raw["id"]),
            path=base,
            max_bytes=int(raw.get("max_bytes", 524288)),
            description=str(raw.get("description", "")),
        )
    return entries


def _slugify(title: str) -> str:
    text = title.lower().strip()
    text = re.sub(r"[^\w\s\-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_\-]+", "-", text).strip("-")
    return (text[:80] or "dokument").strip("-")


def resolve_local_file(
    entry: LocalPathEntry,
    filename: str,
    *,
    project_root: Path,
) -> tuple[Path | None, str]:
    name = (filename or "").strip().replace("\\", "/")
    if not name:
        return None, "Chybí název souboru."
    if "/" in name or ".." in name:
        return None, "Název souboru nesmí obsahovat cestu (použij jen soubor.md)."
    if not name.lower().endswith(".md"):
        name = f"{name}.md"
    if re.search(r'[<>:"|?*]', name):
        return None, "Neplatné znaky v názvu souboru."

    try:
        entry.path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return None, "Cílová složka není uvnitř projektu."

    target = (entry.path / name).resolve()
    try:
        target.relative_to(entry.path.resolve())
    except ValueError:
        return None, "Cesta mimo povolenou složku."

    return target, ""


def _format_document(title: str, content: str, *, source: str = "Agent Admin") -> str:
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")
    body = content.strip()
    return (
        f"# {title.strip()}\n\n"
        f"> Vytvořeno: {now} · {source}\n\n"
        f"---\n\n"
        f"{body}\n"
    )


def list_local_documents(project_root: Path, permissions: dict) -> tuple[bool, str]:
    entries = parse_local_document_paths(permissions, project_root)
    if not entries:
        return False, "Není nakonfigurována žádná složka local_document_paths."

    lines: list[str] = []
    for entry in entries.values():
        lines.append(f"## {entry.id} — {entry.path}")
        if not entry.path.is_dir():
            lines.append("  (složka zatím neexistuje)")
            continue
        files = sorted(entry.path.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            lines.append("  (žádné .md)")
            continue
        for f in files[:30]:
            size = f.stat().st_size
            lines.append(f"  - {f.name} ({size} B)")
    return True, "\n".join(lines)


def create_local_document(
    project_root: Path,
    permissions: dict,
    args: dict,
) -> tuple[bool, str]:
    entries = parse_local_document_paths(permissions, project_root)
    path_id = str(args.get("path_id", "docs"))
    entry = entries.get(path_id)
    if not entry:
        return False, f"Neznámá složka: {path_id}"

    title = str(args.get("title", "")).strip()
    content = str(args.get("content", ""))
    if not title:
        return False, "Chybí title (nadpis dokumentu)."
    if not content.strip():
        return False, "Chybí content (tělo dokumentu v Markdown)."

    filename = str(args.get("filename", "")).strip()
    if not filename:
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        filename = f"{ts}-{_slugify(title)}.md"

    target, err = resolve_local_file(entry, filename, project_root=project_root)
    if not target:
        return False, err

    if target.exists() and not bool(args.get("overwrite", False)):
        return (
            False,
            f"Soubor už existuje: {target}. Nastav overwrite=true nebo jiný filename.",
        )

    full_text = _format_document(title, content)
    encoded = full_text.encode("utf-8")
    if len(encoded) > entry.max_bytes:
        return (
            False,
            f"Dokument je příliš velký ({len(encoded)} B, limit {entry.max_bytes} B).",
        )

    entry.path.mkdir(parents=True, exist_ok=True)
    target.write_text(full_text, encoding="utf-8", newline="\n")

    rel = target.relative_to(project_root)
    return (
        True,
        f"Dokument uložen:\n{target}\n(relativně: {rel})\nVelikost: {len(encoded)} B",
    )


def local_tool_schemas(permissions: dict) -> list[dict]:
    path_ids = [e.id for e in parse_local_document_paths(permissions).values()]
    if not path_ids:
        path_ids = ["docs"]
    return [
        {
            "type": "function",
            "function": {
                "name": "create_local_document",
                "description": (
                    "Vytvoří Markdown dokument na LOKÁLNÍM PC (kde běží Agent Admin), ne na Pi. "
                    "Použij když uživatel chce dokumentaci, přehled, zápis z auditu nebo shrnutí do souboru. "
                    "Vyžaduje potvrzení v GUI. Obsah piš strukturovaně (nadpisy ##, odrážky, tabulky)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Nadpis dokumentu (H1)."},
                        "content": {
                            "type": "string",
                            "description": "Tělo v Markdown (bez H1 — doplní se automaticky).",
                        },
                        "path_id": {
                            "type": "string",
                            "enum": path_ids,
                            "description": "Cílová složka z whitelistu.",
                        },
                        "filename": {
                            "type": "string",
                            "description": "Volitelně jen název souboru, např. predicapp-analyza.md",
                        },
                        "overwrite": {
                            "type": "boolean",
                            "description": "Přepsat existující soubor.",
                            "default": False,
                        },
                    },
                    "required": ["title", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_local_documents",
                "description": "Read-only: seznam .md dokumentů v lokálních složkách na PC.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    ]
