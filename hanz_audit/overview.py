from __future__ import annotations

import re
from datetime import datetime

from hanz_audit.agent_config import enabled_overview_sections, severity_label
from hanz_audit.analysis import AnalysisResult, analyze
from hanz_audit.audit import AuditResult


def _sec(result: AuditResult, title: str) -> str:
    for s in result.sections:
        if s.title == title:
            return s.output if not s.skipped else ""
    return ""


def _disk_pct(df: str) -> int | None:
    for line in df.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[-1] == "/" and parts[4].endswith("%"):
            return int(parts[4].rstrip("%"))
    return None


def _line_after_header(output: str, skip: int = 1) -> list[str]:
    lines = [ln for ln in output.splitlines() if ln.strip()]
    return lines[skip:]


def _format_table_rows(rows: list[tuple[str, str]], col1: int = 28) -> list[str]:
    out: list[str] = []
    for a, b in rows:
        out.append(f"  {a[:col1]:<{col1}}  {b}")
    return out


def _section_health(result: AuditResult, agent: dict) -> list[str]:
    th = agent.get("thresholds", {})
    warn = th.get("disk_warn_percent", 70)
    crit = th.get("disk_critical_percent", 85)

    df = _sec(result, "Disk — df -h")
    free = _sec(result, "Paměť — free")
    temp = _sec(result, "Teplota (RPi)").replace("temp=", "").replace("'C", " °C")
    uptime = _sec(result, "Uptime & load").splitlines()[0] if _sec(result, "Uptime & load") else "?"
    pct = _disk_pct(df)

    labels = agent.get("severity_labels", {})
    if pct is None:
        disk_verdict = "neznámý"
    elif pct >= crit:
        disk_verdict = labels.get("critical", "KRITICKÉ")
    elif pct >= warn:
        disk_verdict = labels.get("warning", "POZOR")
    else:
        disk_verdict = labels.get("ok", "OK")

    mem_line = ""
    for line in free.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            if len(parts) >= 7:
                mem_line = f"použito {parts[2]} z {parts[1]}, volných ~{parts[6]}"
            break

    rows = [
        ("Disk /", f"{pct} % zaplnění — {disk_verdict}" if pct else "?"),
        ("Paměť", mem_line or free.splitlines()[1] if free else "?"),
        ("Teplota CPU", temp or "?"),
        ("Uptime", uptime),
    ]
    return _format_table_rows(rows)


def _section_disk(result: AuditResult) -> list[str]:
    lines: list[str] = []
    opt = _line_after_header(_sec(result, "Velikost /opt"), 0)
    if opt:
        lines.append("Největší složky v /opt:")
        for row in opt[:8]:
            if "\t" in row:
                size, path = row.split("\t", 1)
                lines.append(f"  • {path.strip()} — {size.strip()}")

    big = _line_after_header(_sec(result, "Velké soubory v /opt (>50 MB)"), 0)
    if big and big[0] != "(prázdný výstup)":
        lines.append("")
        lines.append("Největší soubory (>50 MB):")
        for row in big[:8]:
            if "\t" in row:
                size, path = row.split("\t", 1)
                short = path.strip()
                if len(short) > 55:
                    short = "…" + short[-52:]
                lines.append(f"  • {size.strip()} — {short}")

    predic = _line_after_header(_sec(result, "PredicApp — detail /opt"), 0)
    if predic:
        lines.append("")
        lines.append("PredicApp (detail):")
        for row in predic[1:6]:
            if "\t" in row:
                size, path = row.split("\t", 1)
                lines.append(f"  • {path.strip()} — {size.strip()}")

    if not lines:
        lines.append("  (nedostatek dat z auditu)")
    return lines


def _section_memory(result: AuditResult, agent: dict) -> list[str]:
    th = agent.get("thresholds", {})
    warn = th.get("memory_warn_percent", 15)
    ps = _sec(result, "Top procesy (RAM)")
    lines: list[str] = []
    for line in ps.splitlines()[1:8]:
        parts = line.split(None, 10)
        if len(parts) < 11:
            continue
        try:
            mem = float(parts[3])
        except ValueError:
            continue
        cmd = parts[10]
        name = cmd.split("/")[-1].split()[0][:40]
        flag = " ← vysoká spotřeba" if mem >= warn else ""
        lines.append(f"  • {mem:4.1f} % RAM — {name}{flag}")
    if not lines:
        lines.append("  (nedostatek dat)")
    return lines


def _section_services(result: AuditResult) -> list[str]:
    lines: list[str] = []

    docker = _sec(result, "Docker — kontejnery")
    dlines = [ln for ln in docker.splitlines()[1:] if ln.strip()]
    if dlines:
        lines.append("Docker kontejnery:")
        for ln in dlines:
            name = ln.split()[0]
            status = "běží" if "Up" in ln else "stop"
            port = ""
            m = re.search(r"0\.0\.0\.0:(\d+)", ln)
            if m:
                port = f", port {m.group(1)}"
            lines.append(f"  • {name} — {status}{port}")

    units: list[str] = []
    for line in _sec(result, "Systemd — běžící služby").splitlines():
        m = re.match(r"^\s*(\S+\.service)\s+", line)
        if not m:
            continue
        u = m.group(1)
        if u.startswith(("systemd-", "user@", "getty@", "dbus", "ssh.service", "cron")):
            continue
        if any(x in u for x in ("NetworkManager", "ModemManager", "cups", "polkit", "rtkit", "accounts-daemon", "wpa_supplicant", "avahi", "bluetooth", "wayvnc-control", "udisks2", "serial-getty")):
            continue
        units.append(u.replace(".service", ""))

    if units:
        lines.append("")
        lines.append(f"Vlastní systemd služby ({len(units)}):")
        wrapped = ", ".join(units[:14])
        if len(units) > 14:
            wrapped += f", … (+{len(units) - 14})"
        lines.append(f"  {wrapped}")

    return lines or ["  (nedostatek dat)"]


def _section_findings(analysis: AnalysisResult, agent: dict) -> list[str]:
    if not analysis.findings:
        return ["  Žádné výrazné problémy nebo vše v normě."]
    lines: list[str] = []
    for f in analysis.findings:
        lab = severity_label(agent, f.severity)
        lines.append(f"  [{lab}] {f.title}")
        lines.append(f"         {f.detail}")
        lines.append("")
    return lines


def _section_recommendations(analysis: AnalysisResult) -> list[str]:
    if not analysis.recommendations:
        return ["  Žádná doporučení."]
    lines: list[str] = []
    for rec in analysis.recommendations:
        confirm = " (vyžaduje potvrzení)" if rec.requires_confirmation else ""
        lines.append(f"  {rec.priority}. {rec.problem}{confirm}")
        lines.append(f"     Řešení: {rec.solution}")
        if rec.steps:
            lines.append("     Kroky:")
            for step in rec.steps[:4]:
                lines.append(f"       - {step}")
        lines.append("")
    return lines


_SECTION_BUILDERS = {
    "health_snapshot": lambda r, a, an: _section_health(r, a),
    "disk": lambda r, a, an: _section_disk(r),
    "memory": lambda r, a, an: _section_memory(r, a),
    "services": lambda r, a, an: _section_services(r),
    "findings": lambda r, a, an: _section_findings(an, a),
    "recommendations": lambda r, a, an: _section_recommendations(an),
}


def format_overview(
    result: AuditResult,
    analysis: AnalysisResult | None,
    agent: dict,
    *,
    actions_in_panel: bool = False,
) -> str:
    if analysis is None:
        analysis = analyze(result)

    ts = result.timestamp.astimezone().strftime("%d.%m.%Y %H:%M")
    lines: list[str] = [
        f"HANZHUB — PŘEHLED AUDITU",
        f"Host: {result.host}  |  {ts}",
        "",
    ]

    intro = agent.get("overview", {}).get("intro", "")
    if intro:
        lines.append(intro.strip())
        lines.append("")

    for section in enabled_overview_sections(agent):
        sid = section.get("id", "")
        title = section.get("title", sid)
        lines.append("─" * 60)
        lines.append(title.upper())
        lines.append("─" * 60)

        static = section.get("static_text")
        if static:
            lines.append(static.strip())
        elif sid == "recommendations":
            if actions_in_panel:
                n = len(analysis.recommendations)
                lines.append(
                    f"  Připraveno {n} doporučení — použij tlačítka v panelu „Akce“ níže."
                )
            else:
                body = _SECTION_BUILDERS[sid](result, agent, analysis)
                lines.extend(body)
        elif sid in _SECTION_BUILDERS:
            body = _SECTION_BUILDERS[sid](result, agent, analysis)
            lines.extend(body)
        elif sid.startswith("custom_"):
            lines.append(section.get("static_text", "(prázdná vlastní sekce)").strip())
        else:
            lines.append(f"  (neznámá sekce '{sid}' — doplň handler nebo použij static_text)")

        lines.append("")

    lines.append("─" * 60)
    agent_name = agent.get("agent", {}).get("name", "HanzAgent")
    lines.append(f"Přehled vygenerován aplikací {agent_name}.")
    return "\n".join(lines)
