"""API tests for ``POST /api/import/ris`` (#174).

The endpoint kept its contract when the hand-rolled parser moved into
``ris_import``; these tests are the guard for that — and for what the move
bought: wrapped values and every extra tag in ``raw_metadata``.
"""

from __future__ import annotations

import json

RIS = """TY  - JOUR
TI  - Deep Things in Shallow Waters
AU  - Smith, Jane
AU  - Doe, John
PY  - 2021///
JO  - Journal of Things
DO  - 10.5555/xyz
AB  - An abstract the exporting tool wrapped
across two lines.
KW  - ethics
N1  - Read twice.
ER  -

TY  - BOOK
TI  - Ein Buch
AU  - Mueller, Anna
PY  - 2018
PB  - Verlag
SN  - 978-3-16-148410-0
ER  -
"""


def _post(client, text: str):
    return client.post(
        "/api/import/ris",
        files={"file": ("export.ris", text.encode("utf-8"),
                        "application/x-research-info-systems")},
    )


class TestRisImportEndpoint:
    def test_imports_every_record(self, client, db):
        data = _post(client, RIS).json()
        assert data["total"] == 2
        assert data["imported"] == 2
        assert data["errors"] == 0
        assert [r["status"] for r in data["results"]] == ["ok", "ok"]

    def test_core_fields_land_in_their_columns(self, client, db):
        _post(client, RIS)
        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT * FROM papers WHERE title LIKE 'Deep Things%'").fetchone()
        finally:
            conn.close()
        assert row["authors"] == "Smith, Jane; Doe, John"
        assert row["year"] == 2021
        assert row["doi"] == "10.5555/xyz"
        assert row["journal"] == "Journal of Things"

    def test_a_wrapped_abstract_arrives_whole(self, client, db):
        _post(client, RIS)
        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT abstract FROM papers WHERE title LIKE 'Deep Things%'").fetchone()
        finally:
            conn.close()
        assert row["abstract"] == "An abstract the exporting tool wrapped across two lines."

    def test_extra_tags_survive_in_raw_metadata(self, client, db):
        _post(client, RIS)
        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT raw_metadata FROM papers WHERE title LIKE 'Deep Things%'"
            ).fetchone()
        finally:
            conn.close()
        raw = json.loads(row["raw_metadata"])
        assert raw["KW"] == "ethics"
        assert raw["N1"] == "Read twice."

    def test_a_second_run_skips_what_is_already_there(self, client, db):
        _post(client, RIS)
        data = _post(client, RIS).json()
        assert data["imported"] == 0
        assert data["skipped"] == 2

    def test_an_empty_file_is_no_error(self, client, db):
        data = _post(client, "").json()
        assert data == {"total": 0, "imported": 0, "skipped": 0,
                        "errors": 0, "results": []}
