from __future__ import annotations

import re


def redact_secrets(text: str) -> str:
    """Maskuje tokeny a klíče v audit výstupech."""
    text = re.sub(
        r"(--token\s+)\S+",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
        "[REDACTED_JWT]",
        text,
    )
    text = re.sub(r"sk-[A-Za-z0-9]{20,}", "sk-[REDACTED]", text)
    return text
