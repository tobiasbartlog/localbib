"""The readable release report the command line prints (``--dry-run`` and before
a publish). Split out of ``scripts/publish_public.py`` for the line budget (#95);
still reached through that module, so callers keep one import site.
"""

from __future__ import annotations

from scripts import public_exclusions
from scripts.release_notes import format_dropped


def format_preview(preview_) -> str:
    """``preview_`` is a ``scripts.publish_public.ReleasePreview``."""
    counts = preview_.counts()
    published = (
        f"{preview_.published_tag or '(untagged)'}"
        if preview_.published_exists else "nothing published yet (first release)"
    )
    lines = [
        "",
        f"Release preview — {preview_.tag}"
        + ("  (REPLACES the existing tag)" if preview_.replace_tag else ""),
        "=" * (len(preview_.tag) + 19),
        f"  ref            {preview_.ref}"
        + (f" @ {preview_.sha[:12]}" if preview_.sha else "")
        + ("  (detached worktree)" if preview_.from_worktree else ""),
        f"  source         {preview_.source}",
        f"  public repo    {preview_.repo_slug}  ({preview_.remote_url})",
        f"  published      {published}",
        f"  author         {preview_.author}",
        f"  export         {preview_.export_summary}",
        f"  publishable    {'yes' if preview_.publishable else 'NO'}",
        "",
        "Commit message:",
    ]
    lines += [f"  {line}" for line in preview_.message.rstrip("\n").splitlines()]
    lines += [
        "",
        f"Changes ({counts['added']} added, {counts['modified']} modified, "
        f"{counts['deleted']} deleted; {preview_.unchanged} unchanged):",
    ]
    if not preview_.changes:
        lines.append("  (nothing would change in the public repo)")
    for change in preview_.changes:
        counts_text = "binary" if change.binary else f"+{change.added_lines} -{change.removed_lines}"
        flags = "".join(sorted({"!" if a.blocking else "?" for a in change.alarms}))
        lines.append(f"  {change.marker} {change.path:<58} {counts_text:>14} {flags}")
    lines += public_exclusions.format_withheld(preview_.withheld)
    lines += ["", f"Alarms ({len(preview_.alarms)}):"]
    if not preview_.alarms:
        lines.append("  none")
    lines += [f"  {alarm}" for alarm in preview_.alarms]
    lines += ["", "Verification:", preview_.verification_report, "", "Drafted release notes:"]
    lines += [f"  {line}" for line in preview_.notes.text.rstrip("\n").splitlines()]
    # The filter's own audit trail. Printed right under the notes so the two are
    # read together: what went in, and what was held back for which reason.
    lines += ["", "Filtered out of the notes:"]
    lines += [f"  {line}" for line in format_dropped(preview_.notes).splitlines()]
    lines.append("")
    return "\n".join(lines)
