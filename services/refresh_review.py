"""Refresh Review: reviewable before/after rows for a library-wide refresh.

The Maintenance full refresh writes on its own. The Refresh Review splits the
same work into a *propose* pass that writes nothing and an *apply* pass that
writes only the rows the user ticked. This module owns the two pure halves of
that contract:

- turning a refreshed value (metadata Proposal, abstract, LLM category
  suggestions) into rows ``{id, paper_id, kind, before, after, ...}``;
- checking each ticked row against the item's *current* state, so a value the
  user edited between preview and apply is skipped and reported rather than
  overwritten.

Row kinds: ``field`` (one metadata column), ``category_add`` and
``category_remove``. A row that would empty a filled field carries
``clears: True``; together with ``category_remove`` those are the rows the UI
flags as destructive.

Side-effect-free: no DB, no filesystem, no network. ``routers/refresh_review.py``
gathers the inputs and persists the plan.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from author_names import normalize_people
from bib_fields import WRITABLE_FIELDS, missing_fields

_PEOPLE = ("authors", "editors")

SCOPES = ("categories", "metadata", "abstracts", "bibliographic")
"""What a refresh may cover. Page counts, citation counts and chunks are
derived values that overwrite nothing — they stay in the full refresh.

``bibliographic`` completes the bibliographic data from the DOI's or ISBN's
record (``bib_lookup``) and needs no PDF text. Its gap fills are written by
the run itself (user decision: empty fields fill automatically); a differing
value becomes an ordinary ``field`` row (:func:`bibliographic_rows`)."""

FIELDS = WRITABLE_FIELDS
"""Metadata columns a ``field`` row may write — the same set
``validation_policy.validate_apply`` persists (one constant, ``bib_fields``)."""


def _norm(value: Any) -> str:
    return "" if value is None else str(value).strip()


def scopes_for(paper: Dict[str, Any], has_categories: bool, scopes: Iterable[str],
               only_gaps: bool) -> set:
    """The scopes that actually apply to one item — the one place that decides
    which items a refresh touches, so ``propose`` and ``count`` cannot drift.

    ``only_gaps``: *categories* only for an item without any category,
    *metadata* only for an item without a DOI, *bibliographic* only for an
    item that lacks a field its type requires (``bib_fields.missing_fields``).
    *abstracts* never changes: it only ever acts on an empty abstract.
    *bibliographic* always needs a DOI or an ISBN to look up. Without
    ``only_gaps`` every selected scope applies to every item (the abstracts
    one still needs the gap)."""
    out = set()
    for s in scopes:
        if s == "categories" and (not only_gaps or not has_categories):
            out.add(s)
        elif s == "metadata" and (not only_gaps or not _norm(paper.get("doi"))):
            out.add(s)
        elif s == "abstracts" and not _norm(paper.get("abstract")):
            out.add(s)
        elif (s == "bibliographic" and (_norm(paper.get("doi")) or _norm(paper.get("isbn")))
              and (not only_gaps or missing_fields(paper))):
            out.add(s)
    return out


def select_items(papers: Iterable[Dict[str, Any]], categorised: set, scopes: Iterable[str],
                 only_gaps: bool) -> List[Tuple[Dict[str, Any], set]]:
    """``[(paper, applicable_scopes)]`` for every item the refresh affects."""
    scopes = list(scopes)
    picked = []
    for p in papers:
        applicable = scopes_for(p, p["id"] in categorised, scopes, only_gaps)
        if applicable:
            picked.append((p, applicable))
    return picked


def without_identifier(papers: Iterable[Dict[str, Any]], only_gaps: bool) -> int:
    """How many items the *bibliographic* scope would look at but cannot —
    no DOI and no ISBN to ask with ("skipped" beside the count). With
    ``only_gaps`` only those that lack a required field count."""
    return sum(1 for p in papers
               if not (_norm(p.get("doi")) or _norm(p.get("isbn")))
               and (not only_gaps or missing_fields(p)))


def row_id(paper_id: int, kind: str, key: Any) -> str:
    return f"{paper_id}:{kind}:{key}"


def field_rows(paper: Dict[str, Any], changes: Dict[str, Any],
               sources: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """One row per metadata field whose proposed value differs from the item's."""
    sources = sources or {}
    rows: List[Dict[str, Any]] = []
    for fld in FIELDS:
        if fld not in changes or changes[fld] is None:
            continue
        before, after = paper.get(fld), changes[fld]
        if _norm(before) == _norm(after):
            continue
        if fld in _PEOPLE and normalize_people(_norm(before)) == normalize_people(_norm(after)):
            continue  # the same people, only the separators differ (CrossRef's old ", " form)
        rows.append({
            "id": row_id(paper["id"], "field", fld),
            "paper_id": paper["id"],
            "kind": "field",
            "field": fld,
            "before": before,
            "after": after,
            "source": sources.get(fld, ""),
            "clears": bool(_norm(before)) and not _norm(after),
        })
    return rows


def bibliographic_rows(paper: Dict[str, Any],
                       conflicts: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The *bibliographic* scope's review rows: one ``field`` row per
    conflict of ``bib_lookup.compare`` (a filled field the record names
    differently) — the same rows, and so the same stale check on apply, as
    every other field. Its fills never become rows; the run writes them."""
    conflicts = list(conflicts or [])
    return field_rows(paper, {c["field"]: c["found"] for c in conflicts},
                      {c["field"]: c.get("source") or "" for c in conflicts})


def category_rows(paper_id: int, current: Iterable[Dict[str, Any]],
                  suggested: Iterable[Dict[str, Any]],
                  names: Dict[int, str]) -> List[Dict[str, Any]]:
    """Add rows for suggested categories the item lacks, remove rows for
    assigned categories the suggestion no longer contains.

    An empty suggestion yields no rows at all: it means the LLM failed or
    answered nothing usable, never "this item belongs nowhere" — proposing to
    strip every category on a timeout would be exactly the data loss the
    review exists to prevent.
    """
    suggested = list(suggested or [])
    if not suggested:
        return []
    current = list(current or [])
    current_ids = {c["id"] for c in current}
    suggested_ids = {s["category_id"] for s in suggested}
    rows: List[Dict[str, Any]] = []
    for s in suggested:
        cid = s["category_id"]
        if cid in current_ids:
            continue
        rows.append({
            "id": row_id(paper_id, "category_add", cid),
            "paper_id": paper_id,
            "kind": "category_add",
            "category_id": cid,
            "before": None,
            "after": names.get(cid, str(cid)),
            "confidence": s.get("confidence", 0.0),
        })
    for c in current:
        if c["id"] in suggested_ids:
            continue
        rows.append({
            "id": row_id(paper_id, "category_remove", c["id"]),
            "paper_id": paper_id,
            "kind": "category_remove",
            "category_id": c["id"],
            "before": names.get(c["id"], c.get("name") or str(c["id"])),
            "after": None,
            "assigned_by": c.get("assigned_by") or "",
        })
    return rows


def _stale_reason(row: Dict[str, Any], paper: Optional[Dict[str, Any]],
                  assigned: set, known_categories: set) -> Optional[str]:
    kind = row.get("kind")
    if paper is None:
        return "item_not_found"
    if kind == "field":
        fld = row.get("field")
        if fld not in FIELDS or "after" not in row:
            return "invalid"
        if _norm(paper.get(fld)) != _norm(row.get("before")):
            return "changed"
        return None
    if kind in ("category_add", "category_remove"):
        try:
            cid = int(row.get("category_id"))
        except (TypeError, ValueError):
            return "invalid"
        if kind == "category_add":
            if cid not in known_categories:
                return "category_not_found"
            return "already_assigned" if cid in assigned else None
        return None if cid in assigned else "already_removed"
    return "invalid"


def plan_apply(rows: Iterable[Dict[str, Any]],
               papers: Dict[int, Dict[str, Any]],
               assigned: Dict[int, set],
               known_categories: set) -> Tuple[Dict[int, Dict[str, Any]], List[Dict[str, Any]]]:
    """Group the ticked rows per item and drop every row that is stale.

    Returns ``(plan, skipped)``. ``plan[paper_id]`` is
    ``{"fields": {field: value}, "add": [(category_id, confidence)],
    "remove": [category_id]}``; ``skipped`` lists ``{id, reason}`` with reason
    ``changed`` (field edited since the preview), ``already_assigned``,
    ``already_removed``, ``category_not_found``, ``item_not_found`` or
    ``invalid``.
    """
    plan: Dict[int, Dict[str, Any]] = {}
    skipped: List[Dict[str, Any]] = []
    for row in rows:
        try:
            pid = int(row.get("paper_id"))
        except (TypeError, ValueError):
            skipped.append({"id": row.get("id"), "reason": "invalid"})
            continue
        reason = _stale_reason(row, papers.get(pid), assigned.get(pid, set()), known_categories)
        if reason:
            skipped.append({"id": row.get("id"), "reason": reason})
            continue
        entry = plan.setdefault(pid, {"fields": {}, "add": [], "remove": []})
        kind = row["kind"]
        if kind == "field":
            entry["fields"][row["field"]] = row["after"]
        elif kind == "category_add":
            entry["add"].append((int(row["category_id"]), float(row.get("confidence") or 0.0)))
        else:
            entry["remove"].append(int(row["category_id"]))
    return plan, skipped
