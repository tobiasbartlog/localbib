from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from openalex_client import BASE_URL, BATCH_SIZE, OpenAlexClient, Work

MAILTO = "test@example.com"


def _client() -> OpenAlexClient:
    return OpenAlexClient(MAILTO)


def _work_dict(
    doi: str = "10.1000/test",
    oa_id: str = "https://openalex.org/W1",
    title: str = "Test Paper About Deep Learning",
) -> dict:
    return {
        "id": oa_id,
        "doi": f"https://doi.org/{doi}",
        "title": title,
        "authorships": [{"author": {"display_name": "Doe, John"}}],
        "publication_year": 2020,
        "cited_by_count": 42,
        "referenced_works": ["https://openalex.org/W99"],
    }


class TestFetchWorksByDoi:
    @respx.mock
    def test_returns_parsed_works(self):
        respx.get(f"{BASE_URL}/works").mock(
            return_value=httpx.Response(200, json={"results": [_work_dict()]})
        )
        works = _client().fetch_works_by_doi(["10.1000/test"])
        assert len(works) == 1
        assert isinstance(works[0], Work)

    @respx.mock
    def test_mailto_appended(self):
        route = respx.get(f"{BASE_URL}/works").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        _client().fetch_works_by_doi(["10.1000/x"])
        url = str(route.calls[0].request.url)
        assert "mailto=" in url and "test" in url and "example.com" in url

    @respx.mock
    def test_chunking_at_batch_size(self):
        dois = [f"10.{i:04d}/x" for i in range(BATCH_SIZE + 1)]
        respx.get(f"{BASE_URL}/works").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        with patch("openalex_client.time.sleep"):
            _client().fetch_works_by_doi(dois)
        assert respx.calls.call_count == 2

    @respx.mock
    def test_429_triggers_retry(self):
        respx.get(f"{BASE_URL}/works").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(200, json={"results": [_work_dict()]}),
            ]
        )
        with patch("openalex_client.time.sleep"):
            works = _client().fetch_works_by_doi(["10.1000/test"])
        assert len(works) == 1


class TestResponseNormalization:
    @respx.mock
    def test_doi_stripped_and_lowercased(self):
        respx.get(f"{BASE_URL}/works").mock(
            return_value=httpx.Response(200, json={"results": [_work_dict(doi="10.1000/TEST")]})
        )
        works = _client().fetch_works_by_doi(["10.1000/TEST"])
        assert works[0].doi == "10.1000/test"

    @respx.mock
    def test_fields_parsed_correctly(self):
        respx.get(f"{BASE_URL}/works").mock(
            return_value=httpx.Response(200, json={"results": [_work_dict()]})
        )
        w = _client().fetch_works_by_doi(["10.1000/test"])[0]
        assert w.cited_by_count == 42
        assert w.year == 2020
        assert "Doe, John" in w.authors
        assert "https://openalex.org/W99" in w.referenced_works


class TestFetchWorkByTitle:
    @respx.mock
    def test_returns_match_above_threshold(self):
        respx.get(f"{BASE_URL}/works").mock(
            return_value=httpx.Response(
                200, json={"results": [_work_dict(title="Test Paper About Deep Learning")]}
            )
        )
        work = _client().fetch_work_by_title("Test Paper About Deep Learning")
        assert work is not None
        assert work.doi == "10.1000/test"

    @respx.mock
    def test_returns_none_below_threshold(self):
        respx.get(f"{BASE_URL}/works").mock(
            return_value=httpx.Response(
                200, json={"results": [_work_dict(title="Completely Unrelated Result")]}
            )
        )
        work = _client().fetch_work_by_title("Test Paper About Deep Learning")
        assert work is None

    def test_short_title_returns_none_without_request(self):
        work = _client().fetch_work_by_title("Hi")
        assert work is None

    @respx.mock
    def test_custom_min_similarity(self):
        respx.get(f"{BASE_URL}/works").mock(
            return_value=httpx.Response(
                200, json={"results": [_work_dict(title="Test Paper About Deep Learning Stuff")]}
            )
        )
        # Same words plus one extra → similarity ~6/7 ≈ 0.86, passes 0.5 but let's verify
        work = _client().fetch_work_by_title(
            "Test Paper About Deep Learning", min_similarity=0.5
        )
        assert work is not None


class TestFetchWorkById:
    @respx.mock
    def test_returns_dict_keyed_by_id(self):
        respx.get(f"{BASE_URL}/works").mock(
            return_value=httpx.Response(200, json={"results": [_work_dict()]})
        )
        result = _client().fetch_works_by_id(["https://openalex.org/W1"])
        assert "https://openalex.org/W1" in result


class TestApiKey:
    @respx.mock
    def test_explicit_api_key_is_sent(self):
        route = respx.get(f"{BASE_URL}/works").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        OpenAlexClient(MAILTO, api_key="premium-key").fetch_works_by_doi(["10.1000/x"])
        assert "api_key=premium-key" in str(route.calls[0].request.url)

    @respx.mock
    def test_no_api_key_param_when_unset(self):
        route = respx.get(f"{BASE_URL}/works").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        # Explicit empty overrides any Config default -> polite pool only.
        OpenAlexClient(MAILTO, api_key="").fetch_works_by_doi(["10.1000/x"])
        assert "api_key=" not in str(route.calls[0].request.url)

    @respx.mock
    def test_api_key_defaults_from_config(self):
        route = respx.get(f"{BASE_URL}/works").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        with patch("config.Config.OPENALEX_API_KEY", "cfg-key"):
            OpenAlexClient(MAILTO).fetch_works_by_doi(["10.1000/x"])
        assert "api_key=cfg-key" in str(route.calls[0].request.url)
