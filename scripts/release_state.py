"""What GitHub already holds under a release tag - none, a draft, or a release.

Split out of ``scripts/publish_public.py`` for the line budget (#95) and because
it is the one place the release path talks to the GitHub *releases* API rather
than to git. It exists for exactly one rule: a tag may be **replaced** (its
build failed, so nothing was ever offered under it) but never *re-released* -
an installed app that took an update under this version must never be able to
find a different installer under the same name afterwards.

The distinction is not visible from git: the tag exists either way. Only the
release object tells the two apart, and ``softprops/action-gh-release`` would
happily overwrite a published asset on the re-run a replaced tag triggers.
"""

from __future__ import annotations

import subprocess

#: The three answers, in the order of how much they cost to undo.
NONE, DRAFT, PUBLISHED = "none", "draft", "published"

_GH_TIMEOUT = 60.0


class ReleaseStateError(RuntimeError):
    """``gh`` could not answer - which must never be read as "no release"."""


def github_release_state(repo_slug: str, tag: str) -> str:
    """``NONE``, ``DRAFT`` or ``PUBLISHED`` for ``tag`` in ``repo_slug``.

    Raises :class:`ReleaseStateError` when the answer is unknown (no ``gh``, no
    auth, no network): the caller is about to move a tag, and "could not tell"
    is not a licence to do so.
    """
    try:
        done = subprocess.run(
            ["gh", "release", "view", tag, "--repo", repo_slug, "--json", "isDraft",
             "--jq", ".isDraft"],
            capture_output=True, text=True, timeout=_GH_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseStateError(f"gh release view {tag}: {exc}") from exc
    if done.returncode == 0:
        return DRAFT if done.stdout.strip() == "true" else PUBLISHED
    stderr = done.stderr.strip()
    if "release not found" in stderr.lower():
        return NONE
    raise ReleaseStateError(f"gh release view {tag}: {stderr or 'unknown failure'}")
