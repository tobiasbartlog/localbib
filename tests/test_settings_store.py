"""``settings_store.write_env`` never leaves the .env emptied or half written
(review of #235, round 1 finding 1b and round 2 findings 1-3)."""
from __future__ import annotations

import os
import threading

import pytest

import settings_store


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(settings_store, "_REPLACE_DELAY", 0)


def test_a_value_that_cannot_be_encoded_leaves_the_file_untouched(tmp_path):
    env = tmp_path / ".env"
    env.write_text("CROSSREF_MAILTO=me@example.org\nOPENALEX_API_KEY=k\n", encoding="utf-8")
    before = env.read_bytes()
    with pytest.raises(UnicodeEncodeError):
        settings_store.write_env(env, {"X": "bad\ud83d"})
    assert env.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


def test_a_replace_that_stays_refused_raises_and_keeps_the_old_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    calls = []

    def locked(src, dst):
        calls.append(src)
        raise PermissionError("held by another process")

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(settings_store.EnvLocked):
        settings_store.write_env(env, {"B": "2"})
    assert len(calls) == 3
    assert env.read_text(encoding="utf-8") == "A=1\n"
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


def test_a_replace_refused_once_is_retried(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    real = os.replace
    state = {"n": 0}

    def flaky(src, dst):
        state["n"] += 1
        if state["n"] == 1:
            raise PermissionError("busy")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    settings_store.write_env(env, {"B": "2"})
    assert env.read_text(encoding="utf-8").splitlines() == ["A=1", "B=2"]
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


def test_the_temp_file_has_a_unique_name_and_is_fsynced(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    names, synced = [], []
    real_replace, real_fsync = os.replace, os.fsync

    def spy_replace(src, dst):
        names.append(os.path.basename(src))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd))[1])
    settings_store.write_env(env, {"A": "1"})
    settings_store.write_env(env, {"B": "2"})
    assert len(set(names)) == 2 and all(n != ".env.tmp" for n in names)
    assert len(synced) == 2


def test_two_threads_writing_different_keys_both_survive(tmp_path):
    env = tmp_path / ".env"
    env.write_text("BASE=1\n", encoding="utf-8")
    errors = []

    def work(prefix):
        try:
            for i in range(15):
                settings_store.write_env(env, {f"{prefix}{i}": "v"})
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(p,)) for p in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    keys = {line.split("=")[0] for line in env.read_text(encoding="utf-8").splitlines()}
    assert keys == {"BASE"} | {f"{p}{i}" for p in ("A", "B") for i in range(15)}
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


def test_a_write_creates_the_file_and_its_folder(tmp_path):
    env = tmp_path / "sub" / ".env"
    settings_store.write_env(env, {"A": "1"})
    assert env.read_text(encoding="utf-8") == "A=1\n"
