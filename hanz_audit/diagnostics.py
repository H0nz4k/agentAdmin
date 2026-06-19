from __future__ import annotations

from dataclasses import dataclass

from hanz_audit.analysis import Recommendation
from hanz_audit.redact import redact_secrets
from hanz_audit.ssh_client import SSHClient


@dataclass
class DiagnosticSection:
    title: str
    command: str
    output: str
    error: str = ""
    exit_code: int = 0


# Read-only diagnostika pro režim „Zkusit vyřešit“
DIAGNOSTIC_SETS: dict[str, list[tuple[str, str]]] = {
    "memory": [
        ("Paměť — free", "free -h"),
        (
            "Paměť — detail",
            "grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree):' "
            "/proc/meminfo",
        ),
        ("Top procesy (RAM %)", "ps aux --sort=-%mem 2>/dev/null | head -15"),
        (
            "Top procesy (RSS MB)",
            "ps -eo pid,user,rss,vsz,comm,args --sort=-rss 2>/dev/null | head -15",
        ),
        (
            "Služby — paměť systemd",
            "systemctl show predicapp hanz-agent stream-web planetum_bot --property="
            "Id,ActiveState,MemoryCurrent,MemoryPeak 2>/dev/null || true",
        ),
        (
            "Docker — statistiky",
            "docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}' "
            "2>/dev/null || echo 'Docker není dostupný'",
        ),
    ],
    "disk": [
        ("Disk — df", "df -h"),
        ("Velikost /opt", "du -xhd1 /opt 2>/dev/null | sort -hr | head -15"),
        (
            "PredicApp — detail",
            "du -xhd1 /opt/PredicApp 2>/dev/null | sort -hr | head -15",
        ),
        (
            "Velké soubory",
            "find /opt -xdev -type f -size +50M 2>/dev/null | head -15",
        ),
    ],
    "hanz_agent": [
        ("hanz-agent — proces", "ps aux | grep -E '[h]anz-agent|[l]lama' || true"),
        (
            "hanz-agent — unit",
            "systemctl status hanz-agent --no-pager -l 2>/dev/null | head -25",
        ),
        (
            "hanz-agent — soubory",
            "du -xhd1 /opt/hanz-agent 2>/dev/null | sort -hr | head -10",
        ),
        ("Síť — bind", "ss -tulpn 2>/dev/null | grep -E ':(8080|8085|4010|5000) ' || true"),
    ],
    "docker": [
        ("Docker — df", "docker system df 2>/dev/null || echo 'Docker není dostupný'"),
        (
            "Docker — images",
            "docker image ls --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}' "
            "2>/dev/null | head -20",
        ),
        (
            "Docker — kontejnery",
            "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Size}}' 2>/dev/null",
        ),
    ],
    "network": [
        ("Porty", "ss -tulpn 2>/dev/null | head -40"),
        (
            "Cloudflared",
            "systemctl status cloudflared --no-pager 2>/dev/null | head -15",
        ),
    ],
    "general": [
        ("Uptime", "uptime && free -h"),
        ("Top RAM", "ps aux --sort=-%mem 2>/dev/null | head -10"),
        ("Disk /", "df -h /"),
    ],
}


def detect_diagnostic_topic(rec: Recommendation) -> str:
    text = f"{rec.problem} {rec.solution} {' '.join(rec.steps)}".lower()
    if any(k in text for k in ("ram", "paměť", "pamet", "memory", "rss", "memusage")):
        return "memory"
    if any(k in text for k in ("disk", "místo", "misto", "/opt", "predicapp", "záloh")):
        return "disk"
    if "hanz-agent" in text or "llama" in text or "192.168.1.3" in text:
        return "hanz_agent"
    if "docker" in text or "image" in text or "dashy" in text:
        return "docker"
    if any(k in text for k in ("port", "cloudflared", "token", "síť", "sit")):
        return "network"
    return "general"


def run_diagnostics(ssh: SSHClient, topic: str) -> list[DiagnosticSection]:
    commands = DIAGNOSTIC_SETS.get(topic, DIAGNOSTIC_SETS["general"])
    sections: list[DiagnosticSection] = []
    for title, command in commands:
        try:
            out, err, code = ssh.run(command, timeout=45)
            sections.append(
                DiagnosticSection(
                    title=title,
                    command=command,
                    output=redact_secrets(out),
                    error=redact_secrets(err),
                    exit_code=code,
                )
            )
        except Exception as exc:
            sections.append(
                DiagnosticSection(
                    title=title,
                    command=command,
                    output="",
                    error=str(exc),
                    exit_code=-1,
                )
            )
    return sections


def format_diagnostics(sections: list[DiagnosticSection], topic: str) -> str:
    lines = [
        f"--- LIVE DIAGNOSTIKA ({topic}) — právě z Pi ---",
        "",
    ]
    for sec in sections:
        lines.append(f"### {sec.title}")
        lines.append(f"$ {sec.command}")
        if sec.output:
            lines.append(sec.output)
        if sec.error:
            lines.append(f"[stderr] {sec.error}")
        if sec.exit_code not in (0, -1):
            lines.append(f"[exit {sec.exit_code}]")
        lines.append("")
    return "\n".join(lines).strip()
