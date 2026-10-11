"""The link mode takes effect (#213).

``LINK_MODE`` used to be accepted by the settings and read by nothing:
``filenames.create_symlinks`` always made a hard link on Windows (a ``.lnk``
shortcut when that failed) and a symbolic link elsewhere. Now the mode decides —
and without an entry in the ``.env`` that old behaviour is exactly what you get.

Everything runs in temp folders; the shortcut and the symbolic link are faked
where a test must not depend on the platform (``.lnk`` needs Windows, a real
symlink needs a privilege there).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import filenames
import webapp
from literature_manager import Config


@pytest.fixture
def folders(tmp_path, monkeypatch):
    all_dir = tmp_path / "all"
    cat_dir = tmp_path / "kategorien"
    all_dir.mkdir()
    cat_dir.mkdir()
    monkeypatch.setattr(Config, "ALL_DIR", str(all_dir))
    monkeypatch.setattr(Config, "CATEGORIES_DIR", str(cat_dir))
    pdf = all_dir / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 link mode test")
    return pdf, cat_dir


@pytest.fixture
def fake_links(monkeypatch):
    """A shortcut writes ``<name>.lnk``, a symbolic link a marker file — so the
    test sees which one was chosen on any platform."""
    made = []

    def shortcut(target, link_path):
        Path(link_path + ".lnk").write_text(target)
        made.append("shortcut")
        return "shortcut"

    def symlink(src, dst):
        Path(dst).write_text("symlink -> " + str(src))
        made.append("symlink")

    monkeypatch.setattr(filenames, "_create_windows_shortcut", shortcut)
    monkeypatch.setattr(filenames.os, "symlink", symlink)
    return made


class _Db:
    """The two reads create_symlinks makes."""

    def __init__(self, categories, assigned):
        self._categories = categories
        self._assigned = assigned

    def get_categories(self):
        return self._categories

    def get_paper_categories(self, paper_id):
        return [{"id": cid} for cid in self._assigned]


DB = _Db([{"id": 1, "name": "Materials", "parent_id": None},
          {"id": 2, "name": "Concrete", "parent_id": 1}], [2])


def _entry(cat_dir) -> Path:
    return cat_dir / "Materials" / "Concrete" / "a.pdf"


# ---------------------------------------------------------------------------
# The modes
# ---------------------------------------------------------------------------

def test_copy_places_a_copy(folders):
    pdf, cat_dir = folders
    assert filenames.create_symlinks(DB, 1, "a.pdf", mode="copy") == {"placed": 1, "fallbacks": 0, "failed": 0}
    entry = _entry(cat_dir)
    assert entry.read_bytes() == pdf.read_bytes()
    assert not entry.is_symlink() and os.stat(pdf).st_nlink == 1


def test_hardlink_places_a_hard_link(folders):
    pdf, cat_dir = folders
    assert filenames.create_symlinks(DB, 1, "a.pdf", mode="hardlink") == {"placed": 1, "fallbacks": 0, "failed": 0}
    assert os.stat(pdf).st_nlink == 2
    assert not _entry(cat_dir).is_symlink()


def test_shortcut_is_a_lnk_on_windows_and_a_symlink_elsewhere(folders, fake_links):
    _pdf, cat_dir = folders
    assert filenames.create_symlinks(DB, 1, "a.pdf", mode="symlink")["placed"] == 1
    if sys.platform == "win32":
        assert fake_links == ["shortcut"]
        assert (cat_dir / "Materials" / "Concrete" / "a.pdf.lnk").is_file()
    else:
        assert fake_links == ["symlink"]


def test_a_failed_hard_link_falls_back_to_a_shortcut_and_is_counted(folders, fake_links, monkeypatch):
    def cross_device(src, dst):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(filenames.os, "link", cross_device)
    assert filenames.create_symlinks(DB, 1, "a.pdf", mode="hardlink") == {"placed": 1, "fallbacks": 1, "failed": 0}
    assert fake_links == (["shortcut"] if sys.platform == "win32" else ["symlink"])


def test_nothing_placeable_is_counted_not_raised(folders, monkeypatch):
    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(filenames.shutil, "copy2", broken)
    assert filenames.create_symlinks(DB, 1, "a.pdf", mode="copy") == {"placed": 0, "fallbacks": 0, "failed": 1}


def test_a_new_mode_replaces_the_old_entry_without_duplicates(folders, fake_links):
    _pdf, cat_dir = folders
    filenames.create_symlinks(DB, 1, "a.pdf", mode="symlink")   # .lnk on Windows, marker elsewhere
    filenames.create_symlinks(DB, 1, "a.pdf", mode="copy")
    assert sorted(p.name for p in (cat_dir / "Materials" / "Concrete").iterdir()) == ["a.pdf"]
    assert _entry(cat_dir).read_bytes().startswith(b"%PDF")


def test_the_configured_mode_is_used_without_an_argument(folders, monkeypatch):
    pdf, _cat_dir = folders
    monkeypatch.setattr(Config, "LINK_MODE", "copy")
    filenames.create_symlinks(DB, 1, "a.pdf")
    assert os.stat(pdf).st_nlink == 1


# ---------------------------------------------------------------------------
# Unset = today's behaviour; the default lives in Config only
# ---------------------------------------------------------------------------

@pytest.fixture
def link_env(monkeypatch):
    # setenv records the original, so the value a test's PUT loads into the
    # process environment is removed again afterwards.
    monkeypatch.setenv("LINK_MODE", "")
    yield monkeypatch
    monkeypatch.undo()
    Config.reload_from_env()


def test_unset_means_hard_link_on_windows_and_symlink_elsewhere(link_env):
    Config.reload_from_env()
    assert Config.LINK_MODE == ("hardlink" if sys.platform == "win32" else "symlink")


@pytest.mark.parametrize("raw, mode", [("copy", "copy"), (" Hardlink ", "hardlink"), ("symlink", "symlink")])
def test_a_stored_mode_is_used(link_env, raw, mode):
    link_env.setenv("LINK_MODE", raw)
    Config.reload_from_env()
    assert Config.LINK_MODE == mode


def test_an_unknown_value_falls_back_to_the_default(link_env):
    link_env.setenv("LINK_MODE", "junction")
    Config.reload_from_env()
    assert Config.LINK_MODE == ("hardlink" if sys.platform == "win32" else "symlink")


def test_unset_places_what_was_placed_before_the_setting(link_env, folders):
    """The intent of the former characterization: on Windows a hard link (not
    a symlink), elsewhere a symbolic link."""
    Config.reload_from_env()
    pdf, cat_dir = folders
    filenames.create_symlinks(DB, 1, "a.pdf")
    if sys.platform == "win32":
        assert os.stat(pdf).st_nlink == 2 and not _entry(cat_dir).is_symlink()
    else:
        assert _entry(cat_dir).is_symlink()


@pytest.fixture
def temp_env(tmp_path, link_env):
    import routers.settings as settings_mod

    link_env.setattr(settings_mod, "_ENV_PATH", tmp_path / ".env")
    link_env.setattr(Config, "ENV_PATH", str(tmp_path / ".env"))
    link_env.setenv("LLM_CONFIG_PATH", str(tmp_path / "llm.json"))
    Config.reload_from_env()
    return tmp_path


def test_settings_show_the_effective_mode_and_store_a_chosen_one(temp_env):
    with TestClient(webapp.app) as c:
        assert c.get("/api/settings").json()["LINK_MODE"] == Config.LINK_MODE
        assert c.put("/api/settings", json={"LINK_MODE": "copy"}).status_code == 200
        assert c.get("/api/settings").json()["LINK_MODE"] == "copy"
        assert Config.LINK_MODE == "copy"
    assert "LINK_MODE=copy" in (temp_env / ".env").read_text(encoding="utf-8")


def test_an_unknown_mode_is_refused_and_not_written(temp_env):
    with TestClient(webapp.app) as c:
        resp = c.put("/api/settings", json={"LINK_MODE": "junction"})
    assert resp.status_code == 422
    assert resp.json()["detail"] == {"code": "error.settings.invalidChoice", "params": {"key": "LINK_MODE"}}
    env = temp_env / ".env"
    assert not env.exists() or "LINK_MODE" not in env.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Clearing the category folders
# ---------------------------------------------------------------------------

def test_clearing_removes_every_entry_and_stale_folder_but_keeps_the_pdfs(folders):
    pdf, cat_dir = folders
    filenames.create_symlinks(DB, 1, "a.pdf", mode="hardlink")
    (cat_dir / "Gone").mkdir()
    (cat_dir / "Gone" / "a.pdf.lnk").write_text("old")
    assert filenames.clear_category_folders() == 2
    assert list(cat_dir.iterdir()) == []
    assert pdf.read_bytes().startswith(b"%PDF") and os.stat(pdf).st_nlink == 1


def test_clearing_never_enters_the_pdf_folder(tmp_path, monkeypatch):
    """Even a base folder set up the wrong way round keeps its PDFs."""
    cat_dir = tmp_path / "kategorien"
    all_dir = cat_dir / "all"
    all_dir.mkdir(parents=True)
    (all_dir / "a.pdf").write_bytes(b"%PDF")
    (cat_dir / "x.pdf").write_bytes(b"%PDF entry")
    monkeypatch.setattr(Config, "ALL_DIR", str(all_dir))
    monkeypatch.setattr(Config, "CATEGORIES_DIR", str(cat_dir))
    filenames.clear_category_folders()
    assert (all_dir / "a.pdf").is_file()
    assert not (cat_dir / "x.pdf").exists()
