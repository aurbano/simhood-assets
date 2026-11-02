"""The licence allowlist, display names and AI labels (design D7, D8)."""

from __future__ import annotations

ALLOWLIST = ("CC0-1.0", "CC-BY-3.0", "CC-BY-4.0")
ORIGINAL_LICENCES = ("CC0-1.0", "CC-BY-4.0")

LICENCE_NAMES = {"CC0-1.0": "CC0 1.0", "CC-BY-3.0": "CC BY 3.0", "CC-BY-4.0": "CC BY 4.0"}
LICENCE_URLS = {
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "CC-BY-3.0": "https://creativecommons.org/licenses/by/3.0/",
    "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
}


def is_cc_by(licence: str | None) -> bool:
    return licence in ("CC-BY-3.0", "CC-BY-4.0")


def ai_label(status: str | None, evidence: bool, tool: str | None, model: str | None) -> str:
    """The plain words for a model's AI status (contract, board data).

    `evidence` says whether the model is a third-party work with saved licence evidence.
    """
    if status == "yes":
        if tool and model:
            return f"AI-generated with {tool} ({model})"
        return f"AI-generated with {tool or model or 'an unnamed tool'}"
    if status == "no":
        return "no AI, stated by the source" if evidence else "made without AI"
    return "not stated by the source"
