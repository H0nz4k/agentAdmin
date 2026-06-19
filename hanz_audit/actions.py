from __future__ import annotations

from hanz_audit.analysis import Recommendation


def build_try_prompt(rec: Recommendation, live_diagnostics: str = "") -> str:
    steps_block = ""
    if rec.steps:
        steps_block = "**Známé kroky z auditu:**\n" + "\n".join(
            f"- {s}" for s in rec.steps
        ) + "\n\n"
    live_block = ""
    if live_diagnostics:
        live_block = (
            "**Aktuální data z Pi (právě načteno přes SSH):**\n"
            f"```\n{live_diagnostics}\n```\n\n"
            "Analyzuj tato čerstvá data a porovnej je s auditem. "
            "Uveď konkrétní proces/službu, kolik MB/RAM zabírá a proč.\n\n"
        )
    elif not live_diagnostics:
        live_block = (
            "**Poznámka:** Live diagnostika nebyla dostupná (SSH nepřipojeno). "
            "Pracuj jen s daty z auditu.\n\n"
        )
    return (
        f"Pracuj na doporučení #{rec.priority} z auditu.\n\n"
        f"**Problém:** {rec.problem}\n"
        f"**Navržené řešení:** {rec.solution}\n"
        f"{steps_block}"
        f"{live_block}"
        "Režim: ZKUŠEBNĚ — pouze bezpečná diagnostika a read-only příkazy.\n"
        "Navrhni konkrétní kroky pro HanzHub (192.168.1.5), odhadni dopad a rizika.\n"
        "Nic nemaž a nic nerestartuj. Příkazy označ [READ-ONLY]."
    )


def build_fix_prompt(rec: Recommendation, live_diagnostics: str = "") -> str:
    steps_block = ""
    if rec.steps:
        steps_block = "**Východisko z auditu:**\n" + "\n".join(
            f"- {s}" for s in rec.steps
        ) + "\n\n"
    live_block = ""
    if live_diagnostics:
        live_block = (
            "**Aktuální data z Pi:**\n"
            f"```\n{live_diagnostics}\n```\n\n"
        )
    return (
        f"Chci vyřešit doporučení #{rec.priority} z auditu.\n\n"
        f"**Problém:** {rec.problem}\n"
        f"**Cíl:** {rec.solution}\n"
        f"{steps_block}"
        f"{live_block}"
        "Režim: VYŘEŠIT — máš k dispozici nástroje (tools) pro diagnostiku i servis.\n"
        "Postup:\n"
        "1. Nejdřív read-only diagnostika (get_memory_overview, get_service_status, read_service_logs).\n"
        "2. Urči příčinu a navrhni nejmenší bezpečný zásah.\n"
        "3. U úrovně 1 (restart, prune cache, docker prune dangling) můžeš nástroj spustit sám.\n"
        "4. U úrovně 2 (journal vacuum, system prune, mazání starých záloh) systém vyžádá potvrzení.\n"
        "5. Po každé změně ověř stav (get_service_status) a shrň výsledek.\n"
        "6. Jedna logická změna najednou. Protected/work služby neměň.\n"
        "Začni diagnostikou a prvním bezpečným krokem."
    )
