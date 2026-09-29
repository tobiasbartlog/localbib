from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest

from config import Config  # noqa: F401 — kept for tests that patch Config attributes
from llm_client import EMBED_REQUEST_CHUNK_SIZE, LLMClient, LLMClientError, embed_texts
from tests.llm_helpers import configure_llm

URL = "https://api.example.com/chat"
MODEL = "test-model"
KEY = "test-key"


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """No real sleep in tests: record every backoff wait instead (issue #113,
    Sleep über den patchbaren Modulverweis ``llm_client.time.sleep``)."""
    recorded: list[float] = []
    monkeypatch.setattr("llm_client.time.sleep", recorded.append)
    return recorded


def _client() -> LLMClient:
    return LLMClient(URL, MODEL, KEY)


def _ok_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


def _error_response(status: int, retry_after: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return resp


class TestComplete:
    def test_returns_content(self):
        with patch("llm_client.requests.post", return_value=_ok_response("hello")) as mock_post:
            result = _client().complete([{"role": "user", "content": "hi"}])
        assert result == "hello"
        mock_post.assert_called_once()

    def test_strips_json_fences(self):
        raw = "```json\n{\"key\": 1}\n```"
        with patch("llm_client.requests.post", return_value=_ok_response(raw)):
            result = _client().complete([])
        assert result == '{"key": 1}'

    def test_strips_plain_fences(self):
        raw = "```\nsome text\n```"
        with patch("llm_client.requests.post", return_value=_ok_response(raw)):
            result = _client().complete([])
        assert result == "some text"

    def test_passes_temperature(self):
        with patch("llm_client.requests.post", return_value=_ok_response("ok")) as mock_post:
            _client().complete([], temperature=0.3)
        payload = mock_post.call_args.kwargs["json"]
        assert payload["temperature"] == 0.3

    def test_omits_temperature_when_none(self):
        with patch("llm_client.requests.post", return_value=_ok_response("ok")) as mock_post:
            _client().complete([])
        payload = mock_post.call_args.kwargs["json"]
        assert "temperature" not in payload

    def test_retries_on_5xx(self):
        responses = [_error_response(503), _ok_response("ok")]
        with patch("llm_client.requests.post", side_effect=responses) as mock_post:
            result = _client().complete([])
        assert result == "ok"
        assert mock_post.call_count == 2

    def test_retries_on_429_with_retry_after(self, sleeps):
        responses = [_error_response(429, retry_after="7"), _ok_response("ok")]
        with patch("llm_client.requests.post", side_effect=responses) as mock_post:
            result = _client().complete([])
        assert result == "ok"
        assert mock_post.call_count == 2
        assert sleeps == [7.0]

    def test_429_without_retry_after_uses_default_backoff(self, sleeps):
        responses = [_error_response(429), _ok_response("ok")]
        with patch("llm_client.requests.post", side_effect=responses):
            assert _client().complete([]) == "ok"
        assert len(sleeps) == 1
        assert sleeps[0] > 0

    def test_retry_after_is_capped_at_single_wait_max(self, sleeps):
        responses = [_error_response(429, retry_after="300"), _ok_response("ok")]
        with patch("llm_client.requests.post", side_effect=responses):
            assert _client().complete([]) == "ok"
        assert sleeps == [30.0]

    def test_persistent_429_raises_after_three_attempts(self, sleeps):
        with patch(
            "llm_client.requests.post", return_value=_error_response(429, retry_after="1")
        ) as mock_post:
            with pytest.raises(LLMClientError, match="HTTP 429"):
                _client().complete([])
        assert mock_post.call_count == 3
        assert sleeps == [1.0, 1.0]

    def test_raises_on_4xx(self):
        with patch("llm_client.requests.post", return_value=_error_response(400)):
            with pytest.raises(LLMClientError, match="HTTP 400"):
                _client().complete([])

    def test_retries_without_temperature_when_unsupported(self):
        """GPT-5-family models reject a non-default temperature with a 400.
        The client must drop it and retry (once)."""
        rejected = MagicMock()
        rejected.status_code = 400
        rejected.text = (
            '{"error": {"message": "Unsupported value: \'temperature\' does not '
            "support 0.2 with this model.\", \"code\": \"unsupported_value\"}}"
        )
        responses = [rejected, _ok_response("ok")]
        with patch("llm_client.requests.post", side_effect=responses) as mock_post:
            result = _client().complete([], temperature=0.2)
        assert result == "ok"
        assert mock_post.call_count == 2
        # The retry payload must not carry the rejected temperature.
        assert "temperature" not in mock_post.call_args_list[1].kwargs["json"]

    def test_4xx_error_includes_provider_body(self):
        resp = MagicMock()
        resp.status_code = 400
        resp.text = '{"error": {"message": "bad model"}}'
        with patch("llm_client.requests.post", return_value=resp):
            with pytest.raises(LLMClientError, match="bad model"):
                _client().complete([])

    def test_message_fallback_field(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"message": {"content": "from message field"}}
        with patch("llm_client.requests.post", return_value=resp):
            result = _client().complete([])
        assert result == "from message field"

    def test_empty_response_returns_empty_string(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {}
        with patch("llm_client.requests.post", return_value=resp):
            result = _client().complete([])
        assert result == ""


class TestLastError:
    """``LLMClient.last_error`` lets a caller that degrades (complete_json
    returned its default) find out afterwards WHY: rate limit vs. key vs.
    host. The import surfaces it in the UI instead of leaving fields empty."""

    def test_fresh_client_has_no_error(self):
        assert _client().last_error is None

    def test_persistent_429_records_rate_limit(self, sleeps):
        c = _client()
        with patch("llm_client.requests.post", return_value=_error_response(429)):
            with pytest.raises(LLMClientError):
                c.complete([])
        assert c.last_error is not None
        assert c.last_error.status == 429
        assert c.last_error.kind == "rate_limited"
        assert c.last_error.model == MODEL

    def test_auth_rejection_is_kind_auth(self):
        c = _client()
        with patch("llm_client.requests.post", return_value=_error_response(401)):
            with pytest.raises(LLMClientError):
                c.complete([])
        assert c.last_error.kind == "auth"

    def test_network_error_has_no_status_and_is_unavailable(self, sleeps):
        c = _client()
        with patch("llm_client.requests.post", side_effect=ConnectionError("boom")):
            with pytest.raises(LLMClientError):
                c.complete([])
        assert c.last_error.status is None
        assert c.last_error.kind == "unavailable"
        assert c.last_error.model == MODEL

    def test_success_clears_previous_error(self, sleeps):
        c = _client()
        with patch("llm_client.requests.post", return_value=_error_response(429)):
            with pytest.raises(LLMClientError):
                c.complete([])
        with patch("llm_client.requests.post", return_value=_ok_response("ok")):
            assert c.complete([]) == "ok"
        assert c.last_error is None

    def test_complete_json_leaves_error_readable(self, sleeps):
        c = _client()
        with patch("llm_client.requests.post", return_value=_error_response(429)):
            assert c.complete_json([], expect=dict, default={}) == {}
        assert c.last_error is not None and c.last_error.kind == "rate_limited"

    def test_unparseable_answer_is_not_a_transport_failure(self):
        c = _client()
        with patch("llm_client.requests.post", return_value=_ok_response("no json here")):
            assert c.complete_json([], expect=dict, default={}) == {}
        assert c.last_error is None

    def test_as_failure_wire_shape(self):
        err = LLMClientError("HTTP 429: limited", status=429, model="m")
        assert err.as_failure("abstract") == {
            "step": "abstract", "kind": "rate_limited", "status": 429,
            "model": "m", "detail": "HTTP 429: limited",
        }


class TestStream:
    def _sse_lines(self, tokens: list[str], done: bool = True) -> list[bytes]:
        lines: list[bytes] = []
        for token in tokens:
            chunk = {"choices": [{"delta": {"content": token}}]}
            lines.append(f"data: {json.dumps(chunk)}".encode())
        if done:
            lines.append(b"data: [DONE]")
        return lines

    def test_yields_tokens(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.return_value = self._sse_lines(["hello", " world"])
        with patch("llm_client.requests.post", return_value=resp):
            tokens = list(_client().stream([]))
        assert tokens == ["hello", " world"]

    def test_stops_at_done(self):
        lines = self._sse_lines(["a", "b"], done=True) + [b"data: after-done"]
        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.return_value = lines
        with patch("llm_client.requests.post", return_value=resp):
            tokens = list(_client().stream([]))
        assert tokens == ["a", "b"]

    def test_raises_on_non_200(self):
        with patch("llm_client.requests.post", return_value=_error_response(401)):
            with pytest.raises(LLMClientError, match="HTTP 401"):
                list(_client().stream([]))

    def test_skips_malformed_sse_lines(self):
        lines = [b"data: not-json", b"data: [DONE]"]
        resp = MagicMock()
        resp.status_code = 200
        resp.iter_lines.return_value = lines
        with patch("llm_client.requests.post", return_value=resp):
            tokens = list(_client().stream([]))
        assert tokens == []


class TestParseJson:
    def test_plain_dict(self):
        assert LLMClient.parse_json('{"a": 1}') == {"a": 1}

    def test_plain_list(self):
        assert LLMClient.parse_json('[1, 2]', expect=list) == [1, 2]

    def test_fenced_json(self):
        assert LLMClient.parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_prose_wrapped_dict(self):
        assert LLMClient.parse_json('Gerne! {"a": 1} Hoffe das hilft.') == {"a": 1}

    def test_prose_wrapped_list(self):
        assert LLMClient.parse_json('Hier: [{"a": 1}] fertig.', expect=list) == [{"a": 1}]

    def test_wrong_type_returns_none(self):
        assert LLMClient.parse_json('[1]', expect=dict) is None
        assert LLMClient.parse_json('{"a": 1}', expect=list) is None

    def test_garbage_returns_none(self):
        assert LLMClient.parse_json("kein json weit und breit") is None

    def test_empty_returns_none(self):
        assert LLMClient.parse_json("") is None


class TestCompleteJson:
    def test_parses_ok_response(self):
        with patch("llm_client.requests.post", return_value=_ok_response('{"x": 1}')):
            assert _client().complete_json([], expect=dict, default={}) == {"x": 1}

    def test_prose_wrapped_response(self):
        raw = 'Antwort: [{"t": "a"}] — mehr gibt es nicht.'
        with patch("llm_client.requests.post", return_value=_ok_response(raw)):
            assert _client().complete_json([], expect=list, default=[]) == [{"t": "a"}]

    def test_unparseable_returns_default(self):
        with patch("llm_client.requests.post", return_value=_ok_response("prosa ohne json")):
            assert _client().complete_json([], expect=list, default=[]) == []

    def test_wrong_type_returns_default(self):
        with patch("llm_client.requests.post", return_value=_ok_response('{"kein": "array"}')):
            assert _client().complete_json([], expect=list, default=[]) == []

    def test_http_error_returns_default(self):
        with patch("llm_client.requests.post", return_value=_error_response(400)):
            assert _client().complete_json([], expect=dict, default={}) == {}


def _embed_response(vectors: list[list[float]]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
    }
    return resp


class TestEmbedTexts:
    @pytest.fixture(autouse=True)
    def _embed_config(self, monkeypatch):
        configure_llm(monkeypatch, embedding="test-embed-model",
                      base_url="https://api.example.com", api_key=KEY)

    def test_returns_one_vector_per_text(self):
        with patch(
            "llm_client.requests.post",
            return_value=_embed_response([[0.1, 0.2], [0.3, 0.4]]),
        ) as mock_post:
            result = embed_texts(["a", "b"])
        assert result == [[0.1, 0.2], [0.3, 0.4]]
        mock_post.assert_called_once()
        assert mock_post.call_args.args[0] == "https://api.example.com/embeddings"

    def test_empty_input_returns_empty_without_request(self):
        with patch("llm_client.requests.post") as mock_post:
            assert embed_texts([]) == []
        mock_post.assert_not_called()

    def test_chunks_requests_at_limit(self):
        texts = [f"text-{i}" for i in range(EMBED_REQUEST_CHUNK_SIZE + 5)]
        responses = [
            _embed_response([[float(i)] for i in range(EMBED_REQUEST_CHUNK_SIZE)]),
            _embed_response([[float(i)] for i in range(5)]),
        ]
        with patch("llm_client.requests.post", side_effect=responses) as mock_post:
            result = embed_texts(texts)
        assert len(result) == len(texts)
        assert mock_post.call_count == 2
        first_input = mock_post.call_args_list[0].kwargs["json"]["input"]
        second_input = mock_post.call_args_list[1].kwargs["json"]["input"]
        assert len(first_input) == EMBED_REQUEST_CHUNK_SIZE
        assert len(second_input) == 5

    def test_retries_once_on_5xx(self):
        responses = [_error_response(503), _embed_response([[0.1]])]
        with patch("llm_client.requests.post", side_effect=responses) as mock_post:
            result = embed_texts(["a"])
        assert result == [[0.1]]
        assert mock_post.call_count == 2

    def test_incomplete_response_raises(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": [{"index": 0, "embedding": [0.1]}]}
        with patch("llm_client.requests.post", return_value=resp):
            with pytest.raises(LLMClientError, match="unvollstaendig"):
                embed_texts(["a", "b"])

    def test_persistent_5xx_raises_after_three_attempts(self):
        with patch(
            "llm_client.requests.post", return_value=_error_response(503)
        ) as mock_post:
            with pytest.raises(LLMClientError):
                embed_texts(["a"])
        assert mock_post.call_count == 3

    def test_retries_on_429_with_retry_after(self, sleeps):
        responses = [_error_response(429, retry_after="5"), _embed_response([[0.1]])]
        with patch("llm_client.requests.post", side_effect=responses) as mock_post:
            result = embed_texts(["a"])
        assert result == [[0.1]]
        assert mock_post.call_count == 2
        assert sleeps == [5.0]

    def test_retry_after_is_capped_at_single_wait_max(self, sleeps):
        responses = [_error_response(429, retry_after="300"), _embed_response([[0.1]])]
        with patch("llm_client.requests.post", side_effect=responses):
            assert embed_texts(["a"]) == [[0.1]]
        assert sleeps == [30.0]

    def test_persistent_429_raises_llm_client_error(self, sleeps):
        with patch(
            "llm_client.requests.post", return_value=_error_response(429, retry_after="2")
        ) as mock_post:
            with pytest.raises(LLMClientError, match="HTTP 429"):
                embed_texts(["a"])
        assert mock_post.call_count == 3
        assert sleeps == [2.0, 2.0]

    def test_embedding_role_on_its_own_connection(self, monkeypatch):
        """The embedding role may live on another connection than the chat
        roles — with its own URL and its own (here: no) key."""
        configure_llm(monkeypatch, embedding="test-embed-model",
                      embed_base_url="http://localhost:11434/v1", embed_api_key="")
        with patch(
            "llm_client.requests.post", return_value=_embed_response([[0.1]])
        ) as mock_post:
            embed_texts(["a"])
        assert mock_post.call_args.args[0] == "http://localhost:11434/v1/embeddings"
        assert "Authorization" not in mock_post.call_args.kwargs["headers"]

    def test_unbound_role_raises_not_implemented(self, monkeypatch):
        configure_llm(monkeypatch, embedding="")
        with pytest.raises(NotImplementedError):
            embed_texts(["a"])

    def test_query_mode_adds_instruction_prefix(self):
        with patch(
            "llm_client.requests.post", return_value=_embed_response([[0.1]])
        ) as mock_post:
            embed_texts(["what is x"], mode="query")
        sent = mock_post.call_args.kwargs["json"]["input"][0]
        assert sent.startswith("Instruct:")
        assert sent.endswith("what is x")

    def test_document_mode_leaves_text_unchanged(self):
        with patch(
            "llm_client.requests.post", return_value=_embed_response([[0.1]])
        ) as mock_post:
            embed_texts(["plain text"], mode="document")
        assert mock_post.call_args.kwargs["json"]["input"][0] == "plain text"

    def test_invalid_mode_raises_value_error(self):
        with pytest.raises(ValueError):
            embed_texts(["a"], mode="bogus")
