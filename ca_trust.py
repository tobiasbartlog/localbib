"""Ein Trust-Store fuer alle ausgehenden HTTPS-Aufrufe.

Hintergrund (Bug: "keine Modelle waehlbar", "semantische Suche tot"):
``REQUESTS_CA_BUNDLE`` wurde bisher unveraendert als ``verify=`` durchgereicht.
Das **ersetzt** den Trust-Store, statt ihn zu ergaenzen — ab diesem Moment ist
ausschliesslich vertrauenswuerdig, was in dieser einen Datei steht. Enthaelt
sie (wie in der Praxis ueblich) nur die Zwischenzertifikate des eigenen
Hauses und nicht deren Wurzel, haengt der Handshake daran, ob OpenSSL ein
Zwischenzertifikat als Anker akzeptieren darf. Genau das steuert
``X509_V_FLAG_PARTIAL_CHAIN``, und Python setzt es erst **ab 3.13** per
Default. Derselbe Rechner, dieselbe Datei: aus der Quelle (3.14) lief es, das
gebaute .exe (3.11) brach mit ``unable to get issuer certificate`` ab.

Die Lehre ist nicht "Flag setzen", sondern: ein Zusatz-Bundle ist ein
*Zusatz*. Dieses Modul fuehrt certifi und das konfigurierte Bundle zu einem
Superset zusammen und gibt dessen Pfad zurueck. Damit bleiben oeffentliche
Wurzeln gueltig, das Haus-Zertifikat kommt obendrauf, und keine Konstellation
aus Python-Version, Kettenlaenge oder Bundle-Inhalt kann mehr alles kippen.

Verwendung: :func:`install` einmal beim Start (und nach jedem ``.env``-Reload),
:func:`ca_bundle` an jeder Call-Site als ``verify=``. Das Modul ist
fehlertolerant — kann es nicht zusammenfuehren, faellt es auf certifi zurueck
und bricht nie den Start ab.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from typing import Optional

import certifi

# Die Variable, ueber die der Nutzer sein Haus-Bundle konfiguriert. Sie ist
# zugleich die Variable, die ``requests`` von sich aus liest — deshalb ist sie
# am Ende von install() das *Ergebnis* (der zusammengefuehrte Pfad), nicht mehr
# die Eingabe. Die Eingabe merkt sich _configured_extra.
EXTRA_CA_ENV = "REQUESTS_CA_BUNDLE"

_PEM_BEGIN = b"-----BEGIN CERTIFICATE-----"

# Der urspruenglich konfigurierte Pfad, damit ein zweiter install()-Aufruf
# nicht das eigene Merge-Ergebnis als "Zusatz-Bundle" wieder einliest.
_configured_extra: Optional[str] = None
_merged_path: Optional[str] = None


def _expand(raw: str) -> str:
    """``~`` und ``%VARS%`` aufloesen; Anfuehrungszeichen aus der .env abstreifen."""
    return os.path.expanduser(os.path.expandvars(raw.strip().strip('"').strip("'")))


def configured_extra_ca() -> Optional[str]:
    """Das konfigurierte Zusatz-Bundle, expandiert — oder ``None``.

    ``None`` heisst: nicht gesetzt, leer, oder die Datei existiert auf diesem
    Rechner nicht. Letzteres ist Absicht: dieselbe ``.env`` soll ueber mehrere
    Rechner portabel bleiben, das Bundle greift nur dort, wo es liegt.
    """
    raw = os.environ.get(EXTRA_CA_ENV)
    if raw:
        path = _expand(raw)
        # Nicht das eigene Merge-Ergebnis als Eingabe zurueckliefern.
        if _merged_path and os.path.normcase(path) == os.path.normcase(_merged_path):
            path = _configured_extra or ""
    else:
        path = _configured_extra or ""
    if not path:
        return None
    return path if os.path.isfile(path) else None


def _read_certs(path: str) -> bytes:
    """PEM-Inhalt einer Datei — leer, wenn dort kein Zertifikat steht."""
    data = open(path, "rb").read()
    return data if _PEM_BEGIN in data else b""


def _merge(extra: str) -> Optional[str]:
    """certifi + ``extra`` in eine Datei; gibt deren Pfad zurueck.

    Der Dateiname traegt einen Hash ueber beide Quellen, damit ein geaendertes
    Bundle (oder ein certifi-Update) zu einer neuen Datei fuehrt und alte
    Ergebnisse nie stillschweigend weiterbenutzt werden.
    """
    extra_pem = _read_certs(extra)
    if not extra_pem:
        logging.warning(
            "CA-Bundle %s enthaelt kein Zertifikat — wird ignoriert, es gilt certifi.",
            extra,
        )
        return None
    base_pem = _read_certs(certifi.where())
    digest = hashlib.sha256(base_pem + extra_pem).hexdigest()[:16]
    merged = os.path.join(tempfile.gettempdir(), f"localbib-ca-{digest}.pem")
    if not os.path.isfile(merged):
        # Erst daneben schreiben, dann umbenennen: ein abgebrochener Lauf darf
        # keine halbe Datei hinterlassen, der andere Prozesse dann vertrauen.
        tmp = f"{merged}.{os.getpid()}.part"
        with open(tmp, "wb") as fh:
            fh.write(base_pem)
            if not base_pem.endswith(b"\n"):
                fh.write(b"\n")
            fh.write(extra_pem)
        os.replace(tmp, merged)
    return merged


def ca_bundle() -> Optional[str]:
    """Der Trust-Store fuer ``verify=`` — certifi plus Haus-Bundle.

    ``None`` bedeutet "nimm den Default" (certifi) und ist der sichere
    Normalfall ohne konfiguriertes Zusatz-Bundle. Die Datei wird bei Bedarf
    neu erzeugt, falls sie zwischenzeitlich verschwunden ist (Temp-Aufraeumer).
    """
    extra = configured_extra_ca()
    if not extra:
        return None
    try:
        return _merge(extra)
    except OSError as exc:
        logging.warning("CA-Bundle %s nicht lesbar (%s) — es gilt certifi.", extra, exc)
        return None


def install() -> Optional[str]:
    """Den zusammengefuehrten Trust-Store prozessweit aktiv schalten.

    Setzt ``REQUESTS_CA_BUNDLE`` auf das Merge-Ergebnis, damit auch die
    ``requests``-Aufrufe profitieren, die kein ``verify=`` uebergeben (CrossRef,
    DOI-Discovery, Referenz-Extraktion ...). Ist nichts konfiguriert oder liegt
    die Datei hier nicht, wird die Variable **entfernt** — sonst gaebe ein
    toter Pfad aus einer fremden ``.env`` einen certifi-Fallback, der bei
    ``verify=None`` sogar gegen den expliziten Parameter gewinnt.

    Idempotent; gibt den aktiven Pfad zurueck (oder ``None`` fuer certifi).
    """
    global _configured_extra, _merged_path

    extra = configured_extra_ca()
    _configured_extra = extra
    merged = ca_bundle() if extra else None
    _merged_path = merged

    if merged:
        os.environ[EXTRA_CA_ENV] = merged
        logging.info("CA-Trust: certifi + %s -> %s", extra, merged)
    else:
        os.environ.pop(EXTRA_CA_ENV, None)
    return merged
