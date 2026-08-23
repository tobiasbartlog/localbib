"""Trial-Lebenszyklus und Aktivierungs-Gate — reine Ableitung (ADR-0015, #144).

Der erste Start stempelt einen Zeitstempel; aus ihm, dem Aktivierungsflag und
der Frage "laeuft dieser Prozess als eingefrorenes Build?" faellt alles, was
der Nutzer zum Thema Lizenz zu sehen bekommt: wie viele Tage der Testphase
bleiben, wovon der Kaufhinweis spricht, und ob der blockierende
Aktivierungsdialog kommt.

Drei Regeln sind nicht verhandelbar:

* **Eine Quellinstallation wird nie getestet und nie gesperrt.** Das ist die
  AGPL-Zusage aus ADR-0015 — wer aus dem Quellcode startet, sieht keine
  Testphase, keinen Zaehler, keinen Dialog. ``trial`` ist dann ``None``,
  ``blocked`` immer ``False``.
* **Eine Aktivierung beendet die Testphase endgueltig.** Danach gibt es keinen
  Zaehler mehr, den man wieder anlaufen lassen koennte, und der Kaufhinweis
  schweigt.
* **Ein Altbestand mit Lemon-Squeezy-Supporter-Key wird nie stumm ausgesperrt.**
  Der alte Key war eine Quittung, kein Schluessel (ADR-0015), gilt also als
  nicht aktiviert — bekommt aber einen erklaerenden Satz samt Weg zum
  kostenlosen Code statt einer nackten Kaufwand.

Rein wie ``paper_matcher`` & Co.: kein DB-Zugriff, kein I/O, keine Persistenz.
Die Router lesen die App-Settings und reichen sie hier hinein.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

# Laenge der Testphase in Tagen (PRD #138). Eine Zahl, ein Ort.
TRIAL_DAYS = 14

# Der Preis der Lizenz, fertig formatiert fuer die Anzeige (issue #154). Eine
# Zahl-plus-Waehrung-Konstruktion wuerde die Dezimaltrennzeichen-Frage in jeden
# Aufrufer verschieben — dafuer gibt es keinen Gewinn, jeder Konsument will
# denselben gerenderten Text. Preisaenderung heisst: diese eine Zeile editieren.
PRICE_DISPLAY = "29,50 €"

_SECONDS_PER_DAY = 86400

# Der Satz fuer Installationen, die noch ein ``is_supporter=1`` aus der
# Lemon-Squeezy-Zeit tragen. Er muss zwei Dinge leisten: erklaeren, warum der
# alte Key nichts mehr tut, und den kostenlosen Weg nennen — sonst waere das
# genau die stumme Aussperrung, die ADR-0015 verbietet.
LEGACY_SUPPORTER_NOTE = (
    "Dieser Rechner trägt noch einen Supporter-Key aus dem alten Shop. "
    "Den Shop gibt es nicht mehr, deshalb lässt sich der alte Key hier nicht "
    "aktivieren — kaufen musst du aber nichts noch einmal: schreib kurz an "
    "support@localbib.com, du bekommst kostenlos einen Code für einen neuen "
    "Lizenzschlüssel."
)


def running_frozen() -> bool:
    """True im PyInstaller-Build, False bei einer Quellinstallation.

    Eigene Funktion statt eines ``getattr`` an jeder Aufrufstelle, damit das
    Gate genau *einen* Schalter hat — den Tests umlegen koennen, ohne ein
    zweites Verstaendnis von "eingefroren" zu erfinden."""
    return bool(getattr(sys, "frozen", False))


@dataclass(frozen=True)
class LicenseState:
    """Abgeleiteter Lizenzzustand — genau das, was UI und Banner brauchen."""

    is_frozen: bool
    trial: dict | None
    blocked: bool
    legacy_supporter: bool
    legacy_note: str

    def as_dict(self) -> dict:
        return {
            "is_frozen": self.is_frozen,
            "trial": self.trial,
            "blocked": self.blocked,
            "legacy_supporter": self.legacy_supporter,
            "legacy_note": self.legacy_note,
        }


def _parse(stamp: str | None) -> datetime | None:
    """ISO-Zeitstempel aus den App-Settings -> naives UTC-datetime, oder None."""
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp).strip())
    except (TypeError, ValueError):
        return None
    # Der Rest der Anwendung schreibt naive UTC-Stempel (``datetime.utcnow()``).
    # Ein aus Versehen zonenbehafteter Wert wird angeglichen statt zu einem
    # TypeError beim Subtrahieren zu fuehren.
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _trial_dict(started_at: datetime, now: datetime) -> dict:
    expires_at = started_at + timedelta(days=TRIAL_DAYS)
    remaining_seconds = (expires_at - now).total_seconds()
    # Aufrunden: am ersten Tag stehen 14 Tage an, in der letzten Stunde noch 1.
    # Abrunden hiesse, dass eine frische Installation "13 Tage" meldet.
    days_remaining = max(0, math.ceil(remaining_seconds / _SECONDS_PER_DAY))
    return {
        "days_total": TRIAL_DAYS,
        "days_remaining": days_remaining,
        "expired": remaining_seconds <= 0,
        "started_at": started_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def derive(
    *,
    activated: bool,
    first_run_at: str | None,
    is_frozen: bool,
    legacy_supporter: bool = False,
    now: datetime | None = None,
) -> LicenseState:
    """Leitet den Lizenzzustand aus den drei gespeicherten Fakten ab.

    ``first_run_at`` fehlt oder ist unlesbar? Dann gilt *jetzt* als Erststart —
    die Testphase beginnt neu, statt still zu Null zu kollabieren. Fail-open ist
    hier die einzige vertretbare Richtung: ein verlorener Zeitstempel darf
    niemanden aus seiner eigenen Bibliothek aussperren.
    """
    now = now or datetime.utcnow()
    legacy = bool(legacy_supporter) and not activated

    # Aktiviert oder aus dem Quellcode gestartet: keine Testphase, kein Gate.
    if activated or not is_frozen:
        return LicenseState(
            is_frozen=is_frozen,
            trial=None,
            blocked=False,
            legacy_supporter=legacy,
            legacy_note=LEGACY_SUPPORTER_NOTE if legacy else "",
        )

    trial = _trial_dict(_parse(first_run_at) or now, now)
    return LicenseState(
        is_frozen=True,
        trial=trial,
        blocked=bool(trial["expired"]),
        legacy_supporter=legacy,
        legacy_note=LEGACY_SUPPORTER_NOTE if legacy else "",
    )
