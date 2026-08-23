from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import httpx

BASE_URL = "https://api.openalex.org"
BATCH_SIZE = 50
_WORK_SELECT = (
    "id,doi,title,authorships,publication_year,cited_by_count,referenced_works,"
    "best_oa_location,open_access"
)


@dataclass
class Work:
    id: str
    doi: str
    title: str
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    cited_by_count: int = 0
    referenced_works: list[str] = field(default_factory=list)
    abstract: str = ""
    # Open-access location (Unpaywall data via OpenAlex). pdf_url may point
    # at a landing page when no direct PDF is known — downloaders must verify
    # the response is a PDF.
    oa_pdf_url: str = ""
    oa_landing_url: str = ""


def _normalize_doi(doi: str) -> str:
    doi = (doi or "").strip()
    if doi.startswith("http"):
        doi = doi.split("doi.org/")[-1]
    return doi.lower().rstrip(".")


def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9\s]", "", s.lower()).strip()


def _parse_work(w: dict) -> Work:
    doi = _normalize_doi(w.get("doi") or "")
    year = w.get("publication_year")
    authors = [
        a.get("author", {}).get("display_name", "")
        for a in (w.get("authorships") or [])[:10]
        if a.get("author", {}).get("display_name")
    ]
    best_oa = w.get("best_oa_location") or {}
    open_access = w.get("open_access") or {}
    return Work(
        id=w.get("id", ""),
        doi=doi,
        title=w.get("title") or "",
        authors=authors,
        year=int(year) if year else None,
        cited_by_count=w.get("cited_by_count") or 0,
        referenced_works=w.get("referenced_works") or [],
        oa_pdf_url=best_oa.get("pdf_url") or open_access.get("oa_url") or "",
        oa_landing_url=best_oa.get("landing_page_url") or "",
    )


def reconstruct_abstract(inverted_index: dict | None) -> str:
    """Reconstruct plain text from OpenAlex inverted-index abstract format."""
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, pos_list in inverted_index.items():
        for pos in pos_list:
            positions[pos] = word
    if not positions:
        return ""
    return " ".join(positions[i] for i in sorted(positions))


class OpenAlexClient:
    def __init__(self, mailto: str, api_key: str | None = None) -> None:
        self.mailto = mailto
        # Optional OpenAlex premium key: raises the rate-limit/budget (premium
        # pool). Defaults to Config.OPENALEX_API_KEY so every call site picks it
        # up automatically; pass explicitly to override (e.g. in tests). Empty
        # -> polite pool via mailto only.
        if api_key is None:
            try:
                from config import Config

                api_key = Config.OPENALEX_API_KEY
            except Exception:
                api_key = ""
        self.api_key = api_key or ""

    def _get(self, url: str, params: dict) -> dict:
        """Single GET with mailto injection and 429 backoff (up to 3 attempts).

        An empty ``mailto`` is omitted entirely (#140): the polite pool wants
        the *user's* address, and sending an empty or invented one is worse
        than querying the common pool anonymously.
        """
        params = {**params}
        if self.mailto:
            params["mailto"] = self.mailto
        if self.api_key:
            params["api_key"] = self.api_key
        for attempt in range(3):
            r = httpx.get(url, params=params, timeout=30)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", 5))
                logging.warning("OpenAlex 429, waiting %ss (attempt %s)", retry_after, attempt + 1)
                time.sleep(retry_after)
                continue
            if r.status_code == 200:
                return r.json()
            return {}
        return {}

    def fetch_works_by_doi(self, dois: list[str]) -> list[Work]:
        """Batch-fetch full Work objects by DOI list. Handles chunking at BATCH_SIZE."""
        results: list[Work] = []
        for i in range(0, len(dois), BATCH_SIZE):
            batch = dois[i : i + BATCH_SIZE]
            doi_filter = "|".join(f"https://doi.org/{_normalize_doi(d)}" for d in batch)
            try:
                data = self._get(
                    f"{BASE_URL}/works",
                    {"filter": f"doi:{doi_filter}", "per_page": 200, "select": _WORK_SELECT},
                )
                results.extend(_parse_work(w) for w in data.get("results", []))
            except Exception as e:
                logging.warning("OpenAlex DOI batch failed: %s", e)
            if i + BATCH_SIZE < len(dois):
                time.sleep(0.15)
        return results

    def fetch_works_by_id(self, oa_ids: list[str]) -> dict[str, Work]:
        """Batch-fetch Works by OpenAlex ID. Returns {oa_id: Work} dict."""
        fetched: dict[str, Work] = {}
        ids = list(oa_ids)
        for i in range(0, len(ids), BATCH_SIZE):
            batch = ids[i : i + BATCH_SIZE]
            short_ids = [bid.split("/")[-1] for bid in batch]
            try:
                data = self._get(
                    f"{BASE_URL}/works",
                    {
                        "filter": f"openalex_id:{'|'.join(short_ids)}",
                        "per_page": 200,
                        "select": _WORK_SELECT,
                    },
                )
                for w in data.get("results", []):
                    work = _parse_work(w)
                    if work.id:
                        fetched[work.id] = work
            except Exception as e:
                logging.warning("OpenAlex ID batch failed: %s", e)
            if i + BATCH_SIZE < len(ids):
                time.sleep(0.15)
        return fetched

    def fetch_work_by_title(
        self, title: str, authors: str = "", min_similarity: float = 0.6
    ) -> Work | None:
        """Title search with optional author filter. Returns best match above min_similarity."""
        if not title or len(title) < 5:
            return None
        clean = re.sub(r'["\\]', " ", title[:200]).strip()
        filters = [f"title.search:{clean}"]
        if authors:
            parts = [a.strip() for a in authors.split(",") if a.strip()]
            if parts:
                surname = parts[0].split()[-1] if " " in parts[0] else parts[0]
                if len(surname) > 2:
                    filters.append(f"raw_author_name.search:{surname}")
        try:
            data = self._get(
                f"{BASE_URL}/works",
                {"filter": ",".join(filters), "per_page": 10, "select": _WORK_SELECT},
            )
            query_words = set(_norm_title(title).split())
            for w in data.get("results", []):
                result_words = set(_norm_title(w.get("title") or "").split())
                if not query_words or not result_words:
                    continue
                overlap = len(query_words & result_words)
                similarity = overlap / max(len(query_words), len(result_words))
                if similarity >= min_similarity:
                    return _parse_work(w)
        except Exception as e:
            logging.warning("OpenAlex title search failed for '%s': %s", title[:50], e)
        return None

    def fetch_abstract(self, doi: str = "", oa_id: str = "") -> str:
        """Fetch and reconstruct abstract by DOI or OpenAlex ID."""
        if doi:
            norm = _normalize_doi(doi)
            try:
                data = self._get(
                    f"{BASE_URL}/works/https://doi.org/{norm}",
                    {"select": "id,abstract_inverted_index"},
                )
                text = reconstruct_abstract(data.get("abstract_inverted_index"))
                if text:
                    return text
            except Exception as e:
                logging.warning("OpenAlex abstract by DOI failed: %s", e)
        if oa_id and oa_id.startswith("https://openalex.org/"):
            try:
                data = self._get(
                    f"{BASE_URL}/works/{oa_id.split('/')[-1]}",
                    {"select": "id,abstract_inverted_index"},
                )
                return reconstruct_abstract(data.get("abstract_inverted_index"))
            except Exception as e:
                logging.warning("OpenAlex abstract by ID failed: %s", e)
        return ""
