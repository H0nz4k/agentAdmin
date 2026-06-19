from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    load_dotenv(ROOT / ".env")
    config_path = ROOT / "config.yaml"
    with config_path.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config.setdefault("openai", {})
    config["openai"]["api_key"] = os.getenv("OPENAI_API_KEY", "")
    config["openai"]["model"] = os.getenv(
        "OPENAI_MODEL", config["openai"].get("model", "gpt-4o-mini")
    )
    config["_root"] = ROOT
    return config


def default_ssh_key() -> Path | None:
    ssh_dir = Path.home() / ".ssh"
    for name in ("id_ed25519", "id_rsa"):
        candidate = ssh_dir / name
        if candidate.is_file():
            return candidate
    return None
