#!/usr/bin/env python3
"""Zentrale Konfiguration + Logging-Setup.

Foundational module: keine Importe aus anderen literature_manager-Sub-Modulen,
damit es importzyklenfrei bleibt.
"""

import os
import shutil
import logging


# =============================================================================
# KONFIGURATION
# =============================================================================

# Polar-Store der ausgelieferten App (ADR-0015), aufgesetzt in #141. Beides
# sind oeffentliche Kennungen: die Organisations-ID adressiert die
# Aktivierungs-API, die Checkout-URL ist der Kauflink. Kein Secret - der
# Lizenzschluessel des Kaeufers ist das Geheimnis, nicht der Laden.
POLAR_ORGANIZATION_ID_DEFAULT = "9094ce24-7aa7-4e41-915c-7f9eaccca343"
POLAR_CHECKOUT_URL_DEFAULT = (
    "https://buy.polar.sh/polar_cl_ElgxYcLFsyVN4g0xWdBBGEV0tVh92y7XskHaS1hDgBD"
)


class Config:
    """Zentrale Konfiguration."""

    # Pfade (werden beim Start gesetzt)
    BASE_DIR: str = ""
    INPUT_DIR: str = ""
    ALL_DIR: str = ""
    CATEGORIES_DIR: str = ""
    DB_PATH: str = ""

    # LLM-Anbieter (alle OpenAI-kompatibel: /chat/completions + /models)
    LLM_PROVIDERS = {
        "kiconnect":  {"label": "KI Connect NRW (RWTH)",      "base_url": "https://chat.kiconnect.nrw/api/v1"},
        "openai":     {"label": "OpenAI",                      "base_url": "https://api.openai.com/v1"},
        "openrouter": {"label": "OpenRouter (viele Modelle)",  "base_url": "https://openrouter.ai/api/v1"},
        "groq":       {"label": "Groq",                        "base_url": "https://api.groq.com/openai/v1"},
        "deepseek":   {"label": "DeepSeek",                    "base_url": "https://api.deepseek.com/v1"},
        "mistral":    {"label": "Mistral",                     "base_url": "https://api.mistral.ai/v1"},
        "custom":     {"label": "Custom (OpenAI-kompatibel)",  "base_url": ""},
    }

    # Von reload_from_env() gesetzt (einzige Stelle mit den Env-Defaults) —
    # beim Import und nach jedem Settings-Save.
    # Leer = noch kein Anbieter gewaehlt (frische Installation). Das Onboarding
    # (#140) setzt ihn; bis dahin bleiben LLM-Features aus, statt gegen einen
    # Endpunkt zu laufen, den der Nutzer gar nicht erreichen kann.
    LLM_PROVIDER = ""
    LLM_BASE_URL = ""  # nur fuer Custom / Override
    LLM_API_KEY = ""
    LLM_MODEL = ""
    # Zweites Default-Modell fuer einfache Tasks (Extraktion/Kategorisierung).
    # Leer -> Fallback auf LLM_MODEL (siehe fast_model()).
    LLM_MODEL_FAST = ""
    AVAILABLE_MODELS: list = []

    @classmethod
    def reasoning_model(cls) -> str:
        """Modell fuer Denkaufgaben (Chat, Analyse). Immer LLM_MODEL."""
        return cls.LLM_MODEL

    @classmethod
    def fast_model(cls) -> str:
        """Modell fuer einfache Tasks (Extraktion, Kategorisierung, Metadaten).
        Faellt auf das Denk-Modell zurueck, wenn LLM_MODEL_FAST nicht gesetzt ist."""
        return cls.LLM_MODEL_FAST or cls.LLM_MODEL

    # Aufgeloeste Endpunkte (von resolve_llm gesetzt) + Backward-Compat-Aliase
    LLM_CHAT_URL = ""
    LLM_MODELS_URL = ""
    # Historische Aliase (viele Aufrufstellen gaten auf KICONNECT_API_KEY).
    # Der Name blieb, der KI-Connect-Default ist weg: leer = kein Endpunkt.
    KICONNECT_API_URL = ""
    KICONNECT_API_KEY = ""

    # Embedding-Modell fuer semantische Suche/Clustering (Kern-Slice H0,
    # Issue #97). Anders als LLM_MODEL KEIN hartkodierter Fallback: leer
    # (weder .env noch env var gesetzt) -> llm_client.embed_texts() degradiert
    # per NotImplementedError (discovery A4, PRD-Entscheidung "Degradation
    # statt Hard-Fail"). .env.template traegt "qwen3-embedding-8b" als
    # empfohlenen Wert fuer Neuinstallationen; bestehende Installationen ohne
    # den Key bleiben unveraendert ohne Embeddings. LLM_EMBED_URL
    # ueberschreibt die aus KICONNECT_API_URL abgeleitete /embeddings-URL
    # (jeder OpenAI-kompatible Anbieter, z. B. Ollama, fuer Nutzer ohne
    # KI-Connect-Zugriff).
    LLM_EMBED_MODEL = ""
    LLM_EMBED_URL = ""

    @classmethod
    def reload_from_env(cls):
        """Liest alle zur Laufzeit aenderbaren Settings aus der Env — beim
        Import UND nach jedem Settings-Save (PUT /api/settings). Die einzige
        Stelle, an der die Env-Defaults stehen (frueher drifteten config.py
        und routers/settings.py auseinander, z. B. beim LLM_MODEL-Default)."""
        cls.LLM_PROVIDER = os.getenv("LLM_PROVIDER", "")
        cls.LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
        cls.LLM_API_KEY = os.getenv("LLM_API_KEY", "") or os.getenv("KICONNECT_API_KEY", "")
        cls.LLM_MODEL = os.getenv("LLM_MODEL", "")
        cls.LLM_MODEL_FAST = os.getenv("LLM_MODEL_FAST", "")
        cls.LLM_EMBED_MODEL = os.getenv("LLM_EMBED_MODEL", "")
        cls.LLM_EMBED_URL = os.getenv("LLM_EMBED_URL", "")
        cls.AVAILABLE_MODELS = []  # bei Provider-Wechsel neu laden
        # Leer per Default: die hoefliche mailto-Kennung ist die Adresse DES
        # NUTZERS (Onboarding, #140) — nie eine mitgelieferte fremde Adresse.
        cls.CROSSREF_MAILTO = os.getenv("CROSSREF_MAILTO", "").strip()
        cls.OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "")
        cls.WATCH_INTERVAL = int(os.getenv("WATCH_INTERVAL", "5"))
        cls.MAX_OCR_PAGES = int(os.getenv("MAX_OCR_PAGES", "5"))
        cls.UNLOCK_PDFS = os.getenv("UNLOCK_PDFS", "true").lower() == "true"
        # Polar: mitgelieferte Defaults (oeffentliche Kennungen), per Env
        # ueberschreibbar - POLAR_API_BASE zeigt beim Testen auf die Sandbox.
        # "or Default": ein leerer Eintrag in der .env (z. B. aus der Vorlage
        # uebernommen) darf den mitgelieferten Store nicht ausknipsen.
        cls.POLAR_API_BASE = (os.getenv("POLAR_API_BASE", "").strip()
                              or "https://api.polar.sh").rstrip("/")
        cls.POLAR_ORGANIZATION_ID = (os.getenv("POLAR_ORGANIZATION_ID", "").strip()
                                     or POLAR_ORGANIZATION_ID_DEFAULT)
        cls.POLAR_CHECKOUT_URL = (os.getenv("POLAR_CHECKOUT_URL", "").strip()
                                  or POLAR_CHECKOUT_URL_DEFAULT)
        cls.resolve_llm()

    @classmethod
    def resolve_llm(cls):
        """Loest LLM_PROVIDER + optionale Overrides zu konkreten Endpunkten auf.

        Setzt LLM_CHAT_URL / LLM_MODELS_URL und haelt die Aliase
        KICONNECT_API_URL / KICONNECT_API_KEY synchron, damit bestehende
        Aufrufstellen unveraendert weiterfunktionieren.
        """
        provider = (cls.LLM_PROVIDER or "").lower()
        preset = cls.LLM_PROVIDERS.get(provider, cls.LLM_PROVIDERS["custom"])
        base = (cls.LLM_BASE_URL or preset["base_url"]).rstrip("/")
        cls.LLM_API_KEY = cls.LLM_API_KEY or os.getenv("KICONNECT_API_KEY", "")
        cls.LLM_CHAT_URL = f"{base}/chat/completions" if base else ""
        cls.LLM_MODELS_URL = f"{base}/models" if base else ""
        cls.KICONNECT_API_URL = cls.LLM_CHAT_URL
        cls.KICONNECT_API_KEY = cls.LLM_API_KEY
        return cls.LLM_CHAT_URL

    # CrossRef API (kostenlos, kein Key nötig)
    CROSSREF_API_URL = "https://api.crossref.org/works/"
    CROSSREF_MAILTO = ""  # von reload_from_env gesetzt

    @classmethod
    def polite_mailto(cls) -> str:
        """Kontaktadresse fuer CrossRef/OpenAlex — die **des Nutzers**.

        Leer, solange das Onboarding (#140) keine gesetzt hat. Aufrufstellen
        lassen den Parameter/Header dann weg: eine erfundene oder fremde
        Adresse im Namen des Nutzers zu senden ist keine Hoeflichkeit."""
        return (cls.CROSSREF_MAILTO or "").strip()

    @classmethod
    def user_agent(cls, product: str = "LiteraturManager/1.0") -> str:
        """User-Agent mit hoeflicher mailto-Kennung — ohne, wenn keine gesetzt."""
        mailto = cls.polite_mailto()
        return f"{product} (mailto:{mailto})" if mailto else product

    # -------------------------------------------------------------------
    # Polar (Lizenz / Aktivierung, ADR-0015)
    # -------------------------------------------------------------------
    # Organisations-ID und Checkout-URL sind **oeffentliche** Kennungen, keine
    # Geheimnisse: sie werden mit der App ausgeliefert, damit ein gekaufter
    # Lizenzschluessel ohne Konfiguration aktivierbar ist. Env-Variablen
    # ueberschreiben sie (z. B. auf die Polar-Sandbox beim Testen).
    POLAR_API_BASE = ""          # von reload_from_env gesetzt
    POLAR_ORGANIZATION_ID = ""   # von reload_from_env gesetzt
    POLAR_CHECKOUT_URL = ""      # von reload_from_env gesetzt

    # OpenAlex Premium API key (optional) — hebt das Rate-Limit/Budget an
    # (Premium-Pool statt Polite-Pool). Leer -> nur mailto-Polite-Pool.
    OPENALEX_API_KEY = ""  # von reload_from_env gesetzt

    # Watchdog (von reload_from_env gesetzt)
    WATCH_INTERVAL = 5

    # OCR (MAX_OCR_PAGES von reload_from_env gesetzt; Rest nur beim Import)
    MAX_OCR_PAGES = 5
    TESSERACT_PATH = os.getenv("TESSERACT_PATH", "")
    OCR_LANGUAGES = os.getenv("OCR_LANGUAGES", "deu+eng+fra")
    OCR_MIN_TEXT_LENGTH = int(os.getenv("OCR_MIN_TEXT_LENGTH", "50"))

    # PDF-Schutz (von reload_from_env gesetzt)
    UNLOCK_PDFS = True

    @classmethod
    def _detect_tesseract(cls):
        """Versucht Tesseract automatisch zu finden."""
        if cls.TESSERACT_PATH and os.path.isdir(cls.TESSERACT_PATH):
            return cls.TESSERACT_PATH
        # Typische Windows-Installationspfade
        candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR"),
            r"C:\Program Files\Tesseract-OCR",
            r"C:\Program Files (x86)\Tesseract-OCR",
        ]
        for c in candidates:
            if os.path.isdir(c) and os.path.exists(os.path.join(c, "tesseract.exe")):
                cls.TESSERACT_PATH = c
                return c
        # Unix: prüfe ob tesseract im PATH ist
        if shutil.which("tesseract"):
            cls.TESSERACT_PATH = "__system__"
            return cls.TESSERACT_PATH
        return ""

    @classmethod
    def setup_tesseract(cls):
        """Setzt Tesseract im PATH und gibt tessdata-Pfad zurück."""
        tess = cls._detect_tesseract()
        if not tess:
            return None
        if tess != "__system__":
            os.environ["PATH"] = tess + os.pathsep + os.environ.get("PATH", "")
            tessdata = os.path.join(tess, "tessdata")
            if os.path.isdir(tessdata):
                return tessdata
        return None

    @classmethod
    def init_paths(cls, base_dir: str):
        """Initialisiert alle Pfade relativ zum Base-Verzeichnis."""
        cls.BASE_DIR = base_dir
        cls.INPUT_DIR = os.path.join(base_dir, "input")
        cls.ALL_DIR = os.path.join(base_dir, "all")
        cls.CATEGORIES_DIR = os.path.join(base_dir, "kategorien")
        cls.DB_PATH = os.path.join(base_dir, "literatur.db")

        # Ordner erstellen
        for d in [cls.INPUT_DIR, cls.ALL_DIR, cls.CATEGORIES_DIR]:
            os.makedirs(d, exist_ok=True)


# Env-Settings + LLM-Endpunkte beim Import laden (Provider-Preset + Overrides)
Config.reload_from_env()


# =============================================================================
# LOGGING
# =============================================================================

def setup_logging(verbose: bool = False):
    """Konfiguriert Logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                os.path.join(Config.BASE_DIR, "literatur_manager.log"),
                encoding="utf-8"
            ) if Config.BASE_DIR else logging.StreamHandler()
        ]
    )
