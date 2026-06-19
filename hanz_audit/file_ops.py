from __future__ import annotations

import re
import shlex
from dataclasses import dataclass


@dataclass
class FilePathEntry:
    id: str
    path: str
    allow_read: bool
    allow_write: bool
    allow_delete: bool
    max_bytes: int
    is_file: bool
    description: str


def parse_file_paths(permissions: dict) -> dict[str, FilePathEntry]:
    entries: dict[str, FilePathEntry] = {}
    for raw in permissions.get("file_paths") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        entries[str(raw["id"])] = FilePathEntry(
            id=str(raw["id"]),
            path=str(raw["path"]).rstrip("/"),
            allow_read=bool(raw.get("allow_read", True)),
            allow_write=bool(raw.get("allow_write", False)),
            allow_delete=bool(raw.get("allow_delete", False)),
            max_bytes=int(raw.get("max_bytes", 262144)),
            is_file=bool(raw.get("is_file", False)),
            description=str(raw.get("description", "")),
        )
    return entries


def resolve_remote_path(entry: FilePathEntry, relative_path: str = "") -> tuple[str | None, str]:
    rel = (relative_path or "").strip().replace("\\", "/").lstrip("/")
    if rel and (".." in rel.split("/") or rel.startswith("~")):
        return None, "Neplatná relativní cesta (path traversal)."
    if re.search(r"[;|`$()&<>]", rel):
        return None, "Relativní cesta obsahuje zakázané znaky."

    if entry.is_file:
        if rel:
            return None, "Tento whitelist je jeden soubor — relative_path nech prázdný."
        full = entry.path
    else:
        full = entry.path if not rel else f"{entry.path}/{rel}"

    if re.search(r"[;|`$()&<>]", full):
        return None, "Cílová cesta obsahuje zakázané znaky."
    return full, ""


def file_path_ids_for(permissions: dict, *, read: bool = False, write: bool = False, delete: bool = False) -> list[str]:
    ids: list[str] = []
    for entry in parse_file_paths(permissions).values():
        if read and entry.allow_read:
            ids.append(entry.id)
        elif write and entry.allow_write:
            ids.append(entry.id)
        elif delete and entry.allow_delete:
            ids.append(entry.id)
    return ids


def shell_quote(path: str) -> str:
    return shlex.quote(path)
