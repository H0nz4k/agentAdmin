from __future__ import annotations

from pathlib import Path

from hanz_audit.analysis import AnalysisResult, analyze
from hanz_audit.audit import AuditResult
from hanz_audit.overview import format_overview
from hanz_audit.redact import redact_secrets


def _severity_icon(severity: str) -> str:
    return {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(severity, "•")


def analysis_to_markdown(analysis: AnalysisResult) -> str:
    lines = [
        "## Shrnutí",
        "",
    ]
    for s in analysis.summary_lines:
        lines.append(f"- {s}")
    lines.append("")

    if analysis.findings:
        lines.append("## Zjištěné problémy")
        lines.append("")
        for f in analysis.findings:
            lines.append(f"### {_severity_icon(f.severity)} {f.title}")
            lines.append("")
            lines.append(f.severity.upper() + f": {f.detail}")
            lines.append("")

    if analysis.recommendations:
        lines.append("## Navrhovaná řešení")
        lines.append("")
        for rec in analysis.recommendations:
            confirm = " *(vyžaduje potvrzení)*" if rec.requires_confirmation else ""
            lines.append(f"### {rec.priority}. {rec.problem}{confirm}")
            lines.append("")
            lines.append(f"**Řešení:** {rec.solution}")
            lines.append("")
            if rec.steps:
                lines.append("**Kroky:**")
                for step in rec.steps:
                    lines.append(f"- {step}")
                lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def audit_to_markdown(
    result: AuditResult,
    analysis: AnalysisResult | None = None,
    overview_text: str = "",
) -> str:
    if analysis is None:
        analysis = analyze(result)

    ts = result.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        f"# HanzHub Audit — {result.host}",
        "",
        f"**Čas:** {ts}  ",
        f"**Připojení:** `{redact_secrets(result.connection_info.replace(chr(10), ' | '))}`",
        "",
    ]

    if overview_text:
        lines.extend(["## Přehled pro člověka", "", "```text", overview_text, "```", ""])

    lines.append(analysis_to_markdown(analysis))
    lines.append("## Surová data auditu")
    lines.append("")

    for section in result.sections:
        lines.append(f"### {section.title}")
        lines.append("")
        lines.append("<details>")
        lines.append(f"<summary>Příkaz: <code>{section.command}</code></summary>")
        lines.append("")
        lines.append("```bash")
        lines.append(section.command)
        lines.append("```")
        lines.append("</details>")
        lines.append("")

        if section.skipped:
            lines.append(f"*Přeskočeno:* {section.skip_reason}")
        else:
            body = redact_secrets(section.output)
            lines.append("```")
            lines.append(body)
            if section.error:
                lines.append("")
                lines.append(f"[stderr exit={section.exit_code}]")
                lines.append(redact_secrets(section.error))
            lines.append("```")

        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*Vygenerováno HanzHub Audit*")
    return "\n".join(lines)


def recommendations_brief(analysis: AnalysisResult, limit: int = 5) -> str:
    lines = ["Top doporučení z auditu:", ""]
    for rec in analysis.recommendations[:limit]:
        lines.append(f"{rec.priority}. **{rec.problem}**")
        lines.append(f"   → {rec.solution}")
    return "\n".join(lines)


def save_audit_markdown(
    result: AuditResult,
    path: Path,
    analysis: AnalysisResult | None = None,
    overview_text: str = "",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if analysis is None:
        analysis = analyze(result)
    path.write_text(
        audit_to_markdown(result, analysis, overview_text),
        encoding="utf-8",
    )
    return path
