"""Service: recommend a reasoning + a fast model from a provider's model list.

PURE per the side-effect-free invariant (CLAUDE.md / ADR-0006): no DB writes,
no logging config, no filesystem ops, no HTTP. The router
(``routers/settings.py``) performs the LLM call and persists nothing; this
module only builds the prompt-independent heuristic and parses/validates the
LLM's answer.

Two entry points:

* ``heuristic_suggest(model_ids)`` — offline name-pattern fallback.
* ``parse_llm_suggestion(content, model_ids)`` — validate an LLM JSON answer
  against the real model list; returns ``None`` when unusable so the caller can
  fall back to the heuristic.
"""

from __future__ import annotations

from typing import Optional

from llm_client import LLMClient

# Name fragments that hint at a small/fast/cheap model vs. a strong/reasoning one.
_FAST_HINTS = (
    "mini", "flash", "fast", "small", "haiku", "lite", "nano", "tiny",
    "turbo", "8b", "7b", "3b", "1b", "instant",
)
_REASONING_HINTS = (
    "opus", "pro", "ultra", "large", "reason", "sonnet", "gpt-5", "gpt5",
    "o1", "o3", "o4", "r1", "70b", "72b", "405b", "deepseek-reasoner", "think",
)


def _score(model_id: str, hints: tuple[str, ...]) -> int:
    low = model_id.lower()
    return sum(1 for h in hints if h in low)


def heuristic_suggest(model_ids: list[str]) -> Optional[dict]:
    """Pick a reasoning + fast model from ``model_ids`` by name patterns.

    Returns ``{"reasoning", "fast", "reasoning_reason", "fast_reason",
    "source": "heuristic"}`` or ``None`` when the list is empty."""
    ids = [m for m in model_ids if m]
    if not ids:
        return None

    reasoning = max(ids, key=lambda m: (_score(m, _REASONING_HINTS), len(m)))
    fast = max(ids, key=lambda m: (_score(m, _FAST_HINTS), -len(m)))

    # Avoid recommending the identical model for both when an alternative exists.
    if fast == reasoning and len(ids) > 1:
        alt = [m for m in ids if m != reasoning]
        fast = max(alt, key=lambda m: (_score(m, _FAST_HINTS), -len(m)))

    return {
        "reasoning": reasoning,
        "fast": fast,
        "reasoning_reason": "nach Namensmuster als staerkstes Modell eingeschaetzt",
        "fast_reason": "nach Namensmuster als schnelles/guenstiges Modell eingeschaetzt",
        "source": "heuristic",
    }


def build_suggest_prompt(model_ids: list[str]) -> str:
    """Prompt asking the reasoning model to pick the two best models."""
    listing = "\n".join(f"- {m}" for m in model_ids)
    return f"""Du kennst gaengige LLM-Modelle und ihre Staerken.
Aus der folgenden Liste verfuegbarer Modelle sollst du GENAU ZWEI auswaehlen:
1. das staerkste fuer anspruchsvolle Denk-/Analyseaufgaben (reasoning)
2. ein schnelles/guenstiges fuer einfache Extraktions- und Klassifikationsaufgaben (fast)

VERFUEGBARE MODELLE (nutze die IDs exakt so):
{listing}

Antworte NUR mit JSON, kein anderer Text:
{{"reasoning": "<id>", "fast": "<id>", "reasoning_reason": "<Halbsatz>", "fast_reason": "<Halbsatz>"}}"""


def parse_llm_suggestion(content: str, model_ids: list[str]) -> Optional[dict]:
    """Parse + validate the LLM answer against the real model list.

    Returns the suggestion dict (``source="llm"``) or ``None`` when the answer
    is unparseable or picks IDs that are not actually available."""
    # Prosa-tolerante Extraktion lebt zentral in LLMClient.parse_json.
    data = LLMClient.parse_json(content or "", expect=dict)
    if data is None:
        return None

    reasoning = str(data.get("reasoning") or "").strip()
    fast = str(data.get("fast") or "").strip()
    valid = set(model_ids)
    if reasoning not in valid or fast not in valid:
        return None

    return {
        "reasoning": reasoning,
        "fast": fast,
        "reasoning_reason": str(data.get("reasoning_reason") or "").strip(),
        "fast_reason": str(data.get("fast_reason") or "").strip(),
        "source": "llm",
    }
