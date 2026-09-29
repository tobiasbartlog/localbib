#!/usr/bin/env python3
"""Zentrale Konfiguration + Logging-Setup.

Foundational module: keine Importe aus anderen literature_manager-Sub-Modulen,
damit es importzyklenfrei bleibt.
"""

import os
import shutil
import sys
import logging

# Leaf module (stdlib only) — the one exception to "no project imports" above,
# and only because it has none of its own. See llm_config's docstring.
import llm_config
import plugins_config


# =============================================================================
# KONFIGURATION
# =============================================================================


def resolve_env_path() -> str:
    """Where the ``.env`` lives — the ONE rule for every entry point.

    Frozen build (PyInstaller): the exe/_internal folder may be read-only
    (Program Files), so the persistent configuration sits next to the user's
    data at ``~/Literatur/.env``. Source tree: the repo's own ``.env``; a git
    worktree under ``.claude/worktrees/<name>/`` without one falls back to the
    main checkout's file three levels up. ``webapp.py`` and
    ``routers/settings.py`` used to carry a copy of this logic each; the LLM
    document (``llm.json``, see ``LLM_CONFIG_PATH``) needs the same answer from
    the CLI, which loads neither.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.expanduser("~"), "Literatur", ".env")
    here = os.path.dirname(os.path.abspath(__file__))
    env = os.path.join(here, ".env")
    if not os.path.exists(env):
        main_repo = os.path.normpath(os.path.join(here, "..", "..", "..", ".env"))
        if os.path.exists(main_repo):
            return main_repo
    return env


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    """Tolerant numeric env parse: missing, empty or non-numeric text falls
    back to ``default`` instead of raising.

    ``reload_from_env`` runs at import AND after every settings save, so a
    bare ``int(os.getenv(...))`` here is a startup crash waiting to happen —
    ``PUT /api/settings`` used to write an unvalidated value (e.g. the SPA
    clearing the field sends ``""``) straight to the ``.env``, and the next
    process (this one, on its next reload, or the very next app start) died
    on this line before anything else could run. Validation now also lives at
    the write site (``routers/settings.py``), but this parse stays tolerant
    regardless of how a bad value got into the ``.env``.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return default
    return value

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

    # Konfigurationsdateien: die .env (Skalare) und daneben llm.json (das
    # LLM-Verbindungsdokument). LLM_CONFIG_PATH per Env ueberschreibbar —
    # die Tests zeigen damit auf ein Temp-Verzeichnis, wie LITERATUR_BASE_DIR.
    ENV_PATH: str = resolve_env_path()
    LLM_CONFIG_PATH: str = ""  # von reload_from_env gesetzt

    # -------------------------------------------------------------------
    # LLM-Verbindungen (llm.json): benannte Verbindungen + drei Rollen
    # -------------------------------------------------------------------
    # Das Dokument ist die persistierte Wahrheit; llm_config.py kennt seine
    # Form. Eine alte .env (LLM_PROVIDER/LLM_API_KEY/LLM_MODEL/...) wird beim
    # Laden LESEND zu einer Verbindung "default" migriert — nichts wird
    # ungefragt umgeschrieben; die alten Keys verschwinden erst, wenn der
    # Nutzer im LLM-Tab speichert (PUT /api/llm/config).
    LLM_DOCUMENT: dict = llm_config.empty_document()
    # True, sobald llm.json existiert — d. h. die flachen Env-Keys sind nur
    # noch Altlast (Lese-Migration), nicht mehr die Quelle.
    LLM_DOCUMENT_STORED: bool = False

    @classmethod
    def llm_endpoint(cls, tier: str) -> dict | None:
        """Der aufgeloeste Endpunkt einer Rolle (``reasoning`` / ``fast`` /
        ``embedding``): ``{base_url, chat_url, models_url, embed_url, api_key,
        model, connection_id, provider}`` — oder ``None``, wenn die Rolle nicht
        nutzbar ist (keine Verbindung gebunden, kein Modell, keine URL).
        ``fast`` faellt auf ``reasoning`` zurueck, ``embedding`` nie. Der Key
        darf leer sein (Ollama & Co.)."""
        return llm_config.resolve_role(cls.LLM_DOCUMENT, tier, cls.LLM_PROVIDERS)

    @classmethod
    def llm_ready(cls, tier: str) -> bool:
        """Feature-Gate pro Rolle: gebunden an eine Verbindung mit URL und
        Modell. Ersetzt das alte ``if Config.KICONNECT_API_KEY`` — ein leerer
        Key ist kein Grund, ein laufendes Ollama fuer "kein LLM" zu halten,
        und die Embedding-Rolle prueft die Embedding-Bindung, nicht die des
        Chats."""
        return cls.llm_endpoint(tier) is not None

    @classmethod
    def _role_model(cls, tier: str) -> str:
        ep = cls.llm_endpoint(tier)
        return ep["model"] if ep else ""

    @classmethod
    def reasoning_model(cls) -> str:
        """Modell der Reasoning-Rolle (Chat, Analyse); leer = nicht gebunden."""
        return cls._role_model("reasoning")

    @classmethod
    def fast_model(cls) -> str:
        """Modell der Fast-Rolle (Extraktion, Kategorisierung, Metadaten).
        Faellt auf die Reasoning-Rolle zurueck, wenn nicht gebunden."""
        return cls._role_model("fast")

    @classmethod
    def embed_model(cls) -> str:
        """Modell der Embedding-Rolle; leer = semantische Features aus."""
        return cls._role_model("embedding")

    @classmethod
    def llm_status(cls) -> dict:
        """Was die SPA ausserhalb der Settings wissen muss: Bereitschaft pro
        Rolle + Anzahl Verbindungen (die Frage des Onboardings)."""
        out = {tier: cls.llm_ready(tier) for tier in llm_config.TIERS}
        out["connections"] = len(cls.LLM_DOCUMENT.get("connections", []))
        return out

    @classmethod
    def reload_from_env(cls):
        """Liest alle zur Laufzeit aenderbaren Settings aus der Env — beim
        Import UND nach jedem Settings-Save (PUT /api/settings). Die einzige
        Stelle, an der die Env-Defaults stehen (frueher drifteten config.py
        und routers/settings.py auseinander, z. B. beim LLM_MODEL-Default)."""
        cls._load_llm_document()
        cls.load_plugins_document()
        # Leer per Default: die hoefliche mailto-Kennung ist die Adresse DES
        # NUTZERS (Onboarding, #140) — nie eine mitgelieferte fremde Adresse.
        cls.CROSSREF_MAILTO = os.getenv("CROSSREF_MAILTO", "").strip()
        cls.OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "")
        # Oberflaechensprache (ADR-0018). Englisch ist die Quellsprache und
        # der Default; Deutsch ist ein Katalog. Unbekannte Werte fallen auf
        # "en" zurueck, damit ein vertippter .env-Eintrag kein Key-Salat wird.
        _lang = os.getenv("UI_LANGUAGE", "en").strip().lower()
        cls.UI_LANGUAGE = _lang if _lang in cls.UI_LANGUAGES else "en"
        cls.WATCH_INTERVAL = _env_int("WATCH_INTERVAL", 5, minimum=1)
        cls.MAX_OCR_PAGES = _env_int("MAX_OCR_PAGES", 5, minimum=1)
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
    @classmethod
    def _load_llm_document(cls):
        """llm.json laden — oder, solange es keins gibt, das Dokument aus den
        flachen Env-Keys einer alten .env synthetisieren (Lese-Migration:
        eine Verbindung "default", nichts wird geschrieben). Eine kaputte
        Datei nimmt die App nicht mit: sie laeuft dann ohne LLM weiter."""
        cls.LLM_CONFIG_PATH = os.getenv("LLM_CONFIG_PATH", "").strip() or os.path.join(
            os.path.dirname(cls.ENV_PATH), "llm.json"
        )
        doc = llm_config.load(cls.LLM_CONFIG_PATH)
        cls.LLM_DOCUMENT_STORED = doc is not None
        if doc is not None:
            try:
                doc = llm_config.normalize(doc, cls.LLM_PROVIDERS)
            except llm_config.DocumentError as exc:
                logging.warning("llm.json unbrauchbar (%s) — LLM-Konfiguration leer", exc.code)
                doc = llm_config.empty_document()
            cls.LLM_DOCUMENT = doc
            return
        doc = llm_config.from_legacy_env(
            provider=os.getenv("LLM_PROVIDER", ""),
            base_url=os.getenv("LLM_BASE_URL", ""),
            api_key=os.getenv("LLM_API_KEY", "") or os.getenv("KICONNECT_API_KEY", ""),
            model=os.getenv("LLM_MODEL", ""),
            model_fast=os.getenv("LLM_MODEL_FAST", ""),
            embed_model=os.getenv("LLM_EMBED_MODEL", ""),
            embed_url=os.getenv("LLM_EMBED_URL", ""),
            providers=cls.LLM_PROVIDERS,
        )
        cls.LLM_DOCUMENT = doc if doc is not None else llm_config.empty_document()

    # -------------------------------------------------------------------
    # Add-ons (plugins.json, ADR-0021): Zustandsdokument + Ladeorte
    # -------------------------------------------------------------------
    PLUGINS_CONFIG_PATH: str = ""   # von load_plugins_document gesetzt
    PLUGINS_DOCUMENT: dict = plugins_config.empty_document()
    PLUGINS_DOCUMENT_STORED: bool = False
    # Wurzel der installierten Bundles (<root>/<id>/<version>/) und die
    # Dev-Suchpfade aus der Umgebung; die aus plugins.json kommen dazu.
    PLUGIN_DIR: str = ""
    PLUGIN_DEV_PATHS: tuple = ()
    # Marketplace-Index-Cache (ADR-0021, issue #189): Zeitstempel-Datei plus
    # gecachte Bilder, damit der Marketplace offline den zuletzt geholten
    # Stand zeigt. Neben PLUGIN_DIR, gleiches Muster.
    MARKETPLACE_CACHE_DIR: str = ""

    @staticmethod
    def default_plugin_dir() -> str:
        """``%LOCALAPPDATA%/LocalBib/plugins`` unter Windows, sonst der XDG-Datenordner."""
        if os.name == "nt":
            base = os.getenv("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
            return os.path.join(base, "LocalBib", "plugins")
        base = os.getenv("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        return os.path.join(base, "localbib", "plugins")

    @classmethod
    def load_plugins_document(cls):
        """plugins.json laden — oder, solange es keins gibt, die Alt-Schluessel
        der .env lesend uebernehmen (wie llm.json, ADR-0019). Ist das Dokument
        gespeichert, ist es die Wahrheit; Add-ons lesen ihre Werte nur ueber
        ``SettingsApi``, nie aus der Prozess-Umgebung. Aufgerufen von
        ``reload_from_env`` und nach jedem Speichern des Dokuments."""
        cls.PLUGINS_CONFIG_PATH = os.getenv("PLUGINS_CONFIG_PATH", "").strip() or os.path.join(
            os.path.dirname(cls.ENV_PATH), "plugins.json"
        )
        doc = plugins_config.load(cls.PLUGINS_CONFIG_PATH)
        cls.PLUGINS_DOCUMENT_STORED = doc is not None
        if doc is not None:
            cls.PLUGINS_DOCUMENT = plugins_config.normalize(doc)
        else:
            cls.PLUGINS_DOCUMENT = (plugins_config.from_legacy_env(os.environ)
                                    or plugins_config.empty_document())
        cls.PLUGIN_DIR = os.getenv("LOCALBIB_PLUGIN_DIR", "").strip() or cls.default_plugin_dir()
        cls.PLUGIN_DEV_PATHS = tuple(
            p.strip() for p in os.getenv("LOCALBIB_PLUGIN_DEV_PATHS", "").split(os.pathsep) if p.strip()
        )
        cls.MARKETPLACE_CACHE_DIR = os.getenv("LOCALBIB_MARKETPLACE_CACHE_DIR", "").strip() or os.path.join(
            os.path.dirname(cls.PLUGIN_DIR), "marketplace"
        )

    @classmethod
    def plugins_document(cls) -> dict:
        """Eine Arbeitskopie des aktuellen Dokuments. Ungespeichert wird die
        Lese-Migration frisch aus der Umgebung gebaut, damit ein erster
        Schreibvorgang den aktuellen Stand festhaelt, nicht den vom Start."""
        if cls.PLUGINS_DOCUMENT_STORED:
            return plugins_config.normalize(cls.PLUGINS_DOCUMENT)
        return plugins_config.from_legacy_env(os.environ) or plugins_config.empty_document()

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

    # Oberflaechensprache (von reload_from_env gesetzt, ADR-0018)
    UI_LANGUAGES = ("en", "de")
    UI_LANGUAGE = "en"
    UI_LANGUAGE_NAMES = {"en": "English", "de": "German"}

    @classmethod
    def ui_language_name(cls) -> str:
        """Der Sprachname fuer LLM-Prompts ("English" / "German").

        LLM-Ausgaben folgen der Oberflaechensprache ab dem Zeitpunkt der
        Generierung (ADR-0018). Bereits erzeugte Texte bleiben, wie sie sind -
        es gibt kein nachtraegliches Uebersetzen.
        """
        return cls.UI_LANGUAGE_NAMES.get(cls.UI_LANGUAGE, "English")

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
