#!/usr/bin/env python3
"""Deploy agentAdmin skriptů na Raspberry Pi přes SSH (config.yaml)."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import paramiko

from hanz_audit.config import load_config
from hanz_audit.ssh_client import SSHClient, SSHConfig, format_ssh_error
from hanz_audit.version import __version__

REMOTE_STAGING = "agentAdmin-staging"
V1_PACK = "hanz-agent-tools-v1"


def _build_staging(root: Path) -> Path:
    v1 = root / V1_PACK
    scripts_src = v1 / "scripts"
    config_src = v1 / "config"
    if not scripts_src.is_dir():
        raise FileNotFoundError(f"Chybí {scripts_src} — stáhni repozitář nebo zkontroluj cestu.")

    staging = Path(tempfile.mkdtemp(prefix="agentAdmin-deploy-"))
    tools_dst = staging / "tools" / "scripts"
    etc_dst = staging / "etc"
    custom_dst = staging / "custom-scripts"

    shutil.copytree(scripts_src, tools_dst)
    etc_dst.mkdir(parents=True)
    for pattern in ("*.yaml", "*.txt"):
        for f in config_src.glob(pattern):
            shutil.copy2(f, etc_dst / f.name)

    custom_src = root / "scripts" / "pi"
    custom_dst.mkdir()
    if custom_src.is_dir():
        for f in custom_src.iterdir():
            if f.is_file() and f.name != "README.txt":
                shutil.copy2(f, custom_dst / f.name)

    install_sh = root / "scripts" / "pi-install-agentAdmin.sh"
    shutil.copy2(install_sh, staging / "install.sh")
    _normalize_lf(staging)
    return staging


def _normalize_lf(root: Path) -> None:
    """Windows → LF pro bash skripty na Pi; odstraní UTF-8 BOM."""
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix not in (".sh", ".txt", ".yaml") and f.name != "install.sh":
            continue
        data = f.read_bytes()
        changed = False
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
            changed = True
        if b"\r" in data:
            data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            changed = True
        if changed:
            f.write_bytes(data)


def _sftp_mkdir_p(sftp: paramiko.SFTPClient, remote: str) -> None:
    parts = remote.strip("/").split("/")
    path = ""
    for part in parts:
        path = f"{path}/{part}" if path else part
        try:
            sftp.stat(path)
        except OSError:
            sftp.mkdir(path)


def _sftp_upload_dir(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    _sftp_mkdir_p(sftp, remote)
    for item in sorted(local.iterdir()):
        rpath = f"{remote}/{item.name}"
        if item.is_dir():
            _sftp_upload_dir(sftp, item, rpath)
        else:
            sftp.put(str(item), rpath)


def _ssh_cfg_from_config(cfg: dict) -> SSHConfig:
    ssh = cfg.get("ssh") or {}
    return SSHConfig(
        host=str(ssh.get("host", "192.168.1.5")),
        user=str(ssh.get("user", "hanz")),
        port=int(ssh.get("port", 22)),
        key_path=str(ssh.get("key_path", "") or ""),
        connect_timeout=int(ssh.get("connect_timeout", 20)),
        banner_timeout=int(ssh.get("banner_timeout", 30)),
    )


def _out(text: str) -> None:
    """Bezpečný výpis na Windows konzoli (cp1250)."""
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    safe = text.encode(enc, errors="replace").decode(enc, errors="replace")
    print(safe)


def deploy(*, dry_run: bool = False, verify: bool = True) -> int:
    cfg = load_config()
    ssh_cfg = _ssh_cfg_from_config(cfg)
    remote_root = cfg.get("paths", {}).get("remote_agent_admin", "/opt/agentAdmin")

    _out(f"Agent Admin deploy v{__version__}")
    _out(f"  Cil: {ssh_cfg.user}@{ssh_cfg.host}:{ssh_cfg.port}")
    _out(f"  Pi cesta: {remote_root}")

    staging = _build_staging(ROOT)
    try:
        file_count = sum(1 for _ in staging.rglob("*") if _.is_file())
        _out(f"  Balicek: {file_count} souboru ve staging")

        if dry_run:
            _out(f"\n[dry-run] Nic se nenahravalo. Staging: {staging}")
            return 0

        client = SSHClient(ssh_cfg)
        try:
            client.connect()
            hostname = client.test_connection()
            _out(f"  Pripojeno: {hostname.splitlines()[1] if len(hostname.splitlines()) > 1 else hostname}")

            remote_staging = f"{REMOTE_STAGING}"
            client.run(f"rm -rf {remote_staging}", timeout=30)

            transport = client._client.get_transport()
            if not transport:
                raise RuntimeError("SSH transport není k dispozici.")
            sftp = paramiko.SFTPClient.from_transport(transport)
            try:
                _out(f"  Nahravam -> ~/{remote_staging}/ …")
                _sftp_upload_dir(sftp, staging, remote_staging)
            finally:
                sftp.close()

            _out("  Instalace (sudo) …")
            cmd = f"sudo bash ~/{remote_staging}/install.sh ~/{remote_staging}"
            out, err, code = client.run(cmd, timeout=180)
            combined = "\n".join(x for x in (out, err) if x).strip()
            if combined:
                _out(combined)
            if code != 0:
                _out(f"\nCHYBA: Instalace selhala (exit {code}).")
                _out(
                    "  Tip: uzivatel na Pi potrebuje sudo bez hesla, "
                    "nebo spust rucne: sudo bash ~/agentAdmin-staging/install.sh ~/agentAdmin-staging"
                )
                return code

            if verify:
                _out("\n  Verifikace …")
                test_cmd = (
                    f"bash {remote_root}/tools/scripts/read/system_overview.sh 2>&1 | head -5"
                )
                tout, terr, tcode = client.run(test_cmd, timeout=60)
                if tcode == 0 and tout.strip():
                    _out(tout)
                    _out("\nOK Deploy hotovy.")
                else:
                    _out("WARN: Deploy probehl, ale verifikace selhala.")
                    if terr:
                        _out(terr)

            client.run(f"rm -rf ~/{remote_staging}", timeout=30)
            return 0
        except Exception as exc:
            _out(f"\nCHYBA: {format_ssh_error(exc, ssh_cfg)}")
            return 1
        finally:
            client.close()
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy agentAdmin na Raspberry Pi (SSH).")
    parser.add_argument("--dry-run", action="store_true", help="Jen sestav staging, nenahrávej.")
    parser.add_argument("--no-verify", action="store_true", help="Preskoč test po instalaci.")
    args = parser.parse_args()
    raise SystemExit(deploy(dry_run=args.dry_run, verify=not args.no_verify))


if __name__ == "__main__":
    main()
