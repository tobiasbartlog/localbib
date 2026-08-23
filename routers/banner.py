"""Router: purchase-prompt banner state machine.

Serves GET /api/banner/state and POST /api/banner/dismiss.

Seit #144 spiegelt der Hinweis die Testphase: er nennt die verbleibenden Tage
(`trial` im Payload), schweigt bei aktivierten Installationen und schweigt auch
dann, wenn ohnehin der blockierende Aktivierungsdialog steht — zwei Bitten
uebereinander sind eine zu viel. Die Rueckzugslogik (90 Tage, maximal zweimal)
bleibt unveraendert.

Die Ableitung des Testphasen-Zustands liegt in `services.license_state`, damit
Lizenz- und Banner-Router dieselbe Rechnung teilen, ohne einander zu importieren.
DB-Zugriff pro Request via Database(Config.DB_PATH). Kein I/O beim Import.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from literature_manager import Config, Database
from services import license_state

router = APIRouter()

# Der Erststart-Stempel, an dem die Testphase haengt (gestempelt in webapp.py).
FIRST_RUN_SETTING = "license_first_run_at"


def _get_db() -> Database:
    return Database(Config.DB_PATH)


def _license_state(db: Database) -> tuple[bool, license_state.LicenseState]:
    """(aktiviert?, abgeleiteter Zustand) — dieselbe Rechnung wie /api/license."""
    activated = db.get_app_setting("license_activated") == "1"
    return activated, license_state.derive(
        activated=activated,
        first_run_at=db.get_app_setting(FIRST_RUN_SETTING),
        is_frozen=license_state.running_frozen(),
        legacy_supporter=db.get_app_setting("is_supporter") == "1",
    )


def _banner_should_show(db: Database, activated: bool,
                        state: license_state.LicenseState) -> bool:
    """Pure state machine: return True if banner should be shown now."""
    # Wer aktiviert hat, hat bezahlt - der Kaufhinweis schweigt (ADR-0015).
    # Ein altes is_supporter=1 aus der Lemon-Squeezy-Zeit zaehlt bewusst nicht
    # mehr: solche Installationen gelten als nicht aktiviert (#144).
    if activated:
        return False

    # Abgelaufene Testphase im frozen Build: der blockierende Dialog stellt die
    # Frage bereits, und zwar unuebersehbar. Der Streifen darueber waere Laerm.
    if state.blocked:
        return False

    start_count = int(db.get_app_setting("app_start_count") or "0")
    if start_count <= 1:
        return False  # Never on first start

    dismissed_at_str = db.get_app_setting("banner_dismissed_at")
    if not dismissed_at_str:
        return True  # Never dismissed -> show

    dismissed_at = datetime.fromisoformat(dismissed_at_str)
    days_since_dismiss = (datetime.utcnow() - dismissed_at).days
    dismiss_count = int(db.get_app_setting("banner_dismiss_count") or "0")

    if days_since_dismiss < 90:
        return False  # Still within 90-day suppression window

    # 90+ days passed: show once more if we haven't already re-shown
    return dismiss_count < 2  # Show max twice total (initial + one 90-day re-show)


@router.get("/api/banner/state")
async def get_banner_state() -> dict:
    """Return whether the purchase-prompt banner should be shown — and why.

    `trial` ist `None`, wo es keine Testphase gibt (Quellinstallation oder
    bereits aktiviert); die SPA zeigt dann ihren neutralen Kauftext."""
    db = _get_db()
    activated, state = _license_state(db)
    return {
        "show": _banner_should_show(db, activated, state),
        "trial": state.trial,
        "blocked": state.blocked,
    }


@router.post("/api/banner/dismiss")
async def dismiss_banner() -> dict:
    """Record that the user dismissed the banner."""
    db = _get_db()
    count = int(db.get_app_setting("banner_dismiss_count") or "0")
    db.set_app_setting("banner_dismissed_at", datetime.utcnow().isoformat())
    db.set_app_setting("banner_dismiss_count", str(count + 1))
    return {"status": "ok"}
