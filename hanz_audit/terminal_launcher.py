from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import PurePosixPath

from hanz_audit.ssh_client import SSHConfig

# Unix cesty v textu (chat, reporty)
UNIX_PATH_RE = re.compile(
    r"(?P<path>/(?:[\w\-.]+/)*[\w\-.]+(?:\.\w+)?)"
)

FILE_SUFFIXES = {
    ".service", ".yaml", ".yml", ".conf", ".env", ".db", ".sh", ".py",
    ".json", ".txt", ".log", ".gguf", ".md", ".toml", ".ini", ".cfg",
}


def strip_path_punctuation(path: str) -> str:
    return path.rstrip(".,;:)]}`'\"")


def path_to_cd_directory(path: str) -> str:
    """Adresář pro cd — u souboru rodičovská složka."""
    clean = strip_path_punctuation(path)
    if not clean.startswith("/"):
        return clean
    p = PurePosixPath(clean)
    if p.suffix.lower() in FILE_SUFFIXES or ("." in p.name and not p.name.startswith(".")):
        parent = p.parent
        return str(parent) if str(parent) != "." else "/"
    return clean.rstrip("/") or "/"


def find_unix_paths(text: str) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    for m in UNIX_PATH_RE.finditer(text):
        path = strip_path_punctuation(m.group("path"))
        if len(path) > 1:
            found.append((m.start(), m.end(), path))
    return found


def _build_ssh_argv(cfg: SSHConfig, remote_cd: str | None = None) -> list[str]:
    cmd: list[str] = ["ssh"]
    if cfg.key_path:
        cmd.extend(["-i", cfg.key_path])
    if cfg.port != 22:
        cmd.extend(["-p", str(cfg.port)])
    cmd.append("-t")
    cmd.append(f"{cfg.user}@{cfg.host}")
    if remote_cd:
        cmd.append(f"cd '{remote_cd}' && exec bash -l")
    return cmd


def open_ssh_terminal(cfg: SSHConfig, remote_path: str | None = None) -> None:
    cd_dir = path_to_cd_directory(remote_path) if remote_path else None
    ssh_argv = _build_ssh_argv(cfg, cd_dir)

    wt = shutil.which("wt") or shutil.which("wt.exe")
    if wt:
        subprocess.Popen(
            [wt, "new-tab", "--title", "HanzHub", "--"] + ssh_argv,
            close_fds=True,
        )
        return

    subprocess.Popen(
        ssh_argv,
        creationflags=subprocess.CREATE_NEW_CONSOLE,  # type: ignore[attr-defined]
        close_fds=True,
    )
