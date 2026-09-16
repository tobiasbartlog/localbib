"""ca_trust — certifi und ein Haus-Bundle ergeben *einen* Trust-Store.

Der Regressionskern (Bug "keine Modelle waehlbar"): ein Zusatz-Bundle darf die
oeffentlichen Wurzeln nie ersetzen. Frueher wurde REQUESTS_CA_BUNDLE roh als
``verify=`` durchgereicht; enthielt die Datei nur Zwischenzertifikate, hing der
Handshake an ``X509_V_FLAG_PARTIAL_CHAIN`` — unter Python 3.13+ gesetzt, davor
nicht. Aus der Quelle lief es, das gebaute .exe (3.11) brach ab.
"""

import os
import ssl

import certifi
import pytest

import ca_trust


# Ein Zwischenzertifikat ohne seine Wurzel — genau die Konstellation, die den
# Fehler ausgeloest hat. Inhaltlich egal, es muss nur als PEM erkennbar sein.
FAKE_INTERMEDIATE = (
    b"-----BEGIN CERTIFICATE-----\n"
    b"MIIBkTCB+wIJAKZ0000000000MA0GCSqGSIb3DQEBCwUAMBQxEjAQBgNVBAMMCXRl\n"
    b"c3QtaW50ZXJtZWRpYXRlMB4XDTI0MDEwMTAwMDAwMFoXDTM0MDEwMTAwMDAwMFow\n"
    b"-----END CERTIFICATE-----\n"
)


@pytest.fixture(autouse=True)
def _clean_module_state(monkeypatch):
    """Modulzustand je Test zuruecksetzen (install() merkt sich den Pfad)."""
    monkeypatch.setattr(ca_trust, "_configured_extra", None)
    monkeypatch.setattr(ca_trust, "_merged_path", None)
    monkeypatch.delenv(ca_trust.EXTRA_CA_ENV, raising=False)
    yield


@pytest.fixture
def extra_ca(tmp_path):
    p = tmp_path / "haus-bundle.pem"
    p.write_bytes(FAKE_INTERMEDIATE)
    return p


# ---------------------------------------------------------------------------
# Der eigentliche Regressionsschutz
# ---------------------------------------------------------------------------

def test_merged_bundle_keeps_every_public_root(monkeypatch, extra_ca):
    """Das Ergebnis ist ein *Superset*: certifi bleibt vollstaendig enthalten."""
    monkeypatch.setenv(ca_trust.EXTRA_CA_ENV, str(extra_ca))

    merged = ca_trust.ca_bundle()

    assert merged is not None
    content = open(merged, "rb").read()
    assert open(certifi.where(), "rb").read() in content
    assert FAKE_INTERMEDIATE in content


def test_merged_bundle_is_loadable_by_openssl(monkeypatch, tmp_path):
    """Ein reales Zertifikat plus certifi muss OpenSSL auch laden koennen."""
    real = tmp_path / "real.pem"
    # Ein echtes Zertifikat aus certifi als "Haus-Bundle" wiederverwenden.
    first = open(certifi.where(), "rb").read().split(b"-----END CERTIFICATE-----")[0]
    real.write_bytes(first + b"-----END CERTIFICATE-----\n")
    monkeypatch.setenv(ca_trust.EXTRA_CA_ENV, str(real))

    merged = ca_trust.ca_bundle()

    ctx = ssl.create_default_context(cafile=merged)
    # certifi hat >100 Wurzeln; wird nur das Haus-Bundle geladen, waeren es ~1.
    assert ctx.cert_store_stats()["x509_ca"] > 50


def test_partial_chain_no_longer_decides(monkeypatch, tmp_path):
    """Kern der Regression: mit dem Merge haengt nichts mehr am Flag.

    Gegen das blosse Haus-Bundle kennt der Store nur dessen Zertifikate; gegen
    den Merge stehen zusaetzlich alle certifi-Wurzeln bereit — unabhaengig
    davon, ob OpenSSL ein Zwischenzertifikat als Anker akzeptieren darf.
    """
    real = tmp_path / "real.pem"
    first = open(certifi.where(), "rb").read().split(b"-----END CERTIFICATE-----")[0]
    real.write_bytes(first + b"-----END CERTIFICATE-----\n")
    monkeypatch.setenv(ca_trust.EXTRA_CA_ENV, str(real))

    only_extra = ssl.create_default_context(cafile=str(real))
    merged = ssl.create_default_context(cafile=ca_trust.ca_bundle())

    assert only_extra.cert_store_stats()["x509_ca"] < 5
    assert merged.cert_store_stats()["x509_ca"] > 50


# ---------------------------------------------------------------------------
# Portabilitaet und Fehlertoleranz
# ---------------------------------------------------------------------------

def test_no_extra_configured_means_certifi_default(monkeypatch):
    assert ca_trust.ca_bundle() is None
    assert ca_trust.install() is None
    assert ca_trust.EXTRA_CA_ENV not in os.environ


def test_missing_file_falls_back_to_certifi(monkeypatch, tmp_path):
    """Dieselbe .env auf einem Rechner ohne das Bundle: certifi, kein Absturz."""
    monkeypatch.setenv(ca_trust.EXTRA_CA_ENV, str(tmp_path / "gibt-es-nicht.pem"))

    assert ca_trust.install() is None
    # Entfernt, nicht bloss ignoriert: ein toter Pfad in der Variablen wuerde
    # bei verify=None sogar den expliziten Parameter schlagen.
    assert ca_trust.EXTRA_CA_ENV not in os.environ


@pytest.mark.parametrize("spelling", ["%MY_CA_DIR%/haus.pem", "$MY_CA_DIR/haus.pem"])
def test_tilde_and_env_vars_are_expanded(monkeypatch, tmp_path, spelling):
    """Both spellings, on every platform.

    ``os.path.expandvars`` resolves ``%VAR%`` only on Windows; on the Linux CI
    runner it stayed literal, the file was "not there", and the release gate
    went red on a test that was green on the maintainer's machine. A shared
    ``.env`` must mean the same thing wherever it is read.
    """
    monkeypatch.setenv("MY_CA_DIR", str(tmp_path))
    p = tmp_path / "haus.pem"
    p.write_bytes(FAKE_INTERMEDIATE)
    monkeypatch.setenv(ca_trust.EXTRA_CA_ENV, spelling)

    resolved = ca_trust.configured_extra_ca()
    assert resolved is not None
    assert os.path.samefile(resolved, p)


def test_quoted_path_from_env_file(monkeypatch, extra_ca):
    monkeypatch.setenv(ca_trust.EXTRA_CA_ENV, f'"{extra_ca}"')
    assert ca_trust.configured_extra_ca() == str(extra_ca)


def test_file_without_certificate_is_ignored(monkeypatch, tmp_path):
    junk = tmp_path / "kaputt.pem"
    junk.write_text("das ist kein Zertifikat")
    monkeypatch.setenv(ca_trust.EXTRA_CA_ENV, str(junk))

    assert ca_trust.ca_bundle() is None


# ---------------------------------------------------------------------------
# install() als Prozess-Schalter
# ---------------------------------------------------------------------------

def test_install_points_env_var_at_the_merged_file(monkeypatch, extra_ca):
    """Auch requests-Aufrufe ohne verify= (CrossRef, DOI-Discovery) profitieren."""
    monkeypatch.setenv(ca_trust.EXTRA_CA_ENV, str(extra_ca))

    merged = ca_trust.install()

    assert merged is not None
    assert os.environ[ca_trust.EXTRA_CA_ENV] == merged
    assert merged != str(extra_ca)


def test_install_is_idempotent(monkeypatch, extra_ca):
    """Zweiter Aufruf (PUT /api/settings) darf das Ergebnis nicht erneut mergen."""
    monkeypatch.setenv(ca_trust.EXTRA_CA_ENV, str(extra_ca))

    first = ca_trust.install()
    second = ca_trust.install()

    assert first == second
    assert open(first, "rb").read().count(FAKE_INTERMEDIATE) == 1


def test_merged_file_is_rebuilt_when_deleted(monkeypatch, extra_ca):
    """Temp-Aufraeumer zur Laufzeit darf die Verbindung nicht kippen."""
    monkeypatch.setenv(ca_trust.EXTRA_CA_ENV, str(extra_ca))
    merged = ca_trust.install()
    os.remove(merged)

    assert ca_trust.ca_bundle() == merged
    assert os.path.isfile(merged)


def test_changed_extra_bundle_yields_a_new_file(monkeypatch, tmp_path):
    """Ein geaendertes Haus-Bundle darf nie ein altes Ergebnis weiterbenutzen."""
    p = tmp_path / "haus.pem"
    p.write_bytes(FAKE_INTERMEDIATE)
    monkeypatch.setenv(ca_trust.EXTRA_CA_ENV, str(p))
    before = ca_trust.install()

    p.write_bytes(FAKE_INTERMEDIATE.replace(b"MIIBkTCB", b"MIIBkTCC"))
    monkeypatch.setenv(ca_trust.EXTRA_CA_ENV, str(p))
    after = ca_trust.install()

    assert before != after
