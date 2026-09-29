"""Direct tests of the pure Marketplace decision (services/marketplace.py, #189).

No app, no network, no filesystem — exactly the combinatorics the acceptance
criteria name: core version below ``min_core``, a wrong Python tag, a higher
version present, and an installed version no longer listed in the index.
"""
from __future__ import annotations

from services import marketplace
from services.addon_catalog import CoreFacts


def _facts(**over) -> CoreFacts:
    base = dict(api_version=2, python_tag="cp313-win_amd64", core_version="0.9.0", frozen=True)
    base.update(over)
    return CoreFacts(**base)


def _version(**over) -> dict:
    base = dict(
        version="1.0.0", api_version=2, min_core="0.5.0", released="2026-01-01",
        changelog="", requires_source=False,
        artifacts=[{"python": "any", "url": "https://x.invalid/a.zip", "size": 100, "sha256": "abc"}],
    )
    base.update(over)
    return base


def _entry(*versions, **over) -> dict:
    base = dict(id="demo", name="Demo", trust="official", versions=list(versions))
    base.update(over)
    return base


# --- version_incompatible ---------------------------------------------------

def test_core_version_below_min_core_is_incompatible():
    version = _version(min_core="1.5.0")
    assert marketplace.version_incompatible(version, _facts(core_version="0.9.0")) == "error.plugins.coreTooOld"


def test_min_core_is_not_checked_for_a_source_checkout():
    """No core_version (source checkout) — the same rule addon_catalog uses."""
    version = _version(min_core="99.0.0")
    assert marketplace.version_incompatible(version, _facts(core_version=None)) is None


def test_wrong_python_tag_is_incompatible():
    version = _version(artifacts=[{"python": "cp311-win_amd64", "url": "x", "size": 1, "sha256": "a"}])
    assert marketplace.version_incompatible(version, _facts(python_tag="cp313-win_amd64")) \
        == "error.plugins.pythonMismatch"


def test_any_artifact_matches_every_python_tag():
    version = _version(artifacts=[{"python": "any", "url": "x", "size": 1, "sha256": "a"}])
    assert marketplace.version_incompatible(version, _facts(python_tag="cp311-win_amd64")) is None


def test_requires_source_skips_the_artifact_check():
    version = _version(requires_source=True, artifacts=[])
    assert marketplace.version_incompatible(version, _facts()) is None


def test_contract_mismatch_is_checked_first():
    version = _version(api_version=99)
    assert marketplace.version_incompatible(version, _facts(api_version=2)) == "error.plugins.contractMismatch"


# --- incompatible_detail (#199: a machine-readable reason on the card) ------

def test_incompatible_detail_names_the_needed_and_the_running_python():
    version = _version(artifacts=[{"python": "cp313-win_amd64", "url": "x", "size": 1, "sha256": "a"}])
    detail = marketplace.incompatible_detail(version, _facts(python_tag="cp314-win_amd64"))
    assert detail == {"code": "python", "needed": ["cp313-win_amd64"], "have": "cp314-win_amd64"}


def test_incompatible_detail_names_the_required_core_version():
    version = _version(min_core="1.5.0")
    detail = marketplace.incompatible_detail(version, _facts(core_version="0.9.0"))
    assert detail == {"code": "min_core", "needed": "1.5.0", "have": "0.9.0"}


def test_incompatible_detail_names_the_contract_versions():
    version = _version(api_version=99)
    detail = marketplace.incompatible_detail(version, _facts(api_version=2))
    assert detail == {"code": "api_version", "needed": 99, "have": 2}


def test_incompatible_detail_is_none_when_compatible():
    assert marketplace.incompatible_detail(_version(), _facts()) is None


def test_requires_source_never_yields_an_incompatible_detail():
    """A source-only version is not offered as a download, but it is not
    *incompatible* either -- ``requires_source`` already carries that fact,
    so there is deliberately no ``requires_source`` reason code (CLAUDE.md)."""
    version = _version(requires_source=True, artifacts=[])
    assert marketplace.incompatible_detail(version, _facts()) is None


# --- best_version ------------------------------------------------------------

def test_best_version_picks_the_higher_compatible_version():
    entry = _entry(_version(version="1.0.0"), _version(version="2.0.0"))
    assert marketplace.best_version(entry, _facts())["version"] == "2.0.0"


def test_best_version_skips_an_incompatible_higher_version():
    """A newer release that needs a core the user does not have yet must not
    be offered — the next-best compatible version is."""
    entry = _entry(
        _version(version="1.0.0", min_core="0.1.0"),
        _version(version="2.0.0", min_core="99.0.0"),
    )
    offered = marketplace.best_version(entry, _facts(core_version="0.9.0"))
    assert offered["version"] == "1.0.0"


def test_best_version_is_none_when_nothing_fits():
    entry = _entry(_version(version="1.0.0", min_core="99.0.0"))
    assert marketplace.best_version(entry, _facts(core_version="0.9.0")) is None


# --- installed_in_index -------------------------------------------------------

def test_installed_version_no_longer_in_index():
    entry = _entry(_version(version="2.0.0"))
    assert marketplace.installed_in_index(entry, "1.0.0") is False
    assert marketplace.installed_in_index(entry, "2.0.0") is True


# --- build_card / merge_view --------------------------------------------------

def test_card_flags_installed_version_not_in_index():
    entry = _entry(_version(version="2.0.0"))
    card = marketplace.build_card(entry, _facts(), {"version": "1.0.0", "source": "bundle", "state": "active"})
    assert card["installed_not_in_index"] is True
    assert card["update_available"] is True
    assert card["state"] == marketplace.STATE_UPDATE_AVAILABLE


def test_card_not_installed_and_compatible():
    entry = _entry(_version(version="1.0.0"))
    card = marketplace.build_card(entry, _facts(), None)
    assert card["state"] == marketplace.STATE_NOT_INSTALLED
    assert card["installed"] is False


def test_card_not_installed_and_incompatible():
    entry = _entry(_version(version="1.0.0", min_core="99.0.0"))
    card = marketplace.build_card(entry, _facts(core_version="0.9.0"), None)
    assert card["state"] == marketplace.STATE_INCOMPATIBLE
    assert card["incompatible_reason"] == {"code": "min_core", "needed": "99.0.0", "have": "0.9.0"}


def test_card_compatible_carries_no_incompatible_reason():
    entry = _entry(_version(version="1.0.0"))
    card = marketplace.build_card(entry, _facts(), None)
    assert card["incompatible_reason"] is None


def test_card_dev_source_wins_over_update_available():
    """A Dev-Suchpfad install is never nagged about an index update — the
    author is working on the source, not consuming a release."""
    entry = _entry(_version(version="2.0.0"))
    card = marketplace.build_card(entry, _facts(), {"version": "1.0.0", "source": "dev", "state": "active"})
    assert card["state"] == marketplace.STATE_DEV


def test_requires_source_addon_has_no_size_but_is_compatible():
    entry = _entry(_version(version="1.0.0", requires_source=True, artifacts=[]))
    card = marketplace.build_card(entry, _facts(), None)
    assert card["requires_source"] is True
    assert card["size"] is None
    assert card["state"] == marketplace.STATE_NOT_INSTALLED


def test_permission_rows_carry_enforced_flag():
    entry = _entry(_version(permissions=["library.read", "network"]))
    card = marketplace.build_card(entry, _facts(), None)
    by_key = {p["key"]: p["enforced"] for p in card["permissions"]}
    assert by_key["library.read"] is True   # durchgesetzt
    assert by_key["network"] is False       # erklaert


def test_merge_view_appends_dev_only_addon_not_in_index():
    entry = _entry(_version(version="1.0.0"))
    installed = {
        "demo": {"version": "1.0.0", "source": "bundle", "state": "active"},
        "workshop": {"version": "0.1.0", "source": "dev", "state": "active",
                     "name": "Workshop", "tagline": "", "permissions": ["files"]},
    }
    cards = marketplace.merge_view([entry], installed, _facts())
    ids = {c["id"]: c for c in cards}
    assert set(ids) == {"demo", "workshop"}
    assert ids["workshop"]["state"] == marketplace.STATE_DEV
    assert ids["workshop"]["in_index"] is False


# --- announced entries (#198) -------------------------------------------------

def test_an_announced_entry_without_a_version_shows_no_card():
    """The index may carry an Add-on's texts before its first release (the
    release button needs the entry to add a version to); there is nothing to
    offer yet, so no card - an installed copy still shows as a local card."""
    announced = _entry(id="soon", name="Soon")
    assert marketplace.merge_view([announced], {}, _facts()) == []

    cards = marketplace.merge_view([announced], {"soon": {"version": "0.1.0", "source": "dev", "name": "Soon"}},
                                   _facts())
    assert [(c["id"], c["in_index"], c["state"]) for c in cards] == [("soon", False, marketplace.STATE_DEV)]


def test_a_native_artifact_is_offered_with_its_size_only_to_its_interpreter():
    """A Bundle with a vendored numeric stack names the exe's build tag."""
    native = _entry(_version(artifacts=[{"python": "cp313-win_amd64", "url": "https://x.invalid/h.zip",
                                         "size": 120_000_000, "sha256": "f" * 64}]))
    exe = marketplace.merge_view([native], {}, _facts(python_tag="cp313-win_amd64"))[0]
    assert (exe["state"], exe["size"], exe["offered_version"]) == (marketplace.STATE_NOT_INSTALLED, 120_000_000, "1.0.0")
    assert exe["incompatible_reason"] is None
    source = marketplace.merge_view([native], {}, _facts(python_tag="cp314-win_amd64"))[0]
    assert source["state"] == marketplace.STATE_INCOMPATIBLE
    assert source["incompatible_reason"] == {"code": "python", "needed": ["cp313-win_amd64"], "have": "cp314-win_amd64"}
