from __future__ import annotations

from hanz_audit.analysis import Recommendation


def build_try_prompt(rec: Recommendation) -> str:
    steps_block = ""
    if rec.steps:
        steps_block = "**Známé kroky z auditu:**\n" + "\n".join(
            f"- {s}" for s in rec.steps
        ) + "\n\n"
    return (
        f"Pracuj na doporučení #{rec.priority} z auditu.\n\n"
        f"**Problém:** {rec.problem}\n"
        f"**Navržené řešení:** {rec.solution}\n"
        f"{steps_block}"
        "Režim: ZKUŠEBNĚ — pouze bezpečná diagnostika a read-only příkazy.\n"
        "Navrhni konkrétní kroky pro HanzHub (192.168.1.5), odhadni dopad a rizika.\n"
        "Nic nemaž a nic nerestartuj. Příkazy označ [READ-ONLY]."
    )


def build_fix_prompt(rec: Recommendation) -> str:
    steps_block = ""
    if rec.steps:
        steps_block = "**Východisko z auditu:**\n" + "\n".join(
            f"- {s}" for s in rec.steps
        ) + "\n\n"
    return (
        f"Chci vyřešit doporučení #{rec.priority} z auditu.\n\n"
        f"**Problém:** {rec.problem}\n"
        f"**Cíl:** {rec.solution}\n"
        f"{steps_block}"
        "Režim: VYŘEŠIT — připrav plán opravy krok za krokem.\n"
        "U každého kroku uveď: příkaz, co zkontrolovat po něm, riziko.\n"
        "Destruktivní kroky označ [VYŽADUJE POTVRZENÍ] a navrhni zálohu před nimi.\n"
        "Začni prvním bezpečným krokem; další až po mém potvrzení."
    )
