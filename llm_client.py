from __future__ import annotations

import json
import logging
import re
import time
from typing import Iterator

import requests

import ca_trust
from config import Config


class LLMClientError(Exception):
    pass


# =============================================================================
# 429/5xx-Backoff (issue #113) — geteilt von complete() und _embed_request().
#
# KIConnect drosselt mit Minuten-Limits: ein 429 darf einen langen Lauf nicht
# abbrechen, aber ein interaktiver UI-Request darf auch nicht minutenlang
# haengen. Deshalb hart gedeckelt: max. 3 Versuche, Einzelwartung <= 30 s
# (auch wenn Retry-After mehr verlangt), Gesamtwartezeit <= 60 s — danach
# LLMClientError wie bisher. Sleep laeuft ueber den Modulverweis
# ``llm_client.time.sleep`` (Tests patchen ihn, kein realer Sleep).
# =============================================================================

RETRY_MAX_ATTEMPTS = 3
RETRY_MAX_SINGLE_WAIT = 30.0
RETRY_MAX_TOTAL_WAIT = 60.0
_RETRY_DEFAULT_WAITS = (2.0, 10.0)  # 429/5xx ohne (nutzbaren) Retry-After


def _retry_wait(resp, attempt: int) -> float:
    """Wartezeit vor Wiederholungsversuch ``attempt`` (0-basiert) fuer eine
    429/5xx-Antwort. Ein numerischer ``Retry-After``-Header gewinnt, wird aber
    auf :data:`RETRY_MAX_SINGLE_WAIT` gekappt; ohne Header greift der
    Default-Backoff. ``resp=None`` (Netzwerkfehler) nutzt den Default."""
    retry_after: float | None = None
    if resp is not None:
        try:
            retry_after = float(resp.headers.get("Retry-After"))
        except (AttributeError, TypeError, ValueError):
            retry_after = None
    if retry_after is None:
        retry_after = _RETRY_DEFAULT_WAITS[min(attempt, len(_RETRY_DEFAULT_WAITS) - 1)]
    return max(0.0, min(retry_after, RETRY_MAX_SINGLE_WAIT))


def _send_with_retry(send):
    """Fuehrt ``send()`` (ein HTTP-POST) mit gedeckeltem Backoff aus.

    Wiederholt bei 429, 5xx und Netzwerkfehlern; alle anderen Antworten
    (2xx/4xx) gehen unveraendert an den Aufrufer zurueck, der sie wie bisher
    behandelt. Nach Erschoepfung der Versuche bzw. des Wartezeit-Budgets wird
    die letzte Fehlerantwort zurueckgegeben (der Aufrufer wirft daraus seinen
    LLMClientError) bzw. der letzte Netzwerkfehler als LLMClientError geworfen.
    """
    waited = 0.0
    resp = None
    last_exc: Exception | None = None
    for attempt in range(RETRY_MAX_ATTEMPTS):
        resp, last_exc = None, None
        try:
            resp = send()
        except Exception as exc:  # noqa: BLE001 - Netzwerkfehler sind retrybar
            last_exc = exc
        else:
            if resp.status_code != 429 and resp.status_code < 500:
                return resp
        if attempt == RETRY_MAX_ATTEMPTS - 1:
            break
        wait = _retry_wait(resp, attempt)
        if waited + wait > RETRY_MAX_TOTAL_WAIT:
            break
        status = resp.status_code if resp is not None else "Netzwerkfehler"
        logging.warning("LLM %s, Retry in %.1fs (Versuch %d/%d)",
                        status, wait, attempt + 2, RETRY_MAX_ATTEMPTS)
        time.sleep(wait)
        waited += wait
    if last_exc is not None:
        raise LLMClientError(str(last_exc)) from last_exc
    return resp


# Model Routing: die einzige Stelle, die weiss, welcher Task auf welcher
# Modell-Stufe laeuft. Call-Sites nennen ihre Absicht (llm_for("categorize")),
# nicht das Modell. Stufen: "fast" -> Config.fast_model(),
# "reasoning" -> Config.reasoning_model().
TASK_MODELS: dict[str, str] = {
    "categorize":        "fast",
    "metadata_extract":  "fast",
    "reference_extract": "fast",
    "abstract":          "fast",
    "category_suggest":  "fast",
    "research_chat":     "reasoning",
    "thesis_analysis":   "reasoning",
    "model_suggest":     "reasoning",
    "plugin":            "reasoning",
    # Plugin-Requests, die ihre Absicht mit tier="fast" nennen (z. B.
    # Begriffs-Extraktion pro Paper — viele kleine, schematische Aufrufe). Der
    # Tier bleibt damit hier in der Tabelle; das Plugin nennt nie ein Modell.
    "plugin_fast":       "fast",
}


def llm_for(task: str, model: str | None = None) -> "LLMClient":
    """Baut den LLMClient fuer einen Task laut TASK_MODELS.

    Ein unbekannter Task ist ein Programmierfehler (KeyError).
    ``model`` uebersteuert die Tabelle (z. B. Plugin-Requests mit explizitem
    Modellwunsch)."""
    tier = TASK_MODELS[task]
    resolved = model or (
        Config.fast_model() if tier == "fast" else Config.reasoning_model()
    )
    return LLMClient(Config.KICONNECT_API_URL, resolved, Config.KICONNECT_API_KEY)


# =============================================================================
# Embeddings — Kern-Slice H0 (Issue #97, geteilt mit den Plugins).
#
# Zentrale Stelle fuer den Embedding-HTTP-Aufruf: Request-Limit-Chunking,
# ein 5xx-Retry und Antwort-Validierung liegen genau hier, statt in jedem
# Aufrufer (host_services._CoreLlmApi.embed, der spaetere Semantik-Indexer,
# die Plugins) dupliziert zu werden.
# =============================================================================

EMBED_REQUEST_CHUNK_SIZE = 50

# Qwen3-Embedding ist asymmetrisch: Anfragen (Queries) profitieren von einem
# Instruktions-Prefix, Dokumente werden unveraendert embedded. Siehe
# https://huggingface.co/Qwen/Qwen3-Embedding-8B — "Instruct: ...\nQuery: ...".
_EMBED_QUERY_INSTRUCTION = (
    "Instruct: Given a search query, retrieve relevant passages that answer "
    "the query\nQuery: "
)


def _embed_url() -> str:
    """Embeddings-Endpunkt: ``LLM_EMBED_URL`` falls gesetzt (jeder
    OpenAI-kompatible Anbieter, z. B. Ollama), sonst von ``KICONNECT_API_URL``
    abgeleitet (``/chat/completions`` -> ``/embeddings``)."""
    override = (Config.LLM_EMBED_URL or "").strip()
    if override:
        return override
    return Config.KICONNECT_API_URL.replace("/chat/completions", "/embeddings")


def _embed_request(url: str, headers: dict, model: str, chunk: list[str]) -> list[list[float]]:
    """Ein Embeddings-Request fuer <= EMBED_REQUEST_CHUNK_SIZE Texte, mit
    gedeckeltem 429/5xx-Backoff und Vollstaendigkeits-Pruefung der Antwort."""
    resp = _send_with_retry(
        lambda: requests.post(
            url, headers=headers, json={"model": model, "input": chunk}, timeout=60,
            verify=ca_trust.ca_bundle(),
        )
    )
    if resp.status_code == 200:
        data = sorted(resp.json().get("data", []), key=lambda d: d.get("index", 0))
        vectors = [d.get("embedding") for d in data]
        if len(vectors) != len(chunk) or any(v is None for v in vectors):
            raise LLMClientError("Embeddings-Antwort unvollstaendig")
        return vectors
    raise LLMClientError(f"Embeddings HTTP {resp.status_code}: {LLMClient._error_body(resp)}")


def embed_texts(texts: list[str], *, mode: str = "document") -> list[list[float]]:
    """Embeddet ``texts`` ueber den konfigurierten Embedding-Endpunkt.

    Genau eine Stelle fuer: Request-Limit-Chunking (~``EMBED_REQUEST_CHUNK_SIZE``
    Texte/Request), einen Retry bei 5xx und Antwort-Validierung (Anzahl +
    Vollstaendigkeit der Vektoren). ``mode="query"`` fuegt Qwen3-Embedding's
    empfohlenen Instruktions-Prefix hinzu (asymmetrisches Retrieval); der
    Default ``mode="document"`` laesst die Texte unveraendert.

    Wirft ``NotImplementedError``, wenn kein Embedding-Modell konfiguriert ist
    (``LLM_EMBED_MODEL`` leer) — Aufrufer muessen degradieren (discovery A4,
    z. B. auf lexikalisches Ranking). Wirft ``LLMClientError`` bei HTTP- oder
    Validierungsfehlern.
    """
    if mode not in ("query", "document"):
        raise ValueError(f"embed_texts: unbekannter mode {mode!r}")
    model = (Config.LLM_EMBED_MODEL or "").strip()
    if not model:
        raise NotImplementedError("Kein Embedding-Modell konfiguriert (LLM_EMBED_MODEL).")
    if not texts:
        return []

    inputs = [_EMBED_QUERY_INSTRUCTION + t for t in texts] if mode == "query" else list(texts)
    url = _embed_url()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {Config.KICONNECT_API_KEY}",
    }

    vectors: list[list[float]] = []
    for i in range(0, len(inputs), EMBED_REQUEST_CHUNK_SIZE):
        chunk = inputs[i : i + EMBED_REQUEST_CHUNK_SIZE]
        vectors.extend(_embed_request(url, headers, model, chunk))
    return vectors


class LLMClient:
    def __init__(self, url: str, model: str, api_key: str) -> None:
        self._url = url
        self._model = model
        self._api_key = api_key

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

    @staticmethod
    def _extract_content(result: dict) -> str:
        if "choices" in result:
            return result["choices"][0]["message"]["content"]
        if "message" in result:
            return result["message"].get("content", "")
        if "content" in result:
            return result["content"]
        return ""

    @staticmethod
    def _error_body(resp) -> str:
        """Provider error text (truncated) so 4xx/failed calls are diagnosable
        instead of collapsing to a bare status code."""
        try:
            return (resp.text or "").strip()[:300]
        except Exception:
            return ""

    @staticmethod
    def _is_unsupported_temperature(resp) -> bool:
        """True if a 4xx is the provider rejecting a non-default temperature.
        Some models (GPT-5 family) only accept the default temperature (1)."""
        try:
            body = resp.text or ""
        except Exception:
            return False
        return "temperature" in body and (
            "unsupported_value" in body or "does not support" in body
        )

    @staticmethod
    def strip_fences(text: str) -> str:
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*", "", text)
        return text.strip()

    @staticmethod
    def parse_json(text: str, expect: type = dict):
        """Tolerante JSON-Extraktion aus einer LLM-Antwort.

        Reihenfolge: strip_fences -> json.loads -> bei Fehlschlag Regex auf
        das aeusserste {...} bzw. [...] (Prosa um das JSON herum wird
        ignoriert). Gibt None zurueck, wenn nichts vom erwarteten Typ
        (``expect``: dict oder list) extrahierbar ist."""
        if not text:
            return None
        cleaned = LLMClient.strip_fences(text)
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            pattern = r"\[.*\]" if expect is list else r"\{.*\}"
            m = re.search(pattern, cleaned, re.DOTALL)
            if not m:
                return None
            try:
                data = json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                return None
        return data if isinstance(data, expect) else None

    def complete_json(
        self,
        messages: list[dict],
        expect: type = dict,
        default=None,
        timeout: int = 60,
        temperature: float | None = None,
    ):
        """complete() plus tolerantes Parsen der Antwort als JSON.

        Bei jedem Fehlschlag (HTTP-Fehler, unparsebare Antwort, falscher
        Typ) wird eine Warnung geloggt und ``default`` zurueckgegeben —
        Call-Sites brauchen kein eigenes try/except."""
        try:
            content = self.complete(messages, timeout=timeout, temperature=temperature)
        except Exception as exc:
            logging.warning("LLM-Aufruf fehlgeschlagen: %s", exc)
            return default
        data = self.parse_json(content, expect=expect)
        if data is None:
            logging.warning(
                "LLM-Antwort nicht als %s parsebar: %.200r",
                expect.__name__, content,
            )
            return default
        return data

    def complete(
        self,
        messages: list[dict],
        timeout: int = 60,
        temperature: float | None = None,
    ) -> str:
        payload: dict = {"model": self._model, "messages": messages}
        if temperature is not None:
            payload["temperature"] = temperature
        # Aeussere Schleife nur fuer den einmaligen Temperature-Drop-Retry;
        # 429/5xx/Netzwerkfehler behandelt _send_with_retry (gedeckelter Backoff).
        for _ in range(2):
            resp = _send_with_retry(
                lambda: requests.post(
                    self._url, headers=self._headers(), json=payload, timeout=timeout,
                    verify=ca_trust.ca_bundle(),
                )
            )
            if resp.status_code == 200:
                return self.strip_fences(self._extract_content(resp.json()).strip())
            if (
                resp.status_code == 400
                and "temperature" in payload
                and self._is_unsupported_temperature(resp)
            ):
                logging.warning(
                    "LLM rejected temperature=%s, retrying without it",
                    payload["temperature"],
                )
                del payload["temperature"]
                continue
            break
        raise LLMClientError(f"HTTP {resp.status_code}: {self._error_body(resp)}")

    def stream(self, messages: list[dict], timeout: int = 120) -> Iterator[str]:
        payload = {"model": self._model, "messages": messages, "stream": True}
        resp = requests.post(
            self._url, headers=self._headers(), json=payload, timeout=timeout, stream=True,
            verify=ca_trust.ca_bundle(),
        )
        if resp.status_code != 200:
            raise LLMClientError(f"HTTP {resp.status_code}")
        for line in resp.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str.startswith("data: "):
                continue
            data_str = line_str[6:]
            if data_str == "[DONE]":
                return
            try:
                chunk = json.loads(data_str)
                delta = chunk.get("choices", [{}])[0].get("delta", {})
                content = delta.get("content", "")
                if content:
                    yield content
            except json.JSONDecodeError:
                pass
