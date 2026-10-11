"""Recompute technical data (#213) — the pure half.

Refresh block ② recomputes what LocalBib derives and the user never curates:
page counts, citation counts, text passages, the search index and the category
folders. ``routers/maintenance.py`` runs the steps and persists; this module
holds what needs no I/O — which steps exist and in which order they run, the
shape of the stream's events, and matching OpenAlex works back to items.

Side-effect free (ADR-0006): no DB, no filesystem, no network, no logging
configuration.
"""

from __future__ import annotations

# Run order. Passages come before the search index (the item vectors use the
# passages as a substitute abstract, the passage vectors need the passages),
# the category folders last.
STEPS = ("page_counts", "citations", "passages", "search_index", "category_folders")


class StepError(ValueError):
    """A request the endpoint refuses; ``code`` is the catalog key."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def normalize_steps(requested) -> list:
    """The requested steps in run order, each once.

    Raises ``StepError`` for an empty request (``error.recompute_no_step``)
    or an unknown step (``error.recompute_unknown_step``).
    """
    wanted = [s for s in (requested or []) if isinstance(s, str)]
    unknown = [s for s in wanted if s not in STEPS]
    if unknown:
        raise StepError("error.recompute_unknown_step")
    steps = [s for s in STEPS if s in wanted]
    if not steps:
        raise StepError("error.recompute_no_step")
    return steps


# Counters a step reports besides ``updated`` and ``errors``: the search index
# splits its vectors into items and passages, the category folders count the
# entries that had to be placed another way (a hard link across drives).
_EXTRA_COUNTERS = {
    "search_index": ("items", "passages"),
    "category_folders": ("fallbacks",),
}


def new_stats(steps) -> dict:
    """Counters per step: values recomputed and errors on the way, plus the
    step's own extras."""
    stats = {}
    for s in steps:
        stats[s] = {"updated": 0, "errors": 0}
        for extra in _EXTRA_COUNTERS.get(s, ()):
            stats[s][extra] = 0
    return stats


def percent(current: int, total: int) -> int:
    """Progress of one step; an empty step is complete."""
    if total <= 0:
        return 100
    return max(0, min(100, int(current * 100 / total)))


def progress_event(steps: list, step: str, current: int, total: int, stats: dict) -> dict:
    """A ``progress`` frame in the stream format of the former full refresh:
    step, percent and the numbers so far — plus the step's place in the run.
    ``message`` is the step's catalog key; the server sends no sentences
    (ADR-0018)."""
    return {
        "type": "progress",
        "message": f"maintenance.recompute.step.{step}",
        "step": step,
        "index": steps.index(step) + 1,
        "steps": list(steps),
        "current": current,
        "total": total,
        "percent": percent(current, total),
        "stats": stats,
    }


def done_event(steps: list, stats: dict, total_items: int) -> dict:
    """The closing ``done`` frame: the numbers per step and the error count."""
    return {
        "type": "done",
        "steps": list(steps),
        "stats": stats,
        "errors": sum(s.get("errors", 0) for s in stats.values()),
        "total": total_items,
    }


def normalize_doi(doi: str) -> str:
    """A DOI as OpenAlex reports it: no resolver prefix, lower case."""
    doi = (doi or "").strip()
    if doi.lower().startswith("http"):
        doi = doi.split("doi.org/")[-1]
    elif doi.lower().startswith("doi:"):
        doi = doi[4:]
    return doi.strip().lower().rstrip(".")


def match_works(papers: list, works: list) -> list:
    """``[(paper_id, work)]`` for every work whose DOI is an item's DOI.

    ``papers`` are ``{"id", "doi"}`` rows, ``works`` OpenAlex ``Work``-like
    objects with ``.doi``. Two items with the same DOI both get the work.
    """
    by_doi: dict = {}
    for p in papers:
        key = normalize_doi(p.get("doi") or "")
        if key:
            by_doi.setdefault(key, []).append(p["id"])
    pairs = []
    for work in works:
        for paper_id in by_doi.get(normalize_doi(getattr(work, "doi", "") or ""), []):
            pairs.append((paper_id, work))
    return pairs
