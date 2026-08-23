"""Was ein GitHub-Release fuer diese Installation bedeutet — reine Ableitung (#148).

Der Ein-Klick-Update-Weg (PRD #138) hat genau eine Entscheidung, und sie ist
frei von I/O: aus dem Release-Payload, der laufenden Version und der Frage
"laeuft dieser Prozess als eingefrorenes Build?" faellt, ob die App sich selbst
aktualisieren darf — und welchen Link der Nutzer sonst bekommt.

Drei Regeln, die den Rest erklaeren:

* **Der Installer heisst, wie er heisst.** Das Release-Asset ``LocalBib-Setup-
  <version>.exe`` ist ein Vertrag zwischen ``installer/localbib.iss`` (#146) und
  dieser Datei, keine Kosmetik. Wir suchen nach genau diesem Namen und fallen
  nicht auf "irgendeine .exe" zurueck: eine fremde .exe still auszufuehren waere
  schlimmer als kein Update.
* **Ohne Installer-Asset bleibt der Weg offen.** ``download_url`` zeigt dann auf
  die Release-Seite. Es gibt keinen Zustand, in dem der Nutzer einen toten Knopf
  oder einen leeren Link sieht.
* **Nur ein eingefrorenes Build darf sich selbst ueberschreiben.** Eine
  Quellinstallation bekommt nie einen Installer angeboten — dasselbe Prinzip wie
  das Aktivierungs-Gate aus #144 (ADR-0015): der Installer wuerde ein
  git-Arbeitsverzeichnis nicht aktualisieren, sondern danebenlegen.

Rein wie ``license_state`` & Co.: kein Netz, kein Dateisystem, kein Subprozess.
Der Router holt den Payload und fuehrt die Nebenwirkungen aus.
"""

from __future__ import annotations

from dataclasses import dataclass

# Der Asset-Name, den installer/localbib.iss erzeugt (OutputBaseFilename).
INSTALLER_ASSET_PREFIX = "LocalBib-Setup-"
INSTALLER_ASSET_SUFFIX = ".exe"


def installer_asset_name(version: str) -> str:
    """Der Dateiname, unter dem Release ``version`` seinen Installer anhaengt."""
    return f"{INSTALLER_ASSET_PREFIX}{version.lstrip('v')}{INSTALLER_ASSET_SUFFIX}"


# Der vierte Platz im Tupel ordnet Vorab- vor Fertig-Release: 0.3.0-rc1 < 0.3.0.
_PRERELEASE = 0
_FINAL = 1


def parse_version(v: str) -> tuple[int, int, int, int]:
    """"1.2.3" -> (1, 2, 3, 1). Unlesbares gilt als aelteste Version.

    Immer vier Stellen, damit der Vergleich in ``decide`` nie zwei verschieden
    lange Tupel gegeneinander stellt: "0.3" ist dasselbe wie "0.3.0" und nicht
    weniger. Drei Eingaben, die hier real ankommen und frueher alle auf
    ``(0, 0, 0)`` -- also "aelteste Version" -- fielen:

    * **Ein Vorab-Tag.** ``release.yml`` triggert auf ``v*.*.*`` und prueft den
      Tag nur per Praefix, also baut ``v0.3.0-rc1`` und schreibt genau das in
      VERSION. ``int("0-rc1")`` warf, und die Installation meldete danach
      dauerhaft ein Update auf sich selbst.
    * **Ein BOM vor der ersten Ziffer.** ``.strip()`` entfernt U+FEFF nicht, es
      ist kein Whitespace. ``routers/version.py`` liest VERSION zwar mit
      ``utf-8-sig``, aber ein Tag-Name aus dem GitHub-Payload laeuft an diesem
      Leser vorbei -- hier ist die letzte Stelle, die es abfangen kann.
    * **Build-Metadaten.** ``1.2.3+build7`` sagt nichts ueber die Reihenfolge.
    """
    try:
        core = str(v).replace("\ufeff", "").strip().lstrip("vV")
        core = core.partition("+")[0]
        core, _, prerelease = core.partition("-")
        numbers = [int(part) for part in core.split(".")[:3]]
    except Exception:
        return (0, 0, 0, _PRERELEASE)
    numbers += [0] * (3 - len(numbers))
    return (numbers[0], numbers[1], numbers[2], _PRERELEASE if prerelease else _FINAL)


@dataclass(frozen=True)
class UpdateOffer:
    """Alles, was UI und Update-Endpunkt ueber das neueste Release wissen muessen."""

    current: str
    latest: str | None
    update_available: bool
    release_url: str
    #: Die URL des Installer-Assets — "" wenn das Release keines mitliefert.
    installer_url: str
    #: Der Link, den der Nutzer immer anklicken kann: Installer, sonst Release-Seite.
    download_url: str
    is_frozen: bool
    #: Nur dann darf der Ein-Klick-Weg angeboten werden.
    can_auto_update: bool

    def as_dict(self) -> dict:
        return {
            "current": self.current,
            "latest": self.latest,
            "update_available": self.update_available,
            "release_url": self.release_url,
            "installer_url": self.installer_url,
            "download_url": self.download_url,
            "is_frozen": self.is_frozen,
            "can_auto_update": self.can_auto_update,
        }


def no_offer(current: str, is_frozen: bool) -> UpdateOffer:
    """Der Zustand "wir wissen nichts" — Netzfehler, HTTP-Fehler, kein Release."""
    return UpdateOffer(
        current=current,
        latest=None,
        update_available=False,
        release_url="",
        installer_url="",
        download_url="",
        is_frozen=is_frozen,
        can_auto_update=False,
    )


def decide(current: str, release: dict | None, is_frozen: bool) -> UpdateOffer:
    """Werte einen GitHub-``releases/latest``-Payload gegen die laufende Version aus."""
    if not isinstance(release, dict):
        return no_offer(current, is_frozen)

    latest = str(release.get("tag_name") or "").lstrip("v")
    release_url = str(release.get("html_url") or "")
    if not latest:
        return no_offer(current, is_frozen)

    wanted = installer_asset_name(latest).lower()
    installer_url = ""
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("name") or "").lower() == wanted:
            installer_url = str(asset.get("browser_download_url") or "")
            break

    update_available = parse_version(latest) > parse_version(current)
    return UpdateOffer(
        current=current,
        latest=latest,
        update_available=update_available,
        release_url=release_url,
        installer_url=installer_url,
        # Nie leer, solange es ein Release gibt: der manuelle Weg bleibt immer offen.
        download_url=installer_url or release_url,
        is_frozen=is_frozen,
        can_auto_update=bool(update_available and is_frozen and installer_url),
    )
