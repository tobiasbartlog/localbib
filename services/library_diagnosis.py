"""Library Diagnosis — what is wrong with the library, and what would fix it.

``diagnose()`` turns a snapshot (the items, the PDF files actually lying in
``all/``, the LLM probe results, the backup verdict) into **Findings**: one problem each, with a
severity and the fix actions that apply to it. It reads nothing and writes
nothing; ``routers/diagnosis.py`` gathers the snapshot and carries out a fix.

Finding shape::

    {"id": "missing_pdf:20", "kind": "missing_pdf", "severity": "error",
     "paper_id": 20, "title": "...", "params": {...},
     "fixes": ["assign_file", "clear_pdf", "delete_item"],
     "candidates": ["2019_Wangler_....pdf", ...]}   # only for assign_file

``candidates`` (an orphan: ``targets``) are ordered best guess first (title ↔
file name similarity). ``suggestion`` names the first one only when it is
similar enough to be a real guess — otherwise ``None`` and the user picks;
an unrelated stray file must never sit preselected.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from doi_discovery import canonical_arxiv_doi
from rapidfuzz import fuzz

SEVERITIES = ("error", "warning", "info")

# token_set_ratio at or above which the best candidate is preselected.
SUGGEST_AT = 70


def _norm(s: str) -> str:
    """File names compared without underscores and extension, so
    ``2019_Wangler_First.pdf`` meets its title."""
    return re.sub(r"[_\-]+", " ", re.sub(r"\.pdf$", "", s or "", flags=re.IGNORECASE)).lower()


def _similarity(a: str, b: str) -> float:
    return fuzz.token_set_ratio(_norm(a), _norm(b))


def _candidates(name: str, orphans: list[str]) -> dict:
    """``{candidates, suggestion}`` for an item that could take a stray file."""
    if not orphans:
        return {}
    ranked = sorted(orphans, key=lambda o: -_similarity(name, o))
    best = ranked[0] if _similarity(name, ranked[0]) >= SUGGEST_AT else None
    return {"candidates": ranked, "suggestion": best}


def _paper_label(p: dict) -> str:
    return (p.get("title") or "").strip() or (p.get("filename") or "").strip() or f"#{p['id']}"


def diagnose(papers: list[dict], pdf_files: Iterable[str], llm_probes: list[dict],
             backup_health: Optional[dict] = None) -> list[dict]:
    """All Findings for one snapshot, most severe first.

    ``papers``: ``{id, title, filename, doi, categorized}`` per item
    (``categorized``: has at least one category; absent counts as yes).
    ``pdf_files``: the base names of the PDFs lying in ``all/``.
    ``llm_probes``: the Startup check's problems (``llm_check.diagnosis_probes``)
    — ``{connection_id, label, tiers, status, model}`` with status ``auth`` /
    ``unreachable`` (one per connection) or ``model_missing`` (one per
    connection and model); ``ok`` entries are ignored.
    ``backup_health``: ``backup.health()``'s verdict — ``backup_none`` (info)
    or ``backup_overdue`` with ``days`` (warning); ``None`` is all fine.
    """
    files = set(pdf_files)
    referenced = {p["filename"] for p in papers if p.get("filename")}
    orphans = sorted(files - referenced)
    findings: list[dict] = []

    for p in papers:
        filename = (p.get("filename") or "").strip()
        base = {"paper_id": p["id"], "title": _paper_label(p)}
        if filename and filename not in files:
            fixes = (["assign_file"] if orphans else []) + ["clear_pdf", "delete_item"]
            findings.append({
                "id": f"missing_pdf:{p['id']}", "kind": "missing_pdf", "severity": "error",
                **base, "params": {"filename": filename}, "fixes": fixes,
                **_candidates(p.get("title") or filename, orphans),
            })
        elif not filename:
            fixes = (["fetch_oa"] if (p.get("doi") or "").strip() else []) \
                + (["assign_file"] if orphans else [])
            findings.append({
                "id": f"no_pdf:{p['id']}", "kind": "no_pdf", "severity": "info",
                **base, "params": {"doi": p.get("doi") or ""}, "fixes": fixes,
                **_candidates(p.get("title") or "", orphans),
            })

        doi = (p.get("doi") or "").strip()
        fixed = canonical_arxiv_doi(doi)
        if fixed:
            findings.append({
                "id": f"arxiv_doi_version:{p['id']}", "kind": "arxiv_doi_version",
                "severity": "warning", **base,
                "params": {"doi": doi, "fixed": fixed},
                "fixes": ["fix_doi"],
            })

    # An orphan is offered to the items that lack a usable PDF — best match first.
    without_pdf = [p for p in papers
                   if not (p.get("filename") or "").strip() or p["filename"] not in files]
    for name in orphans:
        ranked = sorted(without_pdf, key=lambda p: -_similarity(name, p.get("title") or ""))
        best = ranked[0]["id"] if ranked and _similarity(name, ranked[0].get("title") or "") >= SUGGEST_AT else None
        findings.append({
            "id": f"orphan_pdf:{name}", "kind": "orphan_pdf", "severity": "warning",
            "params": {"filename": name},
            "fixes": (["assign_file"] if ranked else []) + ["to_inbox"],
            "targets": [{"paper_id": p["id"], "title": _paper_label(p)} for p in ranked],
            "suggestion": best,
        })

    # One Finding with a number, not one per item: a fresh library can hold
    # hundreds of uncategorised items and the fix is a single bulk action.
    uncategorised = sum(1 for p in papers if not p.get("categorized", True))
    if uncategorised:
        findings.append({
            "id": "uncategorized", "kind": "uncategorized", "severity": "info",
            "params": {"count": uncategorised},
            "fixes": ["suggest_categories"],
        })

    for probe in llm_probes:
        params = {"connection": probe.get("label") or probe["connection_id"],
                  "tiers": ", ".join(probe.get("tiers") or [])}
        finding_id = probe["connection_id"]
        if probe.get("status") == "auth":
            kind, severity = "llm_auth", "error"
        elif probe.get("status") == "unreachable":
            kind, severity = "llm_unreachable", "warning"
        elif probe.get("status") == "model_missing":
            kind, severity = "llm_model_missing", "error"
            params["model"] = probe.get("model") or ""
            finding_id = f"{probe['connection_id']}:{params['model']}"
        else:
            continue
        findings.append({
            "id": f"{kind}:{finding_id}", "kind": kind, "severity": severity,
            "params": params,
            "fixes": ["open_llm_settings"],
        })

    if backup_health and backup_health.get("kind") == "backup_none":
        findings.append({"id": "backup_none", "kind": "backup_none", "severity": "info",
                         "params": {}, "fixes": ["open_backup_settings"]})
    elif backup_health and backup_health.get("kind") == "backup_overdue":
        findings.append({"id": "backup_overdue", "kind": "backup_overdue", "severity": "warning",
                         "params": {"days": backup_health.get("days")}, "fixes": ["open_backup_settings"]})

    order = {s: i for i, s in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: order[f["severity"]])
    return findings


def counts(findings: list[dict]) -> dict:
    out = {s: 0 for s in SEVERITIES}
    for f in findings:
        out[f["severity"]] += 1
    return out
