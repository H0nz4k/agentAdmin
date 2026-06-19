from __future__ import annotations

import re
from dataclasses import dataclass, field

from hanz_audit.audit import AuditResult
from hanz_audit.inventory import _has_git


@dataclass
class Finding:
    severity: str  # critical | warning | info
    title: str
    detail: str


@dataclass
class Recommendation:
    priority: int
    problem: str
    solution: str
    steps: list[str] = field(default_factory=list)
    requires_confirmation: bool = True


@dataclass
class AnalysisResult:
    findings: list[Finding] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    summary_lines: list[str] = field(default_factory=list)


def _section(result: AuditResult, title: str) -> str:
    for s in result.sections:
        if s.title == title:
            return s.output if not s.skipped else ""
    return ""


def _parse_disk_percent(df_output: str) -> int | None:
    for line in df_output.splitlines():
        if re.match(r"^/dev/\S+\s+\d", line) and " /" in line.split()[-1:]:
            pass
        parts = line.split()
        if len(parts) >= 5 and parts[4].endswith("%") and parts[-1] == "/":
            try:
                return int(parts[4].rstrip("%"))
            except ValueError:
                pass
    for line in df_output.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[-1] == "/" and parts[4].endswith("%"):
            return int(parts[4].rstrip("%"))
    return None


def _parse_du_top(output: str, min_mb: float = 100) -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    for line in output.splitlines()[1:]:
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        size_s, path = parts[0].strip(), parts[1].strip()
        mb = _size_to_mb(size_s)
        if mb >= min_mb:
            items.append((path, mb))
    return items


def _size_to_mb(size: str) -> float:
    size = size.strip().upper()
    if size.endswith("G"):
        return float(size[:-1]) * 1024
    if size.endswith("M"):
        return float(size[:-1])
    if size.endswith("K"):
        return float(size[:-1]) / 1024
    if size.endswith("T"):
        return float(size[:-1]) * 1024 * 1024
    try:
        return float(size) / (1024 * 1024)
    except ValueError:
        return 0


def _parse_mem_hogs(ps_output: str, threshold_pct: float = 10) -> list[tuple[str, float, str]]:
    hogs: list[tuple[str, float, str]] = []
    for line in ps_output.splitlines()[1:]:
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        try:
            mem = float(parts[3])
        except ValueError:
            continue
        if mem >= threshold_pct:
            cmd = parts[10][:120]
            hogs.append((parts[0], mem, cmd))
    return hogs


def _git_paths(git_output: str) -> set[str]:
    return {p.strip() for p in git_output.splitlines() if p.strip()}


def _running_systemd_units(output: str) -> list[str]:
    units: list[str] = []
    for line in output.splitlines():
        m = re.match(r"^\s*(\S+\.service)\s+", line)
        if m:
            name = m.group(1)
            if not name.startswith(("systemd-", "user@", "getty@", "serial-getty@")):
                units.append(name)
    return units


def _has_docker_dangling(docker_df: str) -> bool:
    return "<none>" in docker_df


def _has_unused_dashy(docker_df: str) -> bool:
    return "dashy" in docker_df.lower() and "CONTAINERS" in docker_df


def analyze(result: AuditResult) -> AnalysisResult:
    out = AnalysisResult()
    host = result.host

    df = _section(result, "Disk — df -h")
    disk_pct = _parse_disk_percent(df)
    opt_du = _section(result, "Velikost /opt")
    mem_ps = _section(result, "Top procesy (RAM)")
    docker_df = _section(result, "Docker — disk usage")
    git_out = _section(result, "Git repozitáře")
    systemd_run = _section(result, "Systemd — běžící služby")
    ports = _section(result, "Síť — poslouchající porty")
    predicapp_du = _section(result, "PredicApp — detail /opt")
    hanz_agent_du = _section(result, "hanz-agent — detail /opt")

    git_paths = _git_paths(git_out)
    units = _running_systemd_units(systemd_run)
    opt_dirs = _parse_du_top(opt_du, min_mb=50)

    # --- Findings ---

    if disk_pct is not None:
        if disk_pct >= 85:
            out.findings.append(
                Finding(
                    "critical",
                    f"Disk z {disk_pct} % zaplněný",
                    "Zbývá málo místa — riziko pádů služeb a failed zápisů.",
                )
            )
        elif disk_pct >= 70:
            out.findings.append(
                Finding(
                    "warning",
                    f"Disk na {disk_pct} %",
                    "Není kritické, ale /opt rychle roste — plánuj úklid.",
                )
            )

    for path, mb in opt_dirs:
        if "PredicApp" in path and mb >= 5000:
            out.findings.append(
                Finding(
                    "warning",
                    f"PredicApp zabírá ~{mb/1024:.1f} GB",
                    "Největší spotřeba disku na serveru.",
                )
            )
        if "hanz-agent" in path and mb >= 500:
            out.findings.append(
                Finding(
                    "warning",
                    f"hanz-agent adresář ~{mb:.0f} MB",
                    "Pravděpodobně LLM modely — zkontroluj velikost souborů.",
                )
            )
        if re.search(r"zaloha|_backup|^/opt/ZAL", path, re.I) and mb >= 100:
            out.findings.append(
                Finding(
                    "info",
                    f"Záloha/kopie: {path} (~{mb:.0f} MB)",
                    "Kandidát na archivaci mimo SD kartu po ověření.",
                )
            )

    for user, mem_pct, cmd in _parse_mem_hogs(mem_ps, 15):
        out.findings.append(
            Finding(
                "warning" if mem_pct >= 20 else "info",
                f"Vysoká RAM: {mem_pct:.1f} % ({user})",
                cmd,
            )
        )

    if "192.168.1.3" in mem_ps and host == "192.168.1.5":
        out.findings.append(
            Finding(
                "warning",
                "hanz-agent bind na starou IP 192.168.1.3",
                f"Pi je na {host}, služba nemusí být z LAN dostupná.",
            )
        )

    if _has_docker_dangling(docker_df):
        out.findings.append(
            Finding(
                "info",
                "Docker — visící (dangling) images",
                "Staré vrstvy image zabírají místo (~stovky MB).",
            )
        )

    if "cloudflared" in mem_ps and "--token" in mem_ps:
        out.findings.append(
            Finding(
                "warning",
                "Cloudflared token viditelný v process listu",
                "Token uniká do auditů a ps — přesuň do EnvironmentFile.",
            )
        )

    # služby bez gitu
    known_paths = {
        "predicapp": "/opt/PredicApp",
        "hanz-agent": "/opt/hanz-agent",
        "stream-web": "/opt/stream_server",
        "whiteboard-hub": "/opt/whiteboard-hub",
        "lora-hub": "/opt/lora2",
        "appmeteo": "/opt/HanzMeteo",
        "app-m": "/opt/meteo3",
    }
    for unit, path in known_paths.items():
        if any(unit.replace("-", "") in u.replace("-", "") for u in units):
            if path not in git_paths and not _has_git(path, git_paths):
                out.findings.append(
                    Finding(
                        "info",
                        f"{unit} běží bez git repozitáře",
                        f"Cesta {path} není verzovaná.",
                    )
                )

    # --- Recommendations ---

    if disk_pct and disk_pct >= 70:
        steps = [
            "Spusť audit znovu za týden a porovnej růst /opt.",
            "Projdi největší složky v sekci PredicApp detail.",
        ]
        if any("zaloha" in p.lower() or "/ZAL" in p for p, _ in opt_dirs):
            steps.append(
                "Po ověření záloh přesuň zaloha_predicapp_all a /opt/ZAL na NAS/PC a smaž z Pi."
            )
        out.recommendations.append(
            Recommendation(
                1,
                f"Disk {disk_pct} % — /opt dominuje ({opt_du.splitlines()[0].split()[0] if opt_du else '?'})",
                "Systematický úklid /opt: PredicApp data, staré zálohy, nepoužívané modely.",
                steps,
            )
        )

    if predicapp_du:
        out.recommendations.append(
            Recommendation(
                2,
                "PredicApp zabírá nejvíc místa",
                "Identifikuj největší podsložky (modely, logy, cache, CSV) a nastav retenci.",
                [
                    "Na Pi: du -xhd2 /opt/PredicApp | sort -hr | head -20",
                    "Logy starší 30 dní: najít *.log, *.csv a zkomprimovat nebo rotovat.",
                    "Modely/weights: ponechat jen aktivní verzi, zbytek archivovat mimo Pi.",
                    "Před mazáním: tar zaf záloha do /mnt/nas nebo PC.",
                ],
            )
        )

    mem_hogs = list(_parse_mem_hogs(mem_ps, 15))
    if mem_hogs and mem_hogs[0][1] >= 15:
        _user, top_pct, top_cmd = mem_hogs[0]
        short_cmd = top_cmd[:100] if top_cmd else "?"
        out.recommendations.append(
            Recommendation(
                2,
                f"Vysoká RAM — {top_pct:.0f} % ({short_cmd})",
                "Zjisti, proč proces spotřebovává tolik paměti, a navrhni bezpečnou optimalizaci.",
                [
                    "free -h && ps aux --sort=-%mem | head -10",
                    "Porovnej RSS vs VSZ — leak vs normální cache.",
                    "systemctl status <služba> — kolik paměti hlásí systemd.",
                    "Docker: docker stats --no-stream (pokud jde o kontejner).",
                ],
                requires_confirmation=False,
            )
        )

    if hanz_agent_du or any("hanz-agent" in c for _, _, c in _parse_mem_hogs(mem_ps, 10)):
        out.recommendations.append(
            Recommendation(
                3,
                "hanz-agent — vysoká RAM a velký adresář",
                "Optimalizuj model nebo načítání (llama.cpp).",
                [
                    "Zkontroluj velikost .gguf v /opt/hanz-agent (viz audit).",
                    "Zvaž menší quant (Q4_K_M místo Q8) nebo lazy-load jen při dotazu.",
                    "V systemd unit: omez paměť nebo restart při překročení limitu.",
                    "Oprav bind IP: --host 0.0.0.0 nebo aktuální IP místo 192.168.1.3.",
                ],
            )
        )

    if _has_docker_dangling(docker_df) or _has_unused_dashy(docker_df):
        out.recommendations.append(
            Recommendation(
                4,
                "Docker — nevyužívané images",
                "Uvolni místo bez dopadu na běžící kontejnery.",
                [
                    "docker image ls  # zkontroluj <none> a dashy",
                    "docker image prune -f  # smaže dangling",
                    "docker rmi lissy93/dashy:latest  # pokud Dashy nepoužíváš",
                    "docker builder prune -f  # build cache ~90 MB",
                ],
            )
        )

    if "cloudflared" in " ".join(units):
        out.recommendations.append(
            Recommendation(
                5,
                "Cloudflared token v příkazové řádce",
                "Bezpečnější konfigurace tunelu.",
                [
                    "sudo mkdir -p /etc/cloudflared",
                    "Token dej do /etc/cloudflared/token (chmod 600), ne do ExecStart.",
                    "Uprav cloudflared.service: EnvironmentFile=/etc/cloudflared/env",
                    "sudo systemctl daemon-reload && sudo systemctl restart cloudflared",
                ],
            )
        )

    # unknown ports
    unknown_ports = []
    for port in ("4010", "8085", "5000"):
        if f":{port} " in ports or f":{port}\t" in ports:
            unknown_ports.append(port)
    if unknown_ports:
        out.recommendations.append(
            Recommendation(
                6,
                f"Neidentifikované porty: {', '.join(unknown_ports)}",
                "Zmapuj procesy a rozhodni, zda službu nechat, chránit nebo vypnout.",
                [
                    f"sudo ss -tulpn | grep -E ':({'|'.join(unknown_ports)}) '",
                    "Porovnej s systemd unit a /opt — doplnit do services.yaml.",
                    "Nepoužívané služby: systemctl disable --now <unit>.",
                ],
                requires_confirmation=True,
            )
        )

    out.recommendations.append(
        Recommendation(
            7,
            "Chybějící inventura a git",
            "Srovnej deploy a dokumentaci služeb.",
            [
                "Použij vygenerovaný config/services.yaml — doplň category: personal|work.",
                "Pro každou běžící app bez gitu: git init + první commit + remote.",
                "Jedna systemd unit = jeden README s portem, závislostmi a zálohou.",
                "Work služby označ protected: true — agent je nebude měnit.",
            ],
            requires_confirmation=False,
        )
    )

    out.recommendations.append(
        Recommendation(
            8,
            "Monitoring růstu dat",
            "Prevence opakovaného plného disku.",
            [
                "Spouštěj audit týdně — app porovná diff oproti minulému MD.",
                "Nastav journald SystemMaxUse=500M v /etc/systemd/journald.conf.",
                "Docker: log-driver json-file, max-size 50m, max-file 3 v compose.",
                "InfluxDB: retention policy 180d (v auditu volumes zatím malé).",
            ],
            requires_confirmation=True,
        )
    )

    out.recommendations.sort(key=lambda r: r.priority)

    # summary
    n_crit = sum(1 for f in out.findings if f.severity == "critical")
    n_warn = sum(1 for f in out.findings if f.severity == "warning")
    out.summary_lines = [
        f"Nalezeno {len(out.findings)} zjištění ({n_crit} kritických, {n_warn} varování).",
        f"Připraveno {len(out.recommendations)} doporučených řešení.",
        f"Běží ~{len(units)} vlastních systemd služeb.",
    ]

    return out
