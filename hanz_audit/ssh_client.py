from __future__ import annotations

import socket
from dataclasses import dataclass

import paramiko

from hanz_audit.config import default_ssh_key


@dataclass
class SSHConfig:
    host: str
    user: str
    port: int = 22
    key_path: str = ""
    connect_timeout: int = 20
    banner_timeout: int = 30


def format_ssh_error(exc: Exception, cfg: SSHConfig) -> str:
    msg = str(exc).strip()
    lower = msg.lower()

    if isinstance(exc, FileNotFoundError):
        return str(exc)

    if isinstance(exc, (TimeoutError, socket.timeout)):
        return (
            f"Timeout při připojování na {cfg.host}:{cfg.port}. "
            "Je HanzHub zapnutý a jsi na stejné síti (Wi-Fi/LAN)?"
        )

    if "banner" in lower:
        return (
            f"Server {cfg.host}:{cfg.port} neodpověděl SSH bannerem. "
            "Port může být otevřený, ale sshd neběží nebo je přetížený. "
            f"Ověř v terminálu: ssh {cfg.user}@{cfg.host}"
        )

    if "authentication" in lower or "auth" in lower:
        return (
            f"SSH autentizace selhala pro {cfg.user}@{cfg.host}. "
            "Zkontroluj klíč v config.yaml (ssh.key_path)."
        )

    if "refused" in lower or "no route" in lower or "unreachable" in lower:
        return f"Host {cfg.host}:{cfg.port} není dostupný — {msg}"

    return msg or type(exc).__name__


class SSHClient:
    def __init__(self, cfg: SSHConfig) -> None:
        self.cfg = cfg
        self._client: paramiko.SSHClient | None = None

    def connect(self) -> None:
        self.close()

        key_path = self.cfg.key_path or str(default_ssh_key() or "")
        if not key_path:
            raise FileNotFoundError(
                "SSH klíč nenalezen. Nastav ssh.key_path v config.yaml "
                "nebo vytvoř ~/.ssh/id_ed25519."
            )

        try:
            with socket.create_connection(
                (self.cfg.host, self.cfg.port),
                timeout=self.cfg.connect_timeout,
            ):
                pass
        except OSError as exc:
            raise ConnectionError(
                f"TCP spojení na {self.cfg.host}:{self.cfg.port} selhalo: {exc}"
            ) from exc

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.cfg.host,
            port=self.cfg.port,
            username=self.cfg.user,
            key_filename=key_path,
            timeout=self.cfg.connect_timeout,
            banner_timeout=self.cfg.banner_timeout,
            auth_timeout=self.cfg.connect_timeout,
            allow_agent=True,
            look_for_keys=True,
        )
        self._client = client

    def close(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    @property
    def connected(self) -> bool:
        if not self._client:
            return False
        t = self._client.get_transport()
        return t is not None and t.is_active()

    def run(self, command: str, timeout: int = 60) -> tuple[str, str, int]:
        if not self.connected:
            raise RuntimeError("SSH není připojeno.")

        _, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        return out, err, exit_code

    def test_connection(self) -> str:
        out, err, code = self.run("echo ok && hostname && uptime")
        if code != 0:
            raise RuntimeError(err or out or f"SSH test selhal (exit {code})")
        return out
