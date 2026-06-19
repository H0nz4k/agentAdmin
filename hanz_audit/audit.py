from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from hanz_audit.ssh_client import SSHClient


@dataclass
class AuditSection:
    title: str
    command: str
    output: str
    error: str = ""
    exit_code: int = 0
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class AuditResult:
    timestamp: datetime
    host: str
    connection_info: str
    sections: list[AuditSection] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.sections)


# Pouze read-only diagnostika — whitelist příkazů
AUDIT_COMMANDS: list[tuple[str, str]] = [
    ("Systém", "uname -a && cat /etc/os-release 2>/dev/null | head -5"),
    ("Uptime & load", "uptime && cat /proc/loadavg"),
    ("Teplota (RPi)", "vcgencmd measure_temp 2>/dev/null || echo 'N/A'"),
    ("Disk — df -h", "df -h"),
    ("Disk — inode", "df -i"),
    ("Velikost /", "du -xhd1 / 2>/dev/null | sort -hr | head -20"),
    ("Velikost /opt", "du -xhd1 /opt 2>/dev/null | sort -hr | head -20"),
    ("Velikost /var", "du -xhd1 /var 2>/dev/null | sort -hr | head -20"),
    ("Velikost /home", "du -xhd1 /home 2>/dev/null | sort -hr | head -20"),
    ("Journal — disk usage", "journalctl --disk-usage 2>/dev/null || echo 'N/A'"),
    ("Paměť — free", "free -h"),
    ("Top procesy (RAM)", "ps aux --sort=-%mem 2>/dev/null | head -20"),
    ("Top procesy (CPU)", "ps aux --sort=-%cpu 2>/dev/null | head -20"),
    (
        "Docker — kontejnery",
        "docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null "
        "|| echo 'Docker není dostupný'",
    ),
    (
        "Docker — velikosti",
        "docker ps -a --size --format 'table {{.Names}}\t{{.Size}}' 2>/dev/null "
        "|| echo 'Docker není dostupný'",
    ),
    (
        "Docker — disk usage",
        "docker system df -v 2>/dev/null || echo 'Docker není dostupný'",
    ),
    (
        "Docker — velké logy",
        "find /var/lib/docker/containers -name '*-json.log' -printf '%s %p\n' 2>/dev/null "
        "| sort -rn | head -15 | awk '{printf \"%.1f MB  %s\\n\", $1/1024/1024, $2}' "
        "|| echo 'Nelze číst docker logy (možná chybí oprávnění)'",
    ),
    (
        "Systemd — běžící služby",
        "systemctl list-units --type=service --state=running --no-pager 2>/dev/null",
    ),
    (
        "Systemd — enabled služby",
        "systemctl list-unit-files --type=service --state=enabled --no-pager --no-legend 2>/dev/null "
        "| awk '{print $1}' | head -40",
    ),
    (
        "Síť — poslouchající porty",
        "ss -tulpn 2>/dev/null || netstat -tulpn 2>/dev/null || echo 'ss/netstat N/A'",
    ),
    (
        "Git repozitáře",
        "find /opt /home /srv -maxdepth 4 -name .git -type d 2>/dev/null "
        "| sed 's|/.git||' | head -30",
    ),
    (
        "Otevřené smazané soubory",
        "lsof +L1 2>/dev/null | head -20 || echo 'lsof N/A nebo chybí oprávnění'",
    ),
    (
        "Cron — uživatelské",
        "crontab -l 2>/dev/null || echo 'Žádný user crontab'",
    ),
    (
        "PredicApp — detail /opt",
        "du -xhd2 /opt/PredicApp 2>/dev/null | sort -hr | head -25",
    ),
    (
        "hanz-agent — detail /opt",
        "du -xhd2 /opt/hanz-agent 2>/dev/null | sort -hr | head -25",
    ),
    (
        "SCRIPTS — detail /opt",
        "du -xhd1 /opt/SCRIPTS 2>/dev/null | sort -hr | head -20",
    ),
    (
        "Velké soubory v /opt (>50 MB)",
        "find /opt/PredicApp /opt/hanz-agent /opt/SCRIPTS -type f -size +50M "
        "2>/dev/null -printf '%s\\t%p\\n' | sort -rn | head -20 "
        "| awk 'BEGIN{OFS=\"\\t\"} {printf \"%.1f MB\\t%s\\n\", $1/1024/1024, $2}'",
    ),
    (
        "Systemd — klíčové služby (unit)",
        "for u in predicapp hanz-agent appmeteo app-m lora-hub stream-web "
        "whiteboard-hub planetum_bot cloudflared lcd-info; do "
        "echo \"=== ${u}.service ===\"; "
        "systemctl show \"${u}.service\" -p Description,ExecStart,WorkingDirectory,"
        "FragmentPath,ActiveState 2>/dev/null || echo 'unit nenalezen'; "
        "echo; done",
    ),
]


def run_audit(ssh: SSHClient, host: str) -> AuditResult:
    connection_info = ssh.test_connection()
    result = AuditResult(
        timestamp=datetime.now(timezone.utc),
        host=host,
        connection_info=connection_info,
    )

    for title, command in AUDIT_COMMANDS:
        try:
            out, err, code = ssh.run(command, timeout=120)
            section = AuditSection(
                title=title,
                command=command,
                output=out or "(prázdný výstup)",
                error=err,
                exit_code=code,
            )
        except Exception as exc:
            section = AuditSection(
                title=title,
                command=command,
                output="",
                error=str(exc),
                exit_code=-1,
                skipped=True,
                skip_reason=str(exc),
            )
        result.sections.append(section)

    return result
