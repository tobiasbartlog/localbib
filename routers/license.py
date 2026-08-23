"""Router: Lizenzschluessel-Aktivierung gegen Polar (ADR-0015).

Serves POST /api/license/activate and GET /api/license/status.

Die Aktivierung ist der **einzige** Netzaufruf dieses Routers: der Schluessel
wird genau einmal gegen Polars Aktivierungs-API geprueft, das Ergebnis landet
im App-Settings-Key-Value-Store und wird danach fuer immer geglaubt — keine
Nachpruefung, kein periodischer Check, kein Phone-Home. `GET /status` liest
ausschliesslich lokal; ein aktivierter Rechner funktioniert dauerhaft offline.

Das Aktivierungslimit (2 Geraete) setzt Polar durch, nicht dieser Client.

`GET /status` liefert seit #144 zusaetzlich den abgeleiteten Testphasen-Zustand
(`trial`, `blocked`, `legacy_supporter`); gerechnet wird das in der reinen
`services.license_state`, gespeichert ist nur der Erststart-Zeitstempel.

DB-Zugriff pro Request via Database(Config.DB_PATH). Kein I/O beim Import.
"""

from __future__ import annotations

import socket
from datetime import datetime

import httpx

from fastapi import APIRouter
from pydantic import BaseModel

from literature_manager import Config, Database
from services import license_state

router = APIRouter()

# Polar Customer-Portal-Endpunkt: aktiviert einen Lizenzschluessel und belegt
# einen der beiden Aktivierungsplaetze. Kein Token noetig — der Schluessel des
# Kunden ist die Berechtigung.
ACTIVATE_PATH = "/v1/customer-portal/license-keys/activate"

# Fehlertexte. Jede Ursache bekommt einen eigenen Satz, weil der naechste
# Schritt des Kunden ein anderer ist: nachtippen, Geraet freigeben, spaeter
# erneut versuchen, oder Support kontaktieren.
ERR_EMPTY = "Bitte gib deinen Lizenzschlüssel ein."
ERR_INVALID = (
    "Dieser Lizenzschlüssel ist ungültig. Bitte prüfe die Eingabe — "
    "meistens ist beim Kopieren ein Zeichen verloren gegangen."
)
ERR_LIMIT = (
    "Dieser Lizenzschlüssel ist bereits auf zwei Geräten aktiviert. "
    "Gib in deinem Polar-Konto ein Gerät frei und versuche es erneut."
)
ERR_NETWORK = (
    "Der Lizenzserver ist nicht erreichbar. Prüfe deine Internetverbindung "
    "und versuche es später erneut — deine Bibliothek bleibt nutzbar."
)

# App-Setting, in dem der Erststart steht — der Anker der 14-Tage-Testphase.
# Gestempelt wird er beim Start (webapp.py); hier wird er nur gelesen.
FIRST_RUN_SETTING = "license_first_run_at"


def _err_unexpected(status: int) -> str:
    """Antwort, die laut Polars API-Vertrag nicht vorkommen kann.

    Sie dem Kunden als "dein Schlüssel ist falsch" zu verkaufen waere gelogen
    und schickt ihn auf die falsche Faehrte — also sagen, was ist, samt Code
    fuer den Support."""
    return (
        f"Die Aktivierung ist unerwartet fehlgeschlagen (Fehler {status}). "
        "Bitte versuche es erneut und melde dich beim Support, wenn es bleibt."
    )


class LicenseKeyRequest(BaseModel):
    key: str


def _get_db() -> Database:
    return Database(Config.DB_PATH)


def _device_label() -> str:
    """Name, unter dem diese Aktivierung in Polar auftaucht."""
    try:
        host = socket.gethostname().strip()
    except Exception:
        host = ""
    return f"LocalBib ({host})" if host else "LocalBib"


def _mask(key: str) -> str:
    """Nur die letzten vier Zeichen zeigen — genug zum Wiedererkennen."""
    key = (key or "").strip()
    if len(key) <= 4:
        return "••••" if key else ""
    return "••••" + key[-4:]


def _error_for(resp: httpx.Response) -> str:
    """Polar-Status -> der Satz, den der Kunde lesen soll.

    Die Zuordnung folgt Polars dokumentiertem Vertrag fuer diesen Endpunkt
    (404 / 403 / 422), nicht dem englischen Fliesstext der Antwort: den zu
    durchsuchen hiesse, dass eine Wortaenderung bei Polar dem Kunden still den
    falschen naechsten Schritt nennt. 403 heisst bei Polar "Aktivierung nicht
    moeglich oder Limit erreicht" — bei unserem Produkt (Limit 2) ist es das
    Limit."""
    if resp.status_code == 404:
        return ERR_INVALID
    if resp.status_code == 403:
        return ERR_LIMIT
    if resp.status_code >= 500:
        return ERR_NETWORK  # Polars Problem, nicht der Schluessel des Kunden
    return _err_unexpected(resp.status_code)


def _remaining_activations(result: dict) -> int | None:
    """Verbleibende Aktivierungsplaetze, wenn Polars Antwort sie mitliefert.

    Polars Aktivierungs-Endpunkt liefert (Stand heute) das Limit
    (``license_key.limit_activations``), aber keine Zahl bereits belegter
    Plaetze in derselben Antwort — aus dem Limit allein liesse sich "noch
    frei" nur raten, und genau das verbietet #156: das Limit setzt Polar
    durch, nicht dieser Client. Ein zweiter Netzaufruf, um die Zahl zu
    ermitteln, waere ein Bruch mit der "genau ein Netzaufruf"-Zusage oben
    — also wird nur durchgereicht, was Polar von sich aus als Rest
    beziffert (heute: nichts; ein spaeteres API-Feld faellt hier automatisch
    durch)."""
    candidates = [result]
    nested = result.get("license_key")
    if isinstance(nested, dict):
        candidates.append(nested)
    for source in candidates:
        remaining = source.get("activations_remaining")
        if isinstance(remaining, int) and not isinstance(remaining, bool):
            return remaining
    return None


def _status_payload(db: Database) -> dict:
    """Lizenz- *und* Testphasen-Zustand in einer Antwort (#144).

    Der Trial-Zaehler wird hier abgeleitet, nicht gespeichert: gespeichert ist
    nur der Erststart-Stempel. So kann kein zweiter, veralteter Zustand
    entstehen, und die SPA muss nicht selbst rechnen, ob sie das Gate zeigt."""
    activated = db.get_app_setting("license_activated") == "1"
    state = license_state.derive(
        activated=activated,
        first_run_at=db.get_app_setting(FIRST_RUN_SETTING),
        is_frozen=license_state.running_frozen(),
        legacy_supporter=db.get_app_setting("is_supporter") == "1",
    )
    return {
        "activated": activated,
        "key": _mask(db.get_app_setting("license_key")),
        "activated_at": db.get_app_setting("license_activated_at"),
        "checkout_url": Config.POLAR_CHECKOUT_URL,
        "price_display": license_state.PRICE_DISPLAY,
        **state.as_dict(),
    }


@router.post("/api/license/activate")
async def activate_license(data: LicenseKeyRequest) -> dict:
    """Aktiviert einen Lizenzschluessel — genau einmal, danach nie wieder."""
    db = _get_db()
    key = (data.key or "").strip()

    # Schon aktiviert: lokal antworten. Das ist die Garantie "genau einmal" —
    # ein zweiter Klick darf keinen weiteren Aktivierungsplatz verbrennen.
    if db.get_app_setting("license_activated") == "1":
        return {**_status_payload(db), "already_activated": True}

    if not key:
        return {"activated": False, "error": ERR_EMPTY}

    try:
        resp = httpx.post(
            f"{Config.POLAR_API_BASE}{ACTIVATE_PATH}",
            json={
                "key": key,
                "organization_id": Config.POLAR_ORGANIZATION_ID,
                "label": _device_label(),
            },
            timeout=10,
        )
    except Exception:
        return {"activated": False, "error": ERR_NETWORK}

    if resp.status_code != 200:
        return {"activated": False, "error": _error_for(resp)}

    try:
        result = resp.json() or {}
    except Exception:
        result = {}

    db.set_app_setting("license_activated", "1")
    db.set_app_setting("license_key", key)
    db.set_app_setting("license_activation_id", str(result.get("id", "")))
    db.set_app_setting("license_activated_at", datetime.utcnow().isoformat())

    payload = _status_payload(db)
    # Nur auftauchen, wenn Polar es tatsaechlich mitschickt (#156) — sonst
    # bleibt die Zeile in der SPA ganz weg, statt eine Zahl zu erfinden.
    remaining = _remaining_activations(result)
    if remaining is not None:
        payload["activations_remaining"] = remaining
    return payload


@router.get("/api/license/status")
async def get_license_status() -> dict:
    """Lizenz- und Testphasenstatus aus lokalem Zustand — ohne Netzaufruf."""
    return _status_payload(_get_db())
