"""The core's library change feed — what ``LibraryApi.on_change`` delivers (#204).

Neutral leaf (stdlib + the Contract's constants): writers announce a change
*after* their commit, ``host_services._CoreLibraryApi.on_change`` subscribes an
Add-on's callback. Nothing here touches the database.

Event shape (plain dicts, the Contract's wire form)::

    {"type": "collection", "action": <one of COLLECTION_ACTIONS>,
     "collection_id": int}

An event says *that* something changed, not the new state — a subscriber
reads the state back through ``LibraryApi`` (``get_collection``,
``get_collection_refs``). Callbacks run synchronously on the writer's thread,
so they must be quick (record, schedule, debounce); an exception in one is
logged and never reaches the writer or the other subscribers.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from plugin_api import COLLECTION_EVENT, COLLECTION_EVENT_ACTIONS

logger = logging.getLogger(__name__)

COLLECTION = COLLECTION_EVENT

CREATED = "created"
RENAMED = "renamed"
UPDATED = "updated"          # description changed
ARCHIVED = "archived"
RESTORED = "restored"        # back from the archive
DELETED = "deleted"          # tombstoned; the frozen membership stays readable
MEMBERS_CHANGED = "members_changed"

# The Contract owns the vocabulary; the names above are spellings of it.
COLLECTION_ACTIONS = COLLECTION_EVENT_ACTIONS
assert set(COLLECTION_ACTIONS) == {
    CREATED, RENAMED, UPDATED, ARCHIVED, RESTORED, DELETED, MEMBERS_CHANGED}

_lock = threading.Lock()
_listeners: list[Callable[[dict], None]] = []


def subscribe(callback: Callable[[dict], None]) -> Callable[[], None]:
    """Register ``callback`` for every event; returns its unsubscriber
    (idempotent)."""
    with _lock:
        _listeners.append(callback)

    def unsubscribe() -> None:
        with _lock:
            if callback in _listeners:
                _listeners.remove(callback)

    return unsubscribe


def emit(event: dict) -> None:
    with _lock:
        listeners = list(_listeners)
    for callback in listeners:
        try:
            callback(dict(event))
        except Exception as exc:  # a subscriber never breaks the writer
            logger.warning("Library change listener failed: %s", exc)


def collection_event(action: str, collection_id: int) -> dict:
    if action not in COLLECTION_ACTIONS:
        raise ValueError(f"unknown collection action: {action!r}")
    return {"type": COLLECTION, "action": action, "collection_id": int(collection_id)}


def collection_changed(action: str, *collection_ids: int) -> None:
    """Announce ``action`` once per (distinct) Collection id — after the commit."""
    for cid in dict.fromkeys(collection_ids):
        if cid is not None:
            emit(collection_event(action, cid))


def listener_count() -> int:
    with _lock:
        return len(_listeners)
