// =============================================================================
// Literatur-Manager Web UI - Vue 3 SPA
// =============================================================================

const { createApp, ref, reactive, computed, watch, onMounted, nextTick, markRaw } = Vue;
const { createRouter, createWebHashHistory } = VueRouter;

// =============================================================================
// i18n (ADR-0018)
// -----------------------------------------------------------------------------
// Englisch ist die Quellsprache, Deutsch ein Katalog. Die Kataloge kommen als
// Script-Globals (`window.LB_I18N`) aus `static/locales/`, die Startsprache
// serverseitig gerendert in `window.LB_LANG` — beides, damit der erste Frame
// schon die richtige Sprache traegt und nie ein roher Key-Pfad aufblitzt.
//
// Schluessel sind flach und gepunktet (`papers.detail.notes.save`). Flach, weil
// das den Paritaetstest zu einem Mengenvergleich macht, die Aufloesung zu einem
// Objektzugriff, und weil man jeden Text mit einem grep zu seiner Stelle
// zurueckverfolgen kann.
// =============================================================================

const UI_LANGS = ['en', 'de'];
const uiLang = ref(UI_LANGS.includes(window.LB_LANG) ? window.LB_LANG : 'en');

// Ein fehlender Schluessel ist SICHTBAR kaputt und faellt nicht still auf die
// andere Sprache zurueck: ein deutscher Rest im englischen UI ist genau der
// Fehler, den niemand meldet. Das eigentliche Netz ist der Paritaetstest.
function t(key, vars) {
    const catalog = (window.LB_I18N || {})[uiLang.value] || {};
    let raw = catalog[key];
    // Add-on-Kataloge (#187) liegen unter ihrem Namensraum `<id>.`; der Kern
    // hat Vorrang, ein Add-on kann keinen Kerntext ueberschreiben.
    if (typeof raw !== 'string') raw = addonText(key);
    if (typeof raw !== 'string') return '⟦' + key + '⟧';
    if (!vars) return raw;
    return raw.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m));
}

// Add-on-Zustand (#187, ADR-0021). Hier oben deklariert, weil `t()` ihn liest
// und `t()` schon waehrend der Auswertung dieser Datei laufen kann; die
// Lade- und Registrierungslogik steht unten vor dem Router.
// `addonTick` ist das eine reaktive Signal: jede Aenderung an einem Add-on
// (geladen, registriert, an, aus) zaehlt es hoch, und alles, was Add-on-Texte,
// -Views oder -Slots liest, rendert dadurch neu.
const addonTick = ref(0);
const _addons = {};   // id -> { entry, state, error, registration, catalogs }

// Text eines aktiven Add-ons fuer `key`. Die UI-Sprache, wenn das Add-on sie
// mitbringt, sonst seine `default_language`; fehlt der Schluessel innerhalb
// der gewaehlten Sprache, bleibt es bei der Kernregel (sichtbarer Pfad).
function addonText(key) {
    addonTick.value;  // Abhaengigkeit fuer den Render
    const dot = key.indexOf('.');
    const a = dot > 0 ? _addons[key.slice(0, dot)] : null;
    if (!a || a.state !== 'active') return undefined;
    const lang = a.catalogs[uiLang.value] ? uiLang.value : a.entry.default_language;
    const text = (a.catalogs[lang] || {})[key];
    return typeof text === 'string' ? text : undefined;
}

// Zwei Formen reichen fuer Englisch wie Deutsch; `{n}` steht im Text bereit.
function tn(key, n, vars) {
    return t(key + (n === 1 ? '.one' : '.other'), Object.assign({ n }, vars || {}));
}

// Datums- und Zahlformate haengen an derselben Wahl wie die Texte. Eine
// englische Oberflaeche mit deutschen Formaten waere ein sichtbarer Fehler,
// deshalb bleibt keine `toLocale*`-Stelle ohne explizite Locale.
function uiLocale() {
    return uiLang.value === 'de' ? 'de-DE' : 'en-US';
}

// Vorschlag fuer den First Run. `navigator.languages` kann "de-AT" oder
// "en-GB" liefern - uns interessiert nur der Teil vor dem Bindestrich, und
// alles Unbekannte landet bei der Quellsprache.
function browserLanguage() {
    const wanted = (navigator.languages && navigator.languages.length
        ? navigator.languages : [navigator.language || '']);
    for (const tag of wanted) {
        const base = String(tag).toLowerCase().split('-')[0];
        if (UI_LANGS.includes(base)) return base;
    }
    return 'en';
}

// Live, ohne Neustart: beide Kataloge liegen schon im Speicher.
function setUiLang(lang) {
    if (!UI_LANGS.includes(lang)) return;
    uiLang.value = lang;
    document.documentElement.lang = lang;
}

function hasKey(key) {
    const catalog = (window.LB_I18N || {}).en || {};
    return typeof catalog[key] === 'string';
}

// Meldungen vom Server sind Codes, keine Saetze (ADR-0018, Entscheidung 3) —
// als String (`error.item_not_found`) oder als Objekt mit Parametern
// (`{code, params}`). Ob uebersetzt wird, entscheidet der KATALOG und kein
// Praefix: was er nicht kennt, ist ein fertiger Satz und bleibt unveraendert.
// Genau davon leben die Plugin-Router, die weiterhin deutsch antworten.
function translateDetail(detail, status) {
    if (detail && typeof detail === 'object' && typeof detail.code === 'string') {
        return t(detail.code, detail.params || {});
    }
    if (typeof detail === 'string' && detail) {
        return hasKey(detail) ? t(detail) : detail;
    }
    return t('error.http', { status: status });
}

// Der Kauf-Link kommt vom Server (GET /api/license/status, ADR-0015) - hier
// steht bewusst keine zweite Kopie der Checkout-URL, die beim naechsten
// Store-Umzug vergessen wuerde.

// =============================================================================
// Dark Mode - data-theme attribute (tokens live in style.css)
// =============================================================================

function applyDarkMode(enabled) {
    document.documentElement.setAttribute('data-theme', enabled ? 'dark' : 'light');
}

// Sofort anwenden (vor Vue-Mount), damit kein Flash of Light Mode
applyDarkMode(localStorage.getItem('darkMode') === 'true');

// =============================================================================
// API Helper
// =============================================================================

async function api(url, options = {}) {
    const resp = await fetch(url, {
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(translateDetail(err.detail, resp.status));
    }
    return resp.json();
}

// Every streaming endpoint (smart import, migration commit, bulk reference
// extraction) speaks the same dialect: `data: {json}` lines separated by blank
// lines. One reader, so a cancelled or truncated stream is handled once.
async function readSseStream(resp, onEvent) {
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            try { onEvent(JSON.parse(line.slice(6))); } catch (e) { /* skip */ }
        }
    }
}

// Web-Suche-Fallback fuer Referenzen ohne DOI: Google-Scholar-Query
// aus Titel + Erstautor-Nachname + Jahr
function scholarSearchUrl(ref) {
    const parts = [(ref.title || '').trim()];
    const firstAuthor = (ref.authors || '').split(';')[0].split(',')[0].trim();
    if (firstAuthor) parts.push(firstAuthor);
    if (ref.year) parts.push(String(ref.year));
    return 'https://scholar.google.com/scholar?q=' + encodeURIComponent(parts.filter(Boolean).join(' '));
}

// Markdown -> HTML fuer v-html (Notizen, Research-Chat). `marked` und
// `DOMPurify` kommen wie jede andere Bibliothek der Seite vom CDN. Fehlt eine
// von beiden, greift der Minimal-Renderer: er escaped den Text zuerst und
// bringt danach nur die drei Inline-Formen zurueck, die er selbst erzeugt hat.
// In beiden Pfaden kann also nichts, was der Nutzer getippt hat, zu lebendem
// Markup werden — bei gespeichertem Text ist Sanitizing nicht optional.
function renderMarkdownSafe(text) {
    if (!text) return '';
    if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
        return DOMPurify.sanitize(marked.parse(String(text), { breaks: true }));
    }
    return String(text)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/`(.+?)`/g, '<code class="lb-md-code">$1</code>');
}

// Favicon: dieselben Vorgaben wie im <head> von index.html. Seit das
// LocalBib-Icon mitgeliefert wird, ist der Tab nie leer — ein eigenes Icon
// (Einstellungen > Darstellung) ersetzt es, das Entfernen stellt es wieder her.
// Darum hier ersetzen statt, wie frueher, den Link ersatzlos loeschen.
const DEFAULT_FAVICONS = [
    { href: '/static/icons/localbib-32.png', sizes: '32x32' },
    { href: '/static/icons/localbib.png', sizes: '256x256' },
];

function setFavicon(href) {
    document.querySelectorAll("link[rel~='icon']").forEach(el => el.remove());
    for (const entry of (href ? [{ href }] : DEFAULT_FAVICONS)) {
        const link = document.createElement('link');
        link.rel = 'icon';
        link.href = entry.href;
        if (entry.sizes) link.sizes = entry.sizes;
        document.head.appendChild(link);
    }
}

// =============================================================================
// SVG Icons (inline)
// =============================================================================

const icons = {
    papers: `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>`,
    settings: `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
    folder: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`,
    chevronRight: `<svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>`,
    chevronDown: `<svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>`,
    search: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
    x: `<svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
    back: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>`,
    pdf: `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`,
    download: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`,
    plus: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`,
    trash: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>`,
    edit: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`,
    refresh: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>`,
    upload: `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`,
    power: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>`,
    sparkle: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l2.4 7.2L22 12l-7.6 2.8L12 22l-2.4-7.2L2 12l7.6-2.8z"/></svg>`,
    check: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
    fileExport: `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/><polyline points="9 15 12 12 15 15"/></svg>`,
    moon: `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`,
    sun: `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`,
    image: `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>`,
    project: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>`,
    filter: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>`,
    columns: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="12" y1="3" x2="12" y2="21"/></svg>`,
    note: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>`,
    network: `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="3"/><circle cx="5" cy="19" r="3"/><circle cx="19" cy="19" r="3"/><line x1="12" y1="8" x2="5" y2="16"/><line x1="12" y1="8" x2="19" y2="16"/></svg>`,
    book: `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/><line x1="12" y1="6" x2="16" y2="6"/><line x1="12" y1="10" x2="16" y2="10"/><line x1="12" y1="14" x2="16" y2="14"/></svg>`,
    sortAsc: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>`,
    sortDesc: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>`,
    marketplace: `<svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 7v13a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7l-3-5Z"/><path d="M3 7h18"/><path d="M16 11a4 4 0 0 1-8 0"/></svg>`,
    puzzle: `<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19.439 7.85c-.049.322.059.648.289.878l1.568 1.568c.47.47.706 1.087.706 1.704s-.235 1.233-.706 1.704l-1.611 1.611a.98.98 0 0 1-.837.276c-.47-.07-.802-.48-.968-.925a2.501 2.501 0 1 0-3.214 3.214c.446.166.855.497.925.968a.979.979 0 0 1-.276.837l-1.61 1.61a2.404 2.404 0 0 1-1.705.707 2.402 2.402 0 0 1-1.704-.706l-1.568-1.568a1.026 1.026 0 0 0-.877-.29c-.493.074-.84.504-1.02.968a2.5 2.5 0 1 1-3.237-3.237c.464-.18.894-.527.967-1.02a1.026 1.026 0 0 0-.289-.877l-1.568-1.568A2.402 2.402 0 0 1 1.998 12c0-.617.236-1.234.706-1.704L4.23 8.77c.24-.24.581-.353.917-.303.515.077.877.528 1.073 1.01a2.5 2.5 0 1 0 3.259-3.259c-.482-.196-.933-.558-1.01-1.073-.05-.336.062-.676.303-.917l1.525-1.525A2.402 2.402 0 0 1 12 1.998c.617 0 1.234.236 1.704.706l1.568 1.568c.23.23.556.338.877.29.493-.074.84-.504 1.02-.968a2.5 2.5 0 1 1 3.237 3.237c-.464.18-.894.527-.967 1.02Z"/></svg>`,
};


// =============================================================================
// CategoryNode Component (rekursiv) — editorial .lb-cat-row style
// =============================================================================

const CategoryNode = {
    name: 'CategoryNode',
    props: {
        node: { type: Object, required: true },
        editMode: { type: Boolean, default: false },
        depth: { type: Number, default: 0 },
    },
    emits: ['edit', 'delete'],
    template: `
        <button class="lb-cat-row"
                :class="{ 'is-active': isActive }"
                :style="{ paddingLeft: (10 + depth * 14) + 'px' }"
                @click="navigate">
            <span v-if="depth > 0" class="lb-cat-tick" aria-hidden="true">·</span>
            <span class="lb-cat-name">{{ node.name }}</span>
            <span v-if="!editMode && node.paper_count"
                  class="lb-cat-count">{{ node.paper_count }}</span>
            <template v-if="editMode">
                <span class="lb-cat-count" style="display:inline-flex; gap:4px;">
                    <span @click.stop="$emit('edit', node)" :title="$t('common.edit')"
                          style="cursor:pointer" v-html="icons.edit"></span>
                    <span @click.stop="$emit('delete', node)" :title="$t('common.delete')"
                          style="cursor:pointer" v-html="icons.trash"></span>
                </span>
            </template>
        </button>
        <category-node v-for="child in node.children || []" :key="child.id"
                       :node="child"
                       :edit-mode="editMode"
                       :depth="depth + 1"
                       @edit="$emit('edit', $event)"
                       @delete="$emit('delete', $event)" />
    `,
    data() {
        return { icons };
    },
    computed: {
        isActive() {
            return this.$route.name === 'category' &&
                parseInt(this.$route.params.id) === this.node.id;
        }
    },
    methods: {
        navigate() {
            if (!this.editMode) {
                this.$router.push({ name: 'category', params: { id: this.node.id } });
            }
        }
    }
};


// =============================================================================
// Sidebar Component
// =============================================================================

// Anbieter-Liste fuers Onboarding, falls /api/llm/providers nicht antwortet.
// Die massgebliche Liste ist Config.LLM_PROVIDERS (config.py).
const ONBOARDING_FALLBACK_PROVIDERS = [
    { id: 'openai', label: 'OpenAI' },
    { id: 'openrouter', label: t('onboarding.provider.openrouter') },
    { id: 'groq', label: 'Groq' },
    { id: 'custom', label: t('onboarding.provider.custom') },
];

// The offer on the second onboarding page (#178). The ids are exactly what the
// wizard reads from `?source=` — three named managers plus "any other file",
// which lands on the BibTeX card. Availability is the wizard's business: a
// card without an adapter (Citavi) shows there as "coming soon" rather than
// being hidden here, because the question is where the user comes FROM.
const ONBOARDING_MIGRATION_SOURCES = [
    { id: 'zotero_rdf', labelKey: 'migrate.source.zotero.label' },
    { id: 'citavi', labelKey: 'migrate.source.citavi.label' },
    { id: 'mendeley', labelKey: 'migrate.source.mendeley.label' },
    { id: 'bibtex', labelKey: 'migrate.onboarding.otherFile' },
];

const Sidebar = {
    components: { CategoryNode },
    props: {
        licenseActivated: { type: Boolean, default: false },
    },
    template: `
        <aside class="lb-sidebar" :class="{ 'is-collapsed': collapsed }">
            <!-- Brand -->
            <div class="lb-brand">
                <div class="lb-logo">
                    <img v-if="customIcon" :src="customIcon" style="width:24px;height:24px;border-radius:4px" />
                    <span v-else class="lb-logo-mark" aria-hidden="true">[</span>
                    <span class="lb-logo-name">LocalBib</span>
                </div>
                <button class="lb-collapse" @click="toggleCollapsed"
                        :aria-expanded="String(!collapsed)"
                        :title="collapsed ? $t('sidebar.expand') : $t('sidebar.collapse')">
                    <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m11 17-5-5 5-5"/><path d="m18 17-5-5 5-5"/></svg>
                </button>
            </div>

            <!-- Navigation -->
            <nav class="lb-nav">
                <router-link to="/" class="lb-nav-item" exact-active-class="is-active"
                             :title="collapsed ? $t('nav.items') : null">
                    <span class="lb-nav-icon" v-html="icons.papers"></span>
                    <span class="lb-nav-label">{{ $t('nav.items') }}</span>
                </router-link>
                <router-link to="/import" class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? $t('nav.import') : null">
                    <span class="lb-nav-icon" v-html="icons.upload"></span>
                    <span class="lb-nav-label">{{ $t('nav.import') }}</span>
                    <span v-if="stats.pending_imports" class="lb-nav-badge">{{ stats.pending_imports }}</span>
                </router-link>
                <router-link to="/migrate" class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? $t('nav.migrate') : null">
                    <span class="lb-nav-icon" v-html="icons.fileExport"></span>
                    <span class="lb-nav-label">{{ $t('nav.migrate') }}</span>
                </router-link>
                <router-link to="/analyse" class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? $t('nav.analysis') : null">
                    <span class="lb-nav-icon" v-html="icons.network"></span>
                    <span class="lb-nav-label">{{ $t('nav.analysis') }}</span>
                </router-link>
                <router-link to="/research-chat" class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? $t('nav.researchChat') : null">
                    <span class="lb-nav-icon"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></span>
                    <span class="lb-nav-label">{{ $t('nav.researchChat') }}</span>
                </router-link>
                <router-link v-if="thesisMode" to="/thesis" class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? $t('nav.thesis') : null">
                    <span class="lb-nav-icon"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg></span>
                    <span class="lb-nav-label">{{ $t('nav.thesis') }}</span>
                </router-link>
                <!-- Plugin-NavItems (Phase 0b): vom Server via /api/plugins/nav geliefert -->
                <router-link v-for="item in visiblePluginNavItems" :key="item.id" :to="item.route"
                             class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? navItemLabel(item) : null">
                    <span class="lb-nav-icon" v-html="item.icon"></span>
                    <span class="lb-nav-label">{{ navItemLabel(item) }}</span>
                </router-link>
                <!-- Marketplace (#189, ADR-0021): own sidebar entry, directly before Settings. -->
                <router-link to="/marketplace" class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? $t('nav.marketplace') : null">
                    <span class="lb-nav-icon" v-html="icons.marketplace"></span>
                    <span class="lb-nav-label">{{ $t('nav.marketplace') }}</span>
                </router-link>
                <router-link to="/settings" class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? $t('nav.settings') : null">
                    <span class="lb-nav-icon" v-html="icons.settings"></span>
                    <span class="lb-nav-label">{{ $t('nav.settings') }}</span>
                </router-link>
            </nav>

            <!-- Kategorien -->
            <div class="lb-section">
                <span class="lb-section-label">{{ $t('sidebar.categories') }}</span>
                <span style="display:inline-flex; gap:4px">
                    <button class="lb-section-add" @click="showAddCategoryModal = true" :title="$t('categories.new')">+</button>
                    <button class="lb-section-add" @click="$router.push({ name: 'category-planner' })"
                            :title="$t('categories.plannerOpen')" style="font-size:11px">⌥</button>
                </span>
            </div>
            <div class="lb-list lb-cats">
                <div v-if="loading" style="display:flex; justify-content:center; padding:16px">
                    <div class="spinner"></div>
                </div>
                <div v-else-if="tree.length === 0" style="padding:6px 10px; font-size:12.5px; color:var(--lb-mute)">
                    {{ $t('categories.empty') }}
                </div>
                <category-node v-for="root in tree" :key="root.id" :node="root"
                               :edit-mode="categoryEditMode"
                               @edit="startEditCategory"
                               @delete="deleteCategory" />
            </div>

            <!-- Footer -->
            <div class="lb-foot">
                <div class="lb-foot-stats">
                    <span>{{ $tn('common.itemCount', stats.paper_count || 0) }}</span>
                    <span>{{ $tn('common.categoryCount', stats.category_count || 0) }}</span>
                </div>
                <div class="lb-foot-row">
                    <button class="lb-foot-btn" @click="toggleTheme"
                            :title="darkMode ? $t('theme.toLight') : $t('theme.toDark')">
                        <span v-html="darkMode ? icons.sun : icons.moon" style="width:14px;height:14px"></span>
                    </button>
                    <button class="lb-foot-btn" @click="shutdownServer" :title="$t('app.shutdown')">
                        <span v-html="icons.power" style="width:14px;height:14px"></span>
                    </button>
                </div>
            </div>

            <!-- Add Category Modal -->
            <div v-if="showAddCategoryModal" class="fixed inset-0 bg-black/30 flex items-center justify-center z-50"
                 @click.self="showAddCategoryModal = false">
                <div class="bg-white rounded-xl shadow-xl p-6 w-full max-w-md">
                    <h4 class="text-base font-semibold text-gray-900 mb-4">{{ $t('categories.new') }}</h4>
                    <div class="space-y-3">
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">{{ $t('common.name') }}</label>
                            <input v-model="newCat.name" :placeholder="$t('categories.namePlaceholder')"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">{{ $t('categories.parent') }}</label>
                            <select v-model="newCat.parent_id"
                                    class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-accent focus:border-accent outline-none">
                                <option :value="null">{{ $t('categories.parentNone') }}</option>
                                <option v-for="c in flatCategories" :key="c.id" :value="c.id">{{ c.name }}</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">{{ $t('common.description') }}</label>
                            <input v-model="newCat.description" :placeholder="$t('categories.descriptionPlaceholder')"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">{{ $t('common.keywords') }}</label>
                            <input v-model="newCat.keywords" :placeholder="$t('categories.keywordsPlaceholder')"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                    </div>
                    <div class="flex items-center gap-2 mt-5">
                        <button @click="llmSuggestCategory" :disabled="!newCat.name || llmLoading"
                                class="inline-flex items-center gap-1.5 bg-accent-soft border border-accent-soft text-accent-ink px-3 py-2 rounded-lg text-sm font-medium hover:bg-accent-soft disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            <span v-html="icons.sparkle"></span>
                            <span v-if="llmLoading">{{ $t('categories.llmThinking') }}</span>
                            <span v-else>{{ $t('categories.llmSuggest') }}</span>
                        </button>
                        <div class="flex-1"></div>
                        <button @click="showAddCategoryModal = false"
                                class="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors">
                            {{ $t('common.cancel') }}
                        </button>
                        <button @click="createCategory" :disabled="!newCat.name"
                                class="bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            {{ $t('common.create') }}
                        </button>
                    </div>
                </div>
            </div>

            <!-- Edit Category Modal -->
            <div v-if="editCat" class="fixed inset-0 bg-black/30 flex items-center justify-center z-50"
                 @click.self="editCat = null">
                <div class="bg-white rounded-xl shadow-xl p-6 w-full max-w-md">
                    <h4 class="text-base font-semibold text-gray-900 mb-4">{{ $t('categories.edit') }}</h4>
                    <div class="space-y-3">
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">{{ $t('common.name') }}</label>
                            <input v-model="editCat.name"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">{{ $t('common.description') }}</label>
                            <input v-model="editCat.description"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">{{ $t('common.keywords') }}</label>
                            <input v-model="editCat.keywords"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                    </div>
                    <div class="flex justify-end gap-2 mt-5">
                        <button @click="editCat = null"
                                class="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors">
                            {{ $t('common.cancel') }}
                        </button>
                        <button @click="saveCategory"
                                class="bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                            {{ $t('common.save') }}
                        </button>
                    </div>
                </div>
            </div>

        </aside>
    `,
    data() {
        return {
            tree: [],
            flatCategories: [],
            stats: {},
            loading: true,
            icons,
            categoryEditMode: false,
            showAddCategoryModal: false,
            newCat: { name: '', parent_id: null, description: '', keywords: '' },
            editCat: null,
            llmLoading: false,
            customIcon: null,
            thesisMode: localStorage.getItem('thesisMode') === 'true',
            pluginNavItems: [],
            darkMode: localStorage.getItem('darkMode') === 'true',
            // Collapsed = icon rail (64px). Persisted so a focus-mode choice
            // (e.g. while working in a wide plugin view) survives reloads.
            collapsed: localStorage.getItem('lbSidebarCollapsed') === '1',
            // True while the fold was done *for* the reader (focus mode, #161)
            // rather than *by* them. Only such a fold is undone again.
            autoCollapsed: false,
        };
    },
    async created() {
        await this.load();
    },
    mounted() {
        // Focus mode (#161): the paper detail view asks for the icon rail while
        // it is open. Folding here never writes lbSidebarCollapsed — a reader
        // must not find their sidebar permanently folded by opening a paper.
        this._focusModeHandler = (e) => {
            if (e.detail && e.detail.on) {
                this.autoCollapsed = !this.collapsed;
                this.collapsed = true;
            } else {
                if (this.autoCollapsed) this.collapsed = false;
                this.autoCollapsed = false;
            }
        };
        window.addEventListener('lb-focus-mode', this._focusModeHandler);
    },
    unmounted() {
        if (this._focusModeHandler) window.removeEventListener('lb-focus-mode', this._focusModeHandler);
    },
    computed: {
        // Add-on items only with a registered view behind them (#187): an
        // Add-on whose script failed keeps its server nav item but no page.
        visiblePluginNavItems() {
            return this.pluginNavItems.filter(navItemVisible);
        },
    },
    methods: {
        navItemLabel,
        toggleCollapsed() {
            this.collapsed = !this.collapsed;
            // A deliberate unfold ends the automatism: the choice stands, and
            // closing the paper restores nothing over it.
            this.autoCollapsed = false;
            try {
                localStorage.setItem('lbSidebarCollapsed', this.collapsed ? '1' : '0');
            } catch (e) { /* private mode: the toggle still works, it just won't persist */ }
        },
        async load() {
            this.loading = true;
            this.thesisMode = localStorage.getItem('thesisMode') === 'true';
            try {
                const [tree, stats, categories] = await Promise.all([
                    api('/api/categories/tree'),
                    api('/api/stats'),
                    api('/api/categories'),
                ]);
                this.tree = tree;
                this.stats = stats;
                this.flatCategories = categories;
            } catch (e) {
                console.error('Sidebar load error:', e);
            }
            this.loading = false;
            // Custom Icon laden. Auch der Tab bekommt es hier — sonst zeigte er
            // nach einem Reload wieder das mitgelieferte LocalBib-Icon.
            try {
                const iconData = await api('/api/appearance/icon');
                this.customIcon = iconData.path;
                if (iconData.path) setFavicon(iconData.path);
            } catch (e) {}
            // Add-on nav items (ADR-0021): what the loaded Bundles registered.
            // Their routes come from the Add-on registrations (applyAddons).
            try {
                const data = await api('/api/plugins/nav');
                this.pluginNavItems = data.items || [];
            } catch (e) {
                this.pluginNavItems = [];
            }
        },
        async createCategory() {
            if (!this.newCat.name) return;
            try {
                await api('/api/categories', {
                    method: 'POST',
                    body: JSON.stringify(this.newCat),
                });
                this.newCat = { name: '', parent_id: null, description: '', keywords: '' };
                this.showAddCategoryModal = false;
                await this.load();
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
        },
        startEditCategory(node) {
            this.editCat = { id: node.id, name: node.name, description: node.description || '', keywords: node.keywords || '' };
        },
        async saveCategory() {
            if (!this.editCat) return;
            try {
                await api(`/api/categories/${this.editCat.id}`, {
                    method: 'PUT',
                    body: JSON.stringify({
                        name: this.editCat.name,
                        description: this.editCat.description,
                        keywords: this.editCat.keywords,
                    }),
                });
                this.editCat = null;
                await this.load();
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
        },
        async deleteCategory(node) {
            if (!confirm(`Kategorie "${node.name}" wirklich loeschen? Unterkategorien werden ebenfalls entfernt.`)) return;
            try {
                await api(`/api/categories/${node.id}`, { method: 'DELETE' });
                await this.load();
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
        },
        async llmSuggestCategory() {
            if (!this.newCat.name) return;
            this.llmLoading = true;
            try {
                const suggestion = await api('/api/categories/suggest', {
                    method: 'POST',
                    body: JSON.stringify({ name: this.newCat.name }),
                });
                if (suggestion.description) this.newCat.description = suggestion.description;
                if (suggestion.keywords) this.newCat.keywords = suggestion.keywords;
            } catch (e) {
                alert(t('categories.llmSuggestFailed', { message: e.message }));
            }
            this.llmLoading = false;
        },
        toggleTheme() {
            this.darkMode = !this.darkMode;
            localStorage.setItem('darkMode', String(this.darkMode));
            applyDarkMode(this.darkMode);
        },
        async shutdownServer() {
            if (!confirm(t('app.shutdownConfirm'))) return;
            try {
                await api('/api/server/shutdown', { method: 'POST' });
                document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif;color:#666"><div style="text-align:center"><h2>'
                    + t('app.shutdownDone') + '</h2><p>' + t('app.shutdownCloseWindow') + '</p></div></div>';
            } catch (e) {
                // Expected - server is shutting down
            }
        }
    }
};


// =============================================================================
// PaperList Component
// =============================================================================

const PaperList = {
    template: `
        <div class="lb-view lb-papers">
            <!-- Page Head -->
            <header class="lb-page-head">
                <div class="lb-page-head-l">
                    <div class="lb-eyebrow">
                        <span v-if="heading.color" class="lb-eyebrow-dot" :style="{ background: heading.color }"></span>
                        {{ heading.eyebrow }}
                    </div>
                    <h1 class="lb-page-title">{{ heading.title }}</h1>
                    <div class="lb-page-meta">
                        {{ $tn('common.itemCount', resultCount) }}
                        <template v-if="searchQuery">
                            <span class="lb-page-meta-sep">&middot;</span>
                            <span>{{ $t('search.forQuery', { query: searchQuery }) }}</span>
                        </template>
                        <template v-if="semanticMode && semanticMeta.mode && semanticMeta.mode !== 'semantic'">
                            <span class="lb-page-meta-sep">&middot;</span>
                            <span>{{ semanticMeta.mode === 'hybrid' ? $t('search.modeHybrid') : $t('search.modeLexical') }}</span>
                        </template>
                    </div>
                </div>
                <div class="lb-page-head-r">
                    <button v-if="semanticAvailable" class="lb-btn lb-btn-ghost" :class="{ 'is-on': semanticMode }"
                            @click="toggleSemantic" :title="$t('search.semanticToggleTitle')"
                            data-testid="semantic-toggle">
                        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v3m0 12v3m9-9h-3M6 12H3m14.5-6.5-2 2m-9 9-2 2m0-13 2 2m9 9 2 2"/></svg>
                        {{ $t('search.semantic') }}
                    </button>
                    <div class="lb-search">
                        <span class="lb-search-icon" v-html="icons.search"></span>
                        <input v-model="searchQuery" @input="debouncedSearch"
                               type="text" :placeholder="$t('papers.searchPlaceholder')"
                               class="lb-search-input" />
                        <button v-if="searchQuery" @click="clearSearch" class="lb-search-clear" :aria-label="$t('search.clear')">
                            <span v-html="icons.x"></span>
                        </button>
                    </div>
                </div>
            </header>

            <!-- Toolbar -->
            <div class="lb-toolbar">
                <div class="lb-toolbar-l">
                    <button class="lb-btn lb-btn-ghost" :class="{ 'is-on': showFilters || hasActiveFilters }"
                            @click="showFilters = !showFilters">
                        <span v-html="icons.filter"></span>
                        {{ $t('filters.heading') }}
                        <span v-if="hasActiveFilters" class="lb-pill lb-pill-accent">{{ activeFilterCount }}</span>
                    </button>
                    <button class="lb-btn lb-btn-ghost" @click="openResearchChat">
                        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                        {{ $t('nav.researchChat') }}
                    </button>
                    <button class="lb-btn lb-btn-ghost" :disabled="dupLoading" @click="findDuplicates">
                        <svg v-if="!dupLoading" xmlns="http://www.w3.org/2000/svg" width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
                        <span v-else class="spinner-sm"></span>
                        {{ $t('duplicates.short') }}
                        <span v-if="dupGroups && dupGroups.length" class="lb-pill lb-pill-accent">{{ dupGroups.length }}</span>
                    </button>
                </div>
                <div class="lb-toolbar-r">
                    <div class="lb-sortgroup">
                        <span class="lb-sortgroup-l">{{ $t('papers.sortBy') }}</span>
                        <select v-model="sortBy" class="lb-select">
                            <option value="year">{{ $t('common.year') }}</option>
                            <option value="authors">{{ $t('papers.sortAuthor') }}</option>
                            <option value="title">{{ $t('common.title') }}</option>
                            <option value="cited_by_count">{{ $t('common.citations') }}</option>
                            <option value="created_at">{{ $t('papers.sortAdded') }}</option>
                        </select>
                        <button class="lb-icon-btn" :title="sortAsc ? $t('papers.sortAsc') : $t('papers.sortDesc')"
                                @click="sortAsc = !sortAsc">
                            {{ sortAsc ? '↑' : '↓' }}
                        </button>
                    </div>
                    <div class="lb-segmented">
                        <button class="lb-seg" :class="{ 'is-on': view === 'list' }" :title="$t('papers.viewList')"
                                @click="view = 'list'">
                            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
                        </button>
                        <button class="lb-seg" :class="{ 'is-on': view === 'grid' }" :title="$t('papers.viewGrid')"
                                @click="view = 'grid'">
                            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
                        </button>
                    </div>
                </div>
            </div>

            <!-- Filter Bar -->
            <div v-if="showFilters" class="lb-filter-bar">
                <div class="lb-filter-grid" :style="{ gridTemplateColumns: filterGridColumns }">
                    <label class="lb-field">
                        <span class="lb-field-label">{{ $t('filters.yearFrom') }}</span>
                        <input v-model.number="filters.yearFrom" type="number" :placeholder="$t('filters.yearFromPlaceholder')" class="lb-input" />
                    </label>
                    <label class="lb-field">
                        <span class="lb-field-label">{{ $t('filters.yearTo') }}</span>
                        <input v-model.number="filters.yearTo" type="number" :placeholder="$t('filters.yearToPlaceholder')" class="lb-input" />
                    </label>
                    <label class="lb-field">
                        <span class="lb-field-label">{{ $t('filters.category') }}</span>
                        <select v-model="filters.categoryId" class="lb-input">
                            <option value="">{{ $t('filters.allCategories') }}</option>
                            <option v-for="c in allCategories" :key="c.id" :value="c.id">{{ c.name }}</option>
                        </select>
                    </label>
                    <label class="lb-field">
                        <span class="lb-field-label">DOI</span>
                        <select v-model="filters.hasDoi" class="lb-input">
                            <option value="">{{ $t('filters.any') }}</option>
                            <option value="yes">{{ $t('filters.withDoi') }}</option>
                            <option value="no">{{ $t('filters.withoutDoi') }}</option>
                        </select>
                    </label>
                    <label class="lb-field">
                        <span class="lb-field-label">{{ $t('filters.provenance') }}</span>
                        <select v-model="filters.importSource" class="lb-input">
                            <option value="">{{ $t('filters.allSources') }}</option>
                            <option value="pdf">{{ $t('filters.srcPdf') }}</option>
                            <option value="bibtex">{{ $t('filters.srcBibtex') }}</option>
                            <option value="ris">{{ $t('filters.srcRis') }}</option>
                            <option value="other">{{ $t('filters.srcOther') }}</option>
                        </select>
                    </label>
                    <label class="lb-field">
                        <span class="lb-field-label">PDF</span>
                        <select v-model="filters.hasPdf" class="lb-input">
                            <option value="">{{ $t('filters.any') }}</option>
                            <option value="yes">{{ $t('filters.withPdf') }}</option>
                            <option value="no">{{ $t('filters.withoutPdf') }}</option>
                        </select>
                    </label>
                    <!-- Add-on slot "item-list-filter" (#190) -->
                    <div v-for="s in listFilterSlots" :key="s.key" class="lb-field" :data-addon-slot="s.key">
                        <component :is="s.component" :selection="filters.addonSelections[s.addon] || null"
                                   @update:selection="setAddonSelection(s.addon, $event)" />
                    </div>
                </div>
                <div v-if="tagCloud.length" class="lb-field" style="margin-top: 14px;">
                    <span class="lb-field-label">{{ $t('filters.frequentCategories') }}</span>
                    <div class="lb-chiprow">
                        <button v-for="[name, n, id] in tagCloud" :key="id"
                                class="lb-chip"
                                :class="{ 'is-on': String(filters.categoryId) === String(id) }"
                                @click="filters.categoryId = (String(filters.categoryId) === String(id) ? '' : String(id))">
                            {{ name }} <span class="lb-chip-n">{{ n }}</span>
                        </button>
                    </div>
                </div>
                <div v-if="hasActiveFilters" style="margin-top: 12px;">
                    <button class="lb-btn lb-btn-text" @click="resetFilters">{{ $t('filters.resetAll') }}</button>
                </div>
            </div>

            <!-- Selection Action Bar -->
            <div v-if="selectedPapers.length > 0" class="bg-accent-soft border border-accent-soft rounded-lg p-3 mb-4 flex items-center justify-between">
                <span class="text-sm font-medium text-accent-ink">{{ $t('bulk.selectedCount', { count: selectedPapers.length }) }}</span>
                <div class="flex items-center gap-2">
                    <!-- Action Dropdown -->
                    <div class="relative" ref="actionDropdownRef">
                        <button @click="showActionDropdown = !showActionDropdown"
                                class="inline-flex items-center gap-1.5 bg-white border border-accent-soft text-accent-ink px-3 py-1.5 rounded-lg text-sm font-medium hover:bg-accent-soft transition-colors">
                            {{ $t('bulk.chooseAction') }}
                            <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
                        </button>
                        <div v-if="showActionDropdown" class="absolute right-0 top-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-20 w-56 py-1">
                            <button @click="bulkValidate(); showActionDropdown = false" :disabled="bulkValidating"
                                    class="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2 disabled:opacity-40">
                                <span v-html="icons.refresh"></span>
                                {{ bulkValidating ? $t('bulk.validating') : $t('bulk.validateMetadata') }}
                            </button>
                            <button @click="bulkFetchAbstracts(); showActionDropdown = false" :disabled="fetchingAbstracts"
                                    class="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2 disabled:opacity-40">
                                <span v-html="icons.pdf"></span>
                                {{ fetchingAbstracts ? 'Lade...' : 'Abstracts holen' }}
                            </button>
                            <button @click="discoverDois(); showActionDropdown = false" :disabled="discoveringDois"
                                    class="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2 disabled:opacity-40">
                                <span v-html="icons.search"></span>
                                {{ discoveringDois ? $t('bulk.searching') : $t('bulk.findDois') }}
                            </button>
                            <div class="border-t border-gray-100 my-1"></div>
                            <button @click="bulkDeleteSelected(); showActionDropdown = false" :disabled="bulkDeleting"
                                    class="w-full text-left px-4 py-2 text-sm text-red-600 hover:bg-red-50 flex items-center gap-2 disabled:opacity-40">
                                {{ bulkDeleting ? $t('bulk.deleting') : $t('bulk.deleteSelected') }}
                            </button>
                        </div>
                    </div>
                    <button @click="selectedPapers = []; selectAll = false" class="text-xs text-gray-500 hover:text-gray-700 px-2 py-1">{{ $t('bulk.clearSelection') }}</button>
                </div>
            </div>

            <!-- Loading -->
            <div v-if="loading" class="flex justify-center py-12">
                <div class="spinner"></div>
            </div>

            <!-- Semantic Search Results (own branch: different fields than a full paper,
                 no silent mixing into the regular filtered list -- Erwartungsbruch) -->
            <template v-else-if="semanticMode">
                <p v-if="semanticMeta.note" class="lb-empty-text" style="margin: 0 2px 12px;">{{ semanticMeta.note }}</p>
                <ul v-if="semanticResults.length" class="lb-rowlist" data-testid="semantic-results">
                    <li v-for="hit in semanticResults" :key="hit.paper_id" class="lb-row"
                        @click="$router.push({ name: 'paper', params: { id: hit.paper_id } })">
                        <div class="lb-row-year">
                            <span class="lb-year-num">{{ hit.year || '—' }}</span>
                        </div>
                        <div class="lb-row-body">
                            <h3 class="lb-row-title">{{ hit.title || $t('papers.untitled') }}</h3>
                            <div class="lb-row-meta">
                                <span class="lb-authors">{{ hit.citekey }}</span>
                                <span class="lb-meta-dot">&middot;</span>
                                <span>{{ $t('search.score') }} {{ hit.score.toFixed(3) }}</span>
                            </div>
                            <p v-if="hit.abstract_excerpt" class="lb-row-abstract">{{ hit.abstract_excerpt }}</p>
                        </div>
                    </li>
                </ul>
                <div v-else class="lb-empty">
                    <div class="lb-empty-mark">[ ]</div>
                    <div class="lb-empty-title">{{ searchQuery ? $t('papers.noResults') : $t('search.enterQuery') }}</div>
                    <p class="lb-empty-text">{{ $t('search.semanticHint') }}</p>
                </div>
            </template>

            <!-- Select All + Papers -->
            <template v-else-if="filteredPapers.length">
                <div style="display: flex; align-items: center; gap: 10px; margin: 6px 2px 10px; font-family: var(--lb-font-mono); font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--lb-mute);">
                    <label style="display: inline-flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                        <input type="checkbox" v-model="selectAll" @change="toggleSelectAll" />
                        {{ $t('bulk.selectAll') }}
                    </label>
                </div>

                <!-- List View -->
                <ul v-if="view === 'list'" class="lb-rowlist">
                    <li v-for="paper in sortedPapers" :key="paper.id"
                        class="lb-row"
                        :style="selectedPapers.includes(paper.id) ? { background: 'var(--lb-bg-soft)' } : null"
                        @click="$router.push({ name: 'paper', params: { id: paper.id } })">
                        <div class="lb-row-year">
                            <input type="checkbox" :value="paper.id" v-model="selectedPapers" @click.stop
                                   style="margin-bottom: 8px;" />
                            <span class="lb-year-num">{{ paper.year || '—' }}</span>
                        </div>
                        <div class="lb-row-body">
                            <h3 class="lb-row-title">{{ paper.title || $t('papers.untitled') }}</h3>
                            <div class="lb-row-meta">
                                <span class="lb-authors">{{ formatAuthors(paper.authors) }}</span>
                                <template v-if="paper.journal">
                                    <span class="lb-meta-dot">&middot;</span>
                                    <span class="lb-journal">{{ paper.journal }}</span>
                                </template>
                            </div>
                            <p v-if="paper.abstract" class="lb-row-abstract">{{ paper.abstract }}</p>
                            <div class="lb-row-foot">
                                <div class="lb-tags">
                                    <span class="lb-tag lb-tag-src" :class="'lb-src-' + paperSource(paper).key"
                                          :title="$t('papers.provenanceTooltip', { source: paperSource(paper).label + (paperSource(paper).detail ? ' (' + paperSource(paper).detail + ')' : '') })"
                                          @click.stop="filters.importSource = paperSource(paper).key; showFilters = true">
                                        {{ paperSource(paper).label }}
                                    </span>
                                    <span v-for="cat in (paper.categories || [])" :key="'c'+cat.id"
                                          class="lb-tag lb-tag-cat"
                                          @click.stop="filters.categoryId = String(cat.id); showFilters = true">
                                        {{ cat.name }}
                                    </span>
                                </div>
                                <div class="lb-row-stats">
                                    <span v-if="paper.cited_by_count" class="lb-stat" :title="$t('common.citations')">
                                        <span class="lb-stat-n">{{ paper.cited_by_count }}</span>
                                        <span class="lb-stat-l">{{ $t('papers.citAbbr') }}</span>
                                    </span>
                                    <span v-if="paper.page_count" class="lb-stat" :title="$t('papers.pages')">
                                        <span class="lb-stat-n">{{ paper.page_count }}</span>
                                        <span class="lb-stat-l">{{ $t('papers.pagesAbbr') }}</span>
                                    </span>
                                    <span v-if="paper.filename" class="lb-stat lb-stat-pdf">PDF</span>
                                    <span v-else class="lb-stat lb-stat-nopdf" :title="$t('papers.noPdfTitle')">{{ $t('papers.noPdf') }}</span>
                                </div>
                            </div>
                        </div>
                    </li>
                </ul>

                <!-- Grid View -->
                <div v-else class="lb-grid">
                    <article v-for="paper in sortedPapers" :key="paper.id"
                             class="lb-pcard"
                             @click="$router.push({ name: 'paper', params: { id: paper.id } })">
                        <div class="lb-pcard-thumb">
                            <div class="lb-pcard-pages">
                                <span class="lb-pcard-year">{{ paper.year || '—' }}</span>
                                <span class="lb-pcard-doi">{{ (paper.doi || '').slice(0, 28) }}</span>
                            </div>
                        </div>
                        <div class="lb-pcard-body">
                            <h3 class="lb-pcard-title">{{ paper.title || $t('papers.untitled') }}</h3>
                            <div class="lb-pcard-meta">{{ formatAuthors(paper.authors) }}</div>
                            <div class="lb-tags" style="margin: 6px 0 2px;">
                                <span class="lb-tag lb-tag-src" :class="'lb-src-' + paperSource(paper).key"
                                      :title="$t('papers.provenanceTooltip', { source: paperSource(paper).label })"
                                      @click.stop="filters.importSource = paperSource(paper).key; showFilters = true">
                                    {{ paperSource(paper).label }}
                                </span>
                                <span v-if="!paper.filename" class="lb-tag lb-stat-nopdf" :title="$t('papers.noPdfTitle')">{{ $t('papers.noPdf') }}</span>
                            </div>
                            <div class="lb-pcard-foot">
                                <span class="lb-pcard-journal">{{ paper.journal || '' }}</span>
                                <span class="lb-pcard-cit" v-if="paper.cited_by_count">{{ paper.cited_by_count }} {{ $t('papers.citAbbr') }}</span>
                            </div>
                        </div>
                    </article>
                </div>
            </template>

            <!-- Empty State. Two different emptinesses (#178): a filter that
                 matched nothing is a dead end to back out of, while a library
                 with nothing in it yet is a beginning — and gets the two ways
                 in rather than a shrug. -->
            <div v-else class="lb-empty">
                <div class="lb-empty-mark">[ ]</div>
                <template v-if="libraryEmpty">
                    <div class="lb-empty-title">{{ $t('papers.empty.heading') }}</div>
                    <p class="lb-empty-text">{{ $t('papers.empty.intro') }}</p>
                    <div class="lb-empty-actions" data-testid="library-empty-panel">
                        <router-link to="/import" class="lb-btn lb-btn-primary"
                                     data-testid="library-empty-import">{{ $t('papers.empty.import') }}</router-link>
                        <router-link to="/migrate" class="lb-btn"
                                     data-testid="library-empty-migrate">{{ $t('papers.empty.migrate') }}</router-link>
                    </div>
                </template>
                <template v-else>
                    <div class="lb-empty-title">{{ $t('papers.noResults') }}</div>
                    <p class="lb-empty-text">
                        {{ $t('papers.emptyHint') }}
                    </p>
                </template>
            </div>

            <!-- DOI Confirmation Modal -->
            <div v-if="doiCandidates.length > 0" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="doiCandidates = []">
                <div class="bg-white rounded-xl shadow-xl max-w-3xl w-full mx-4 max-h-[85vh] flex flex-col">
                    <div class="flex items-center justify-between p-5 border-b border-gray-200">
                        <div>
                            <h3 class="text-lg font-semibold text-gray-900">{{ $t('doi.confirmHeading') }}</h3>
                            <p class="text-sm text-gray-500 mt-0.5">{{ $t('doi.candidateProgress', { index: doiCandidateIndex + 1, total: doiCandidates.length }) }}</p>
                        </div>
                        <button @click="doiCandidates = []" class="text-gray-400 hover:text-gray-600 p-1">
                            <span v-html="icons.x" style="width:20px;height:20px;"></span>
                        </button>
                    </div>
                    <div class="p-5 overflow-y-auto flex-1">
                        <template v-if="currentDoiCandidate">
                            <div class="mb-3">
                                <span class="inline-block bg-accent-soft text-accent-ink text-xs font-medium px-2 py-0.5 rounded-full">
                                    DOI: {{ currentDoiCandidate.doi }}
                                </span>
                            </div>
                            <!-- Side by Side Comparison -->
                            <div class="grid grid-cols-2 gap-4">
                                <!-- Local -->
                                <div class="rounded-lg border border-gray-200 p-4">
                                    <h4 class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">{{ $t('duplicates.ownMetadata') }}</h4>
                                    <div class="space-y-2">
                                        <div>
                                            <span class="block text-xs text-gray-400">{{ $t('common.title') }}</span>
                                            <span class="text-sm text-gray-900">{{ currentDoiCandidate.local.title || '—' }}</span>
                                        </div>
                                        <div>
                                            <span class="block text-xs text-gray-400">{{ $t('common.authors') }}</span>
                                            <span class="text-sm text-gray-900">{{ currentDoiCandidate.local.authors || '—' }}</span>
                                        </div>
                                        <div>
                                            <span class="block text-xs text-gray-400">{{ $t('common.year') }}</span>
                                            <span class="text-sm text-gray-900">{{ currentDoiCandidate.local.year || '—' }}</span>
                                        </div>
                                    </div>
                                </div>
                                <!-- CrossRef -->
                                <div class="rounded-lg border border-accent-soft bg-accent-soft p-4">
                                    <h4 class="text-xs font-semibold text-accent uppercase tracking-wide mb-3">{{ $t('duplicates.doiMetadata') }}</h4>
                                    <div class="space-y-2">
                                        <div>
                                            <span class="block text-xs text-gray-400">{{ $t('common.title') }}</span>
                                            <span class="text-sm text-gray-900">{{ currentDoiCandidate.crossref.title || '—' }}</span>
                                        </div>
                                        <div>
                                            <span class="block text-xs text-gray-400">{{ $t('common.authors') }}</span>
                                            <span class="text-sm text-gray-900">{{ currentDoiCandidate.crossref.authors || '—' }}</span>
                                        </div>
                                        <div>
                                            <span class="block text-xs text-gray-400">{{ $t('common.year') }}</span>
                                            <span class="text-sm text-gray-900">{{ currentDoiCandidate.crossref.year || '—' }}</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </template>
                    </div>
                    <div class="flex items-center justify-between p-5 border-t border-gray-200 bg-gray-50 rounded-b-xl">
                        <button @click="rejectDoiCandidate"
                                class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium border border-accent-soft text-accent bg-white hover:bg-accent-soft transition-colors">
                            <span v-html="icons.x"></span>
                            {{ $t('doi.reject') }}
                        </button>
                        <div class="flex gap-2">
                            <button @click="skipDoiCandidate"
                                    class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 text-gray-600 bg-white hover:bg-gray-100 transition-colors">
                                {{ $t('doi.skip') }}
                            </button>
                            <button @click="acceptDoiCandidate"
                                    :disabled="applyingDoi"
                                    class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-accent text-white hover:bg-accent-ink disabled:opacity-40 transition-colors">
                                <span v-if="applyingDoi" class="spinner" style="width:14px;height:14px;border-width:2px;border-top-color:#fff;"></span>
                                <span v-else v-html="icons.check"></span>
                                {{ $t('doi.accept') }}
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Duplikate Modal -->
            <div v-if="showDupModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="showDupModal = false">
                <div class="bg-white rounded-xl shadow-xl max-w-3xl w-full mx-4 max-h-[85vh] flex flex-col">
                    <div class="flex items-center justify-between p-5 border-b border-gray-200">
                        <div>
                            <h3 class="text-lg font-semibold text-gray-900">{{ $t('duplicates.heading') }}</h3>
                            <p class="text-sm text-gray-500 mt-0.5" v-if="dupGroups">{{ $t('duplicates.groupsFound', { count: dupGroups.length }) }}</p>
                        </div>
                        <button @click="showDupModal = false" class="text-gray-400 hover:text-gray-600 p-1">
                            <span v-html="icons.x" style="width:20px;height:20px;"></span>
                        </button>
                    </div>
                    <div class="p-5 overflow-y-auto flex-1">
                        <div v-if="dupLoading" class="flex justify-center py-12">
                            <div class="spinner"></div>
                        </div>
                        <div v-else-if="dupGroups && dupGroups.length === 0" class="text-center py-8">
                            <span class="text-accent text-4xl">&#10003;</span>
                            <p class="text-sm text-gray-600 mt-2">{{ $t('duplicates.none') }}</p>
                        </div>
                        <div v-else-if="dupGroups && dupGroups.length > 0" class="space-y-4">
                            <div v-for="(group, gi) in dupGroups" :key="gi" class="border border-accent-soft rounded-lg overflow-hidden">
                                <div class="bg-accent-soft px-4 py-2 flex items-center justify-between">
                                    <span class="text-sm font-medium text-accent-ink">{{ group.reason }}</span>
                                    <span class="text-xs text-accent">{{ $tn('common.itemCount', group.papers.length) }}</span>
                                </div>
                                <div class="divide-y divide-gray-100">
                                    <div v-for="p in group.papers" :key="p.id"
                                        class="px-4 py-3 flex items-center gap-4 hover:bg-gray-50 transition-colors"
                                        :class="dupKeep[gi] === p.id ? 'bg-accent-soft border-l-4 border-accent' : ''">
                                        <div class="flex-1 min-w-0">
                                            <router-link :to="'/paper/' + p.id" class="text-sm font-medium text-gray-900 hover:text-accent line-clamp-1" @click="showDupModal = false">
                                                {{ p.title || p.filename }}
                                            </router-link>
                                            <div class="text-xs text-gray-500 mt-0.5 flex items-center gap-3">
                                                <span v-if="p.authors">{{ p.authors }}</span>
                                                <span v-if="p.year">{{ p.year }}</span>
                                                <span v-if="p.doi" class="text-accent">{{ p.doi }}</span>
                                                <span class="text-gray-400">ID: {{ p.id }}</span>
                                                <span v-if="p.has_file === false" class="text-accent font-medium">{{ $t('duplicates.noFile') }}</span>
                                            </div>
                                        </div>
                                        <button @click="dupKeep[gi] = p.id; $forceUpdate()"
                                            class="px-3 py-1.5 text-xs rounded-lg transition-colors whitespace-nowrap"
                                            :class="dupKeep[gi] === p.id
                                                ? 'bg-accent text-white'
                                                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'">
                                            {{ dupKeep[gi] === p.id ? '&#10003; Behalten' : 'Behalten' }}
                                        </button>
                                    </div>
                                </div>
                                <div v-if="dupKeep[gi]" class="bg-gray-50 px-4 py-2 flex items-center justify-between border-t border-gray-200">
                                    <span class="text-xs text-gray-500">
                                        {{ group.papers.filter(p => p.id !== dupKeep[gi]).length }} Paper werden geloescht,
                                        Kategorien werden uebernommen.
                                    </span>
                                    <div class="flex items-center gap-2">
                                        <button @click="dismissDuplicateGroup(gi)"
                                            class="bg-white border border-gray-300 text-gray-600 px-3 py-1.5 text-xs rounded-lg hover:bg-gray-100 transition-colors">
                                            {{ $t('duplicates.keepBoth') }}
                                        </button>
                                        <button @click="mergeDuplicateGroup(gi)"
                                            :disabled="dupMerging"
                                            class="bg-accent text-white px-3 py-1.5 text-xs rounded-lg hover:bg-accent-ink disabled:opacity-50 transition-colors">
                                            {{ dupMerging ? 'Merge...' : 'Zusammenfuehren' }}
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `,
    data() {
        return {
            papers: [],
            searchQuery: '',
            sortBy: 'year',
            sortAsc: false,
            view: 'list',
            loading: true,
            debounceTimer: null,
            icons,
            selectedPapers: [],
            selectAll: false,
            bulkValidating: false,
            bulkDeleting: false,
            discoveringDois: false,
            fetchingAbstracts: false,
            doiCandidates: [],
            doiCandidateIndex: 0,
            applyingDoi: false,
            doiAcceptedCount: 0,
            showFilters: false,
            filters: {
                yearFrom: null, yearTo: null, categoryId: '', hasDoi: '', importSource: '', hasPdf: '',
                // Add-on filter selections (#190): add-on id -> { value, label, citeKeys }.
                addonSelections: {},
            },
            allCategories: [],
            customFields: [],
            showActionDropdown: false,
            // Duplikate
            showDupModal: false,
            dupLoading: false,
            dupGroups: null,
            dupKeep: {},
            dupMerging: false,
            // Semantic search toggle (#101, PRD-semantische-suche Phase 2, offene Frage 4):
            // explicit opt-in, default off. Hidden unless an embedding model is configured
            // (embedding role bound) -- otherwise /api/search/semantic just falls back to BM25
            // anyway, and a visible-but-useless toggle is worse than no toggle.
            semanticAvailable: false,
            semanticMode: false,
            semanticResults: [],
            semanticMeta: { mode: '', note: '' },
        };
    },
    computed: {
        resultCount() {
            return this.semanticMode ? this.semanticResults.length : this.filteredPapers.length;
        },
        title() {
            if (this.searchQuery) return 'Suchergebnisse';
            if (this.$route.name === 'category') return this.categoryName || t('breadcrumb.category');
            return 'Alle Paper';
        },
        heading() {
            if (this.$route.name === 'category') {
                return { eyebrow: t('breadcrumb.category'), title: this.categoryName || '—' };
            }
            return { eyebrow: t('breadcrumb.library'), title: t('nav.items') };
        },
        filterGridColumns() {
            // Jahr von, Jahr bis, Kategorie, DOI, Herkunft, PDF, [Add-on-Filter …]
            return '140px 140px 1fr 1fr 1fr 1fr' + ' 1fr'.repeat(this.listFilterSlots.length);
        },
        // Active Add-ons' filter components (slot "item-list-filter", #190).
        listFilterSlots() {
            return addonSlots('item-list-filter');
        },
        // Non-null selections of *active* Add-ons: { addonId: selection }.
        activeAddonSelections() {
            const out = {};
            for (const s of this.listFilterSlots) {
                const sel = this.filters.addonSelections[s.addon];
                if (sel) out[s.addon] = sel;
            }
            return out;
        },
        tagCloud() {
            const m = new Map();
            this.papers.forEach(p => (p.categories || []).forEach(c => {
                const key = c.id;
                const entry = m.get(key) || { name: c.name, id: c.id, n: 0 };
                entry.n += 1;
                m.set(key, entry);
            }));
            return [...m.values()]
                .sort((a, b) => b.n - a.n)
                .slice(0, 12)
                .map(e => [e.name, e.n, e.id]);
        },
        categoryName() {
            const cid = parseInt(this.$route.params.id);
            const cat = this.allCategories.find(c => c.id === cid);
            return cat ? cat.name : '';
        },
        hasActiveFilters() {
            return (
                this.filters.yearFrom || this.filters.yearTo || this.filters.categoryId || this.filters.hasDoi || this.filters.importSource || this.filters.hasPdf
                || Object.keys(this.activeAddonSelections).length > 0
            );
        },
        // "The library is empty" is a stronger statement than "this list is
        // empty" (#178): with a search, a filter or a category in play, an
        // empty list is a filter result and the migration offer would be noise.
        libraryEmpty() {
            return this.papers.length === 0
                && !this.searchQuery
                && !this.hasActiveFilters
                && this.$route.name !== 'category';
        },
        activeFilterCount() {
            let c = 0;
            if (this.filters.yearFrom || this.filters.yearTo) c++;
            if (this.filters.categoryId) c++;
            if (this.filters.hasDoi) c++;
            if (this.filters.importSource) c++;
            if (this.filters.hasPdf) c++;
            c += Object.keys(this.activeAddonSelections).length;
            return c;
        },
        currentDoiCandidate() {
            if (this.doiCandidateIndex < this.doiCandidates.length) {
                return this.doiCandidates[this.doiCandidateIndex];
            }
            return null;
        },
        filteredPapers() {
            let papers = [...this.papers];
            // Year filter
            if (this.filters.yearFrom) {
                papers = papers.filter(p => p.year && p.year >= this.filters.yearFrom);
            }
            if (this.filters.yearTo) {
                papers = papers.filter(p => p.year && p.year <= this.filters.yearTo);
            }
            // Add-on filters are server-side (cite_keys param in loadPapers).
            // Category filter
            if (this.filters.categoryId) {
                const cid = parseInt(this.filters.categoryId);
                papers = papers.filter(p => p.categories && p.categories.some(c => c.id === cid));
            }
            // DOI filter
            if (this.filters.hasDoi === 'yes') {
                papers = papers.filter(p => p.doi);
            } else if (this.filters.hasDoi === 'no') {
                papers = papers.filter(p => !p.doi);
            }
            // Import-source (provenance) filter
            if (this.filters.importSource) {
                papers = papers.filter(p => this.paperSource(p).key === this.filters.importSource);
            }
            // PDF-on-disk filter (importierte Eintraege ohne Datei haben leeres filename)
            if (this.filters.hasPdf === 'yes') {
                papers = papers.filter(p => !!p.filename);
            } else if (this.filters.hasPdf === 'no') {
                papers = papers.filter(p => !p.filename);
            }
            return papers;
        },
        sortedPapers() {
            const papers = [...this.filteredPapers];
            const key = this.sortBy;
            const dir = this.sortAsc ? 1 : -1;
            papers.sort((a, b) => {
                let va = a[key] || '';
                let vb = b[key] || '';
                if (key === 'year' || key === 'cited_by_count') {
                    return ((a[key] || 0) - (b[key] || 0)) * dir;
                }
                if (typeof va === 'string') va = va.toLowerCase();
                if (typeof vb === 'string') vb = vb.toLowerCase();
                if (va < vb) return -1 * dir;
                if (va > vb) return 1 * dir;
                return 0;
            });
            return papers;
        }
    },
    watch: {
        '$route'(to, from) {
            if (to.path !== from.path) {
                this.searchQuery = '';
                this.selectedPapers = [];
                this.selectAll = false;
                this.semanticResults = [];
                this.semanticMeta = { mode: '', note: '' };
                this.loadPapers();
            }
        },
    },
    created() {
        this.loadPapers();
        this.loadMeta();
        this._closeDropdown = (e) => {
            if (this.showActionDropdown && this.$refs.actionDropdownRef && !this.$refs.actionDropdownRef.contains(e.target)) {
                this.showActionDropdown = false;
            }
        };
        document.addEventListener('click', this._closeDropdown);
        this._onAddonChanged = (ev) => this.onAddonChanged(ev);
        window.addEventListener(ADDON_EVENT, this._onAddonChanged);
    },
    beforeUnmount() {
        document.removeEventListener('click', this._closeDropdown);
        window.removeEventListener(ADDON_EVENT, this._onAddonChanged);
    },
    methods: {
        async loadMeta() {
            try {
                const [fields, categories] = await Promise.all([
                    api('/api/custom-fields'),
                    api('/api/categories'),
                ]);
                this.customFields = fields;
                this.allCategories = categories;
            } catch (e) {}
            // Semantic-search toggle only shows up once the embedding role is
            // bound (connection + model, /api/llm/status). No config / request
            // failure -> stay hidden.
            try {
                const status = await api('/api/llm/status');
                this.semanticAvailable = !!(status && status.embedding);
            } catch (e) {
                this.semanticAvailable = false;
            }
            if (!this.semanticAvailable) this.semanticMode = false;
        },
        async loadPapers() {
            this.loading = true;
            try {
                let url = '/api/papers?';
                if (this.searchQuery) url += `search=${encodeURIComponent(this.searchQuery)}&`;
                if (this.$route.name === 'category' && this.$route.params.id) url += `category_id=${this.$route.params.id}&`;
                url = this.withAddonCiteKeys(url);
                this.papers = await api(url);
            } catch (e) {
                console.error('Load papers error:', e);
                this.papers = [];
            }
            this.loading = false;
        },
        debouncedSearch() {
            clearTimeout(this.debounceTimer);
            this.debounceTimer = setTimeout(() => {
                if (this.semanticMode) this.runSemanticSearch();
                else this.loadPapers();
            }, 300);
        },
        clearSearch() {
            this.searchQuery = '';
            if (this.semanticMode) {
                this.semanticResults = [];
                this.semanticMeta = { mode: '', note: '' };
            } else {
                this.loadPapers();
            }
        },
        // Explicit opt-in toggle (#101): switches the search from /api/papers
        // (server-side substring search) to /api/search/semantic (embedding
        // cosine ranking, with a lexical fallback baked into that endpoint).
        // Deliberately its own render branch (see template) instead of merging
        // hits into `papers` -- silently blending scored/unscored result sets
        // would break the "kein stilles Hybrid" expectation from the issue.
        toggleSemantic() {
            if (!this.semanticAvailable) return;
            this.semanticMode = !this.semanticMode;
            if (this.semanticMode) {
                this.runSemanticSearch();
            } else {
                this.semanticResults = [];
                this.semanticMeta = { mode: '', note: '' };
                this.loadPapers();
            }
        },
        async runSemanticSearch() {
            const q = this.searchQuery.trim();
            if (!q) {
                this.semanticResults = [];
                this.semanticMeta = { mode: '', note: '' };
                return;
            }
            this.loading = true;
            try {
                const data = await api(`/api/search/semantic?q=${encodeURIComponent(q)}`);
                this.semanticResults = data.results || [];
                this.semanticMeta = { mode: data.mode || '', note: data.note || '' };
            } catch (e) {
                console.error('Semantic search error:', e);
                this.semanticResults = [];
                this.semanticMeta = { mode: '', note: '' };
            }
            this.loading = false;
        },
        resetFilters() {
            const hadAddonFilter = Object.keys(this.activeAddonSelections).length > 0;
            this.filters = {
                yearFrom: null, yearTo: null, categoryId: '', hasDoi: '', importSource: '', hasPdf: '',
                addonSelections: {},
            };
            // Add-on filters are server-side (cite_keys): fetch again.
            if (hadAddonFilter) this.loadPapers();
        },
        // One Add-on's filter selection changed (slot "item-list-filter", #190).
        setAddonSelection(addonId, selection) {
            const next = Object.assign({}, this.filters.addonSelections);
            if (selection && Array.isArray(selection.citeKeys)) {
                next[addonId] = {
                    value: selection.value, label: selection.label,
                    citeKeys: selection.citeKeys.map(String),
                };
            } else {
                delete next[addonId];
            }
            this.filters.addonSelections = next;
            this.loadPapers();
        },
        // Adds the Add-on filters' cite keys to a /api/papers URL. Several
        // sources (every Add-on selection, and a `cite_keys` already in the
        // URL) are intersected; an empty result deliberately yields no Items.
        withAddonCiteKeys(url) {
            const sets = Object.values(this.activeAddonSelections).map((s) => s.citeKeys);
            if (!sets.length) return url;
            const prior = url.match(/cite_keys=([^&]*)&/);
            if (prior) {
                sets.push(decodeURIComponent(prior[1]).split(',').filter(Boolean));
                url = url.replace(prior[0], '');
            }
            let keys = sets[0];
            for (const other of sets.slice(1)) {
                const keep = new Set(other);
                keys = keys.filter((k) => keep.has(k));
            }
            return url + `cite_keys=${encodeURIComponent([...new Set(keys)].join(','))}&`;
        },
        // An Add-on switched off takes its filter with it.
        onAddonChanged(ev) {
            const d = (ev && ev.detail) || {};
            if (d.active || !this.filters.addonSelections[d.id]) return;
            const next = Object.assign({}, this.filters.addonSelections);
            delete next[d.id];
            this.filters.addonSelections = next;
            this.loadPapers();
        },
        // Provenance stamp: where a paper came from. The import *type* is derived
        // from original_filename (works retroactively on already-imported papers);
        // `detail` prefers the stored import_source (e.g. the .bib filename) and
        // otherwise falls back to what the original_filename encodes.
        paperSource(paper) {
            const orig = paper.original_filename || '';
            const detailStored = paper.import_source || '';
            if (orig.startsWith('BibTeX-Import:')) {
                return { key: 'bibtex', label: 'BibTeX', detail: detailStored || orig.slice('BibTeX-Import:'.length).trim() };
            }
            if (orig.startsWith('RIS-Import:')) {
                return { key: 'ris', label: 'RIS', detail: detailStored };
            }
            if (paper.filename) {
                return { key: 'pdf', label: 'PDF-Upload', detail: detailStored || orig };
            }
            return { key: 'other', label: 'Import', detail: detailStored || orig };
        },
        formatAuthors(authors) {
            if (!authors) return 'Unbekannt';
            if (Array.isArray(authors)) {
                if (authors.length === 0) return 'Unbekannt';
                if (authors.length <= 3) return authors.join(', ');
                return authors.slice(0, 2).join(', ') + ' et al.';
            }
            const parts = String(authors).split(/[,;]\s*/).filter(Boolean);
            if (parts.length > 3) return parts.slice(0, 2).join(', ') + ' et al.';
            return authors;
        },
        sortPapers() {},
        openResearchChat() {
            // Use selected papers if any, otherwise use all filtered papers
            const ids = this.selectedPapers.length > 0
                ? this.selectedPapers
                : this.filteredPapers.map(p => p.id);
            if (!ids.length) return;
            this.$router.push({ name: 'research-chat', query: { paper_ids: ids.join(',') } });
        },
        toggleSelectAll() {
            if (this.selectAll) {
                this.selectedPapers = this.filteredPapers.map(p => p.id);
            } else {
                this.selectedPapers = [];
            }
        },
        async fetchAbstracts() {
            this.fetchingAbstracts = true;
            try {
                const result = await api('/api/papers/fetch-abstracts', {
                    method: 'POST',
                });
                alert(`Abstracts aktualisiert:\n${result.total} Paper geprueft\n${result.fetched} Abstracts nachgeladen`);
                if (result.fetched > 0) {
                    await this.loadPapers();
                }
            } catch (e) {
                alert(t('error.abstractSearchFailed', { message: e.message }));
            }
            this.fetchingAbstracts = false;
        },
        async bulkFetchAbstracts() {
            if (!this.selectedPapers.length) return;
            this.fetchingAbstracts = true;
            try {
                const result = await api('/api/papers/fetch-abstracts', {
                    method: 'POST',
                    body: JSON.stringify({ paper_ids: this.selectedPapers }),
                });
                alert(`Abstracts aktualisiert:\n${result.total} Paper geprueft\n${result.fetched} Abstracts nachgeladen`);
                if (result.fetched > 0) {
                    await this.loadPapers();
                }
            } catch (e) {
                alert(t('error.abstractSearchFailed', { message: e.message }));
            }
            this.fetchingAbstracts = false;
        },
        async discoverDois() {
            this.discoveringDois = true;
            try {
                const result = await api('/api/papers/discover-dois', {
                    method: 'POST',
                });
                if (result.candidates && result.candidates.length > 0) {
                    this.doiCandidates = result.candidates;
                    this.doiCandidateIndex = 0;
                    this.doiAcceptedCount = 0;
                } else {
                    alert(`DOI-Suche abgeschlossen:\n${result.total_checked} Paper geprueft\nKeine neuen DOIs gefunden.`);
                }
            } catch (e) {
                alert(t('error.doiSearchFailed', { message: e.message }));
            }
            this.discoveringDois = false;
        },
        async acceptDoiCandidate() {
            if (!this.currentDoiCandidate) return;
            this.applyingDoi = true;
            try {
                await api('/api/papers/apply-doi', {
                    method: 'POST',
                    body: JSON.stringify({
                        paper_id: this.currentDoiCandidate.paper_id,
                        doi: this.currentDoiCandidate.doi,
                    }),
                });
                this.doiAcceptedCount++;
            } catch (e) {
                alert(t('error.applyFailed', { message: e.message }));
            }
            this.applyingDoi = false;
            this.nextDoiCandidate();
        },
        rejectDoiCandidate() {
            // Ablehnen = DOI nicht speichern, aber manuell verifiziert markieren,
            // damit bei der naechsten Suche nicht nochmal vorgeschlagen wird
            api(`/api/papers/${this.currentDoiCandidate.paper_id}`, {
                method: 'PUT',
                body: JSON.stringify({ doi: '' }),
            }).catch(() => {});
            this.nextDoiCandidate();
        },
        skipDoiCandidate() {
            this.nextDoiCandidate();
        },
        nextDoiCandidate() {
            if (this.doiCandidateIndex < this.doiCandidates.length - 1) {
                this.doiCandidateIndex++;
            } else {
                // Alle durchgegangen
                const total = this.doiCandidates.length;
                this.doiCandidates = [];
                this.doiCandidateIndex = 0;
                alert(`DOI-Pruefung abgeschlossen: ${this.doiAcceptedCount} von ${total} DOIs uebernommen.`);
                if (this.doiAcceptedCount > 0) {
                    this.loadPapers();
                }
            }
        },
        async bulkValidate() {
            if (!this.selectedPapers.length) return;
            this.bulkValidating = true;
            try {
                const result = await api('/api/papers/bulk-validate', {
                    method: 'POST',
                    body: JSON.stringify({ paper_ids: this.selectedPapers }),
                });
                // Response shape after the propose/apply refactor:
                //   results: [{id, status: applied|review|noop|error, confidence}]
                //   review_queue: [{id, proposal}]  -- low-Confidence Proposals
                //                                      to walk through manually
                const applied = result.results.filter(r => r.status === 'applied').length;
                const review = result.results.filter(r => r.status === 'review').length;
                const noop = result.results.filter(r => r.status === 'noop').length;
                const err = result.results.filter(r => r.status === 'error').length;
                let msg = `Validierung abgeschlossen: ${applied} auto-uebernommen`;
                if (review) msg += `, ${review} brauchen Pruefung (niedrige Konfidenz)`;
                if (noop) msg += `, ${noop} unveraendert`;
                if (err) msg += `, ${err} Fehler`;
                if (review && result.review_queue && result.review_queue.length) {
                    // Surface the review-queue IDs in the console so a user
                    // can find them. A future improvement could chain them
                    // into the validation popup sequentially.
                    console.info('Niedrig-Konfidenz Paper-IDs:', result.review_queue.map(e => e.id));
                }
                alert(msg);
                this.selectedPapers = [];
                this.selectAll = false;
                await this.loadPapers();
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            } catch (e) {
                alert('Bulk-Validierung fehlgeschlagen: ' + e.message);
            }
            this.bulkValidating = false;
        },
        async bulkDeleteSelected() {
            if (!this.selectedPapers.length || this.bulkDeleting) return;
            const n = this.selectedPapers.length;
            if (!confirm(t('bulk.deleteConfirm', { count: n }))) return;
            this.bulkDeleting = true;
            try {
                const res = await api('/api/papers/bulk-delete', {
                    method: 'POST',
                    body: JSON.stringify({ paper_ids: this.selectedPapers }),
                });
                this.selectedPapers = [];
                this.selectAll = false;
                await this.loadPapers();
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
                alert(t('bulk.deleted', { count: res.deleted }));
            } catch (e) {
                alert(t('error.deleteFailed', { message: e.message }));
            } finally {
                this.bulkDeleting = false;
            }
        },
        async findDuplicates() {
            this.dupLoading = true;
            this.showDupModal = true;
            this.dupGroups = null;
            this.dupKeep = {};
            try {
                const data = await api('/api/duplicates');
                this.dupGroups = data.groups || [];
                for (let i = 0; i < this.dupGroups.length; i++) {
                    const g = this.dupGroups[i];
                    const best = g.papers.reduce((a, b) => {
                        const scoreA = (a.doi ? 2 : 0) + (a.title ? 1 : 0) + (a.authors ? 1 : 0);
                        const scoreB = (b.doi ? 2 : 0) + (b.title ? 1 : 0) + (b.authors ? 1 : 0);
                        return scoreB > scoreA ? b : a;
                    });
                    this.dupKeep[i] = best.id;
                }
            } catch (e) {
                alert(t('error.duplicateSearchFailed', { message: e.message }));
            }
            this.dupLoading = false;
        },
        async mergeDuplicateGroup(gi) {
            const group = this.dupGroups[gi];
            const keepId = this.dupKeep[gi];
            if (!keepId) return;
            const deleteIds = group.papers.filter(p => p.id !== keepId).map(p => p.id);
            if (!confirm(t('duplicates.mergeConfirm', { count: deleteIds.length, keepId: keepId }))) return;
            this.dupMerging = true;
            try {
                await api('/api/duplicates/merge', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ keep_id: keepId, delete_ids: deleteIds }),
                });
                this.dupGroups.splice(gi, 1);
                const newKeep = {};
                for (let i = 0; i < this.dupGroups.length; i++) {
                    const oldIdx = i >= gi ? i + 1 : i;
                    if (this.dupKeep[oldIdx] !== undefined) newKeep[i] = this.dupKeep[oldIdx];
                }
                this.dupKeep = newKeep;
                await this.loadPapers();
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            } catch (e) {
                alert(t('error.mergeFailed', { message: e.message }));
            }
            this.dupMerging = false;
        },
        dismissDuplicateGroup(gi) {
            this.dupGroups.splice(gi, 1);
            const newKeep = {};
            for (let i = 0; i < this.dupGroups.length; i++) {
                const oldIdx = i >= gi ? i + 1 : i;
                if (this.dupKeep[oldIdx] !== undefined) newKeep[i] = this.dupKeep[oldIdx];
            }
            this.dupKeep = newKeep;
            if (this.dupGroups.length === 0) this.showDupModal = false;
        }
    }
};


// =============================================================================
// PaperDetail Component
// =============================================================================

const PaperDetail = {
    template: `
        <div class="lb-modal-overlay" @click.self="close">
        <!-- Two-panel assembly (#161). It owns the gap between the panels, so a
             click there hits the assembly and stops — only the strip left of the
             PDF panel is still backdrop and still closes the view. -->
        <div class="lb-detail-assembly" @click.stop data-testid="detail-assembly">

            <!-- ============ PDF PANEL (docked, scrolls on its own) ============ -->
            <section v-if="paper" class="lb-pdf-panel" :class="{ 'is-empty': !paper.filename }"
                     data-testid="pdf-panel">
                <header class="lb-pdf-head">
                    <span class="lb-mono lb-truncate lb-pdf-name" :title="paper.filename || $t('pdf.none')">
                        {{ paper.filename || $t('pdf.none') }}
                    </span>
                    <!-- Without a file the actions belong to the drop zone below,
                         not up here twice. -->
                    <div class="lb-pdf-actions">
                        <button v-if="paper.filename" class="lb-btn lb-btn-primary" @click="openInApp">{{ $t('pdf.open') }}</button>
                        <input ref="attachPdfInput" type="file" accept="application/pdf,.pdf" style="display:none" @change="onPdfSelected" />
                    </div>
                </header>
                <div class="lb-pdf-body">
                    <iframe v-if="paper.filename" class="lb-pdf-frame" data-testid="pdf-frame"
                            :src="'/api/papers/' + paper.id + '/pdf' + (pdfPage ? '#page=' + pdfPage : '')"
                            loading="lazy"></iframe>
                    <div v-else class="lb-pdf-drop" data-testid="pdf-drop-zone">
                        <p class="lb-pdf-drop-h">{{ $t('pdf.none') }}</p>
                        <p class="lb-pdf-drop-p">{{ $t('pdf.dropHint') }}</p>
                        <div class="lb-pdf-drop-actions">
                            <button class="lb-btn lb-btn-primary" @click="$refs.attachPdfInput.click()" :disabled="attachingPdf">
                                {{ attachingPdf ? $t('pdf.attaching') : $t('pdf.attach') }}
                            </button>
                            <button v-if="paper.doi" class="lb-btn lb-btn-ghost" @click="fetchOaPdf" :disabled="fetchingOa">
                                {{ fetchingOa ? $t('pdf.fetchingOa') : $t('pdf.fetchOa') }}
                            </button>
                            <a class="lb-btn lb-btn-ghost" :href="scholarUrl" target="_blank" rel="noopener">Google Scholar</a>
                        </div>
                    </div>
                </div>
            </section>

            <div class="lb-modal">
            <!-- Sticky header -->
            <header class="lb-modal-head">
                <button class="lb-modal-back" @click="close">
                    <span v-html="icons.back"></span>
                    {{ $t('detail.backToList') }}
                </button>
                <div class="lb-modal-actions" v-if="paper">
                    <button class="lb-btn lb-btn-ghost" @click="copyBibtex">
                        {{ bibtexCopied ? 'BibTeX kopiert!' : 'BibTeX' }}
                    </button>
                    <a v-if="paper.doi" class="lb-btn lb-btn-ghost"
                       :href="'https://doi.org/' + paper.doi" target="_blank" rel="noopener">
                        {{ $t('detail.openDoi') }}
                    </a>
                    <!-- Only visible in the two-column band (1100-1440px), where the
                         metadata column is folded away by default. -->
                    <button class="lb-btn lb-btn-ghost lb-meta-toggle" @click="metaOpen = !metaOpen"
                            :aria-pressed="String(metaOpen)" data-testid="meta-toggle">
                        {{ metaOpen ? $t('detail.hideMetadata') : $t('detail.metadata') }}
                    </button>
                    <div class="relative">
                        <button @click="actionMenuOpen = !actionMenuOpen"
                                class="lb-modal-close" :aria-label="$t('common.more')"
                                style="width:32px;">
                            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg>
                        </button>
                        <div v-if="actionMenuOpen"
                             class="absolute right-0 top-full mt-1 w-56 bg-white border border-gray-200 rounded-lg shadow-lg z-30 py-1"
                             style="background: var(--lb-bg-elev); border-color: var(--lb-hairline);"
                             @click="actionMenuOpen = false">
                            <button @click="startEditMetadata"
                                    class="w-full flex items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
                                <span v-html="icons.edit"></span> {{ $t('common.edit') }}
                            </button>
                            <button @click="runOcr" :disabled="ocrRunning"
                                    class="w-full flex items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-40">
                                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="15" x2="15" y2="15"/></svg>
                                {{ ocrRunning ? 'OCR laeuft...' : 'OCR starten' }}
                            </button>
                            <button @click="validateMetadata" :disabled="validating"
                                    class="w-full flex items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-40">
                                <span v-html="icons.refresh"></span>
                                {{ validating ? $t('bulk.validating') : $t('bulk.validateMetadata') }}
                            </button>
                            <button @click="extractReferences()" :disabled="extractingRefs"
                                    class="w-full flex items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-40">
                                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
                                {{ extractingRefs ? $t('refs.extracting') : $t('refs.extract') }}
                            </button>
                            <router-link :to="'/research-chat?paper_ids=' + paper.id"
                                         class="w-full flex items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
                                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                                {{ $t('nav.researchChat') }}
                            </router-link>
                            <div class="border-t my-1" style="border-color: var(--lb-hairline);"></div>
                            <button @click="deletePaper"
                                    class="w-full flex items-center gap-2 px-4 py-2 text-sm text-accent hover:bg-accent-soft">
                                <span v-html="icons.trash"></span> {{ $t('common.delete') }}
                            </button>
                        </div>
                        <div v-if="actionMenuOpen" class="fixed inset-0 z-20" @click="actionMenuOpen = false"></div>
                    </div>
                    <button class="lb-modal-close" @click="close" :aria-label="$t('common.close')">
                        <span v-html="icons.x"></span>
                    </button>
                </div>
            </header>

            <!-- Loading -->
            <div v-if="loading" class="flex justify-center py-12" style="flex:1;">
                <div class="spinner"></div>
            </div>

            <template v-else-if="paper">
            <div class="lb-modal-grid" :class="{ 'is-meta-open': metaOpen }">
                <!-- ============ MAIN (content column, own scroll region) ============ -->
                <main class="lb-modal-main" data-testid="content-column">

                <!-- Edit Metadata Form (replaces title block) -->
                <div v-if="editingMeta" class="lb-detail-section">
                    <h4 class="lb-detail-h">{{ $t('detail.editMetadata') }}</h4>
                    <div class="space-y-3 mb-4">
                        <div>
                            <label class="block text-xs text-gray-500 mb-1" style="font-family: var(--lb-font-mono); text-transform: uppercase; letter-spacing: 0.06em; color: var(--lb-mute);">{{ $t('common.title') }}</label>
                            <input v-model="editMeta.title" class="lb-input" />
                        </div>
                        <div class="grid grid-cols-2 gap-3">
                            <div>
                                <label class="block text-xs mb-1" style="font-family: var(--lb-font-mono); text-transform: uppercase; letter-spacing: 0.06em; color: var(--lb-mute);">{{ $t('common.authors') }}</label>
                                <input v-model="editMeta.authors" class="lb-input" />
                            </div>
                            <div>
                                <label class="block text-xs mb-1" style="font-family: var(--lb-font-mono); text-transform: uppercase; letter-spacing: 0.06em; color: var(--lb-mute);">{{ $t('common.year') }}</label>
                                <input v-model.number="editMeta.year" type="number" class="lb-input" />
                            </div>
                        </div>
                        <div class="grid grid-cols-2 gap-3">
                            <div>
                                <label class="block text-xs mb-1" style="font-family: var(--lb-font-mono); text-transform: uppercase; letter-spacing: 0.06em; color: var(--lb-mute);">DOI</label>
                                <input v-model="editMeta.doi" class="lb-input lb-mono" />
                            </div>
                            <div>
                                <label class="block text-xs mb-1" style="font-family: var(--lb-font-mono); text-transform: uppercase; letter-spacing: 0.06em; color: var(--lb-mute);">ISBN</label>
                                <input v-model="editMeta.isbn" class="lb-input lb-mono" />
                            </div>
                        </div>
                        <div class="grid grid-cols-2 gap-3">
                            <div>
                                <label class="block text-xs mb-1" style="font-family: var(--lb-font-mono); text-transform: uppercase; letter-spacing: 0.06em; color: var(--lb-mute);">Journal</label>
                                <input v-model="editMeta.journal" class="lb-input" />
                            </div>
                            <div>
                                <label class="block text-xs mb-1" style="font-family: var(--lb-font-mono); text-transform: uppercase; letter-spacing: 0.06em; color: var(--lb-mute);">{{ $t('common.publisher') }}</label>
                                <input v-model="editMeta.publisher" class="lb-input" />
                            </div>
                        </div>
                        <div>
                            <div class="flex items-center justify-between mb-1">
                                <label class="block text-xs" style="font-family: var(--lb-font-mono); text-transform: uppercase; letter-spacing: 0.06em; color: var(--lb-mute);">Abstract</label>
                                <button @click="generateAbstract" :disabled="generatingAbstract"
                                        class="lb-btn lb-btn-ghost" style="font-size: 11px; padding: 4px 8px;">
                                    <span v-if="generatingAbstract" class="spinner" style="width:10px;height:10px;border-width:1.5px;"></span>
                                    {{ generatingAbstract ? 'Generiere...' : 'Abstract generieren' }}
                                </button>
                            </div>
                            <textarea v-model="editMeta.abstract" rows="4" class="lb-input"></textarea>
                        </div>
                    </div>
                    <div class="flex gap-2">
                        <button @click="saveMetadata" class="lb-btn lb-btn-primary">
                            <span v-html="icons.check"></span> {{ $t('common.save') }}
                        </button>
                        <button @click="editingMeta = false" class="lb-btn lb-btn-ghost">{{ $t('common.cancel') }}</button>
                    </div>
                </div>

                <!-- Title block -->
                <template v-else>
                    <div class="lb-detail-eyebrow">
                        <span v-if="paper.year">{{ paper.year }}</span>
                        <span v-if="paper.year && paper.journal" class="lb-meta-dot">&middot;</span>
                        <span v-if="paper.journal">{{ paper.journal }}</span>
                        <template v-if="paper.doi">
                            <span class="lb-meta-dot">&middot;</span>
                            <span class="lb-mono">{{ paper.doi }}</span>
                        </template>
                    </div>
                    <h1 class="lb-detail-title">{{ paper.title || $t('papers.untitled') }}</h1>
                    <p class="lb-detail-authors" v-if="paper.authors">{{ paper.authors }}</p>
                </template>

                <!-- Abstract -->
                <section v-if="!editingMeta && paper.abstract" class="lb-detail-section">
                    <div class="flex items-center gap-2" style="margin-bottom: 10px;">
                        <h4 class="lb-detail-h" style="margin: 0;">Abstract</h4>
                        <span v-if="paper.abstract_source === 'generated'" class="lb-tag" style="font-size: 9.5px;" :title="$t('abstract.aiGeneratedTitle')">{{ $t('abstract.aiGenerated') }}</span>
                        <span v-else-if="paper.abstract_source === 'pdf'" class="lb-tag" style="font-size: 9.5px;" :title="$t('abstract.aiExtractedTitle')">{{ $t('abstract.aiExtracted') }}</span>
                    </div>
                    <p class="lb-detail-abstract">{{ paper.abstract }}</p>
                </section>

                <!-- Notizen (#160) -->
                <section v-if="!editingMeta" class="lb-detail-section" data-testid="paper-notes">
                    <div class="flex items-center justify-between" style="margin-bottom: 10px;">
                        <h4 class="lb-detail-h" style="margin: 0;">{{ $t('notes.heading') }}</h4>
                        <div class="flex items-center gap-3">
                            <span v-if="notesStatus" class="lb-notes-status" :data-state="notesSaveState">{{ notesStatus }}</span>
                            <button @click="notesEditing ? showNotes() : editNotes()" class="lb-btn-text" style="font-size: 11.5px;">
                                {{ notesEditing ? $t('notes.view') : $t('common.edit') }}
                            </button>
                        </div>
                    </div>
                    <div v-if="notesEditing" class="lb-notes-editor" data-testid="paper-notes-editor">
                        <textarea ref="notesInput" v-model="notesDraft" @input="onNotesTyped()"
                                  rows="6" class="lb-input"
                                  :placeholder="$t('notes.placeholder')"></textarea>
                    </div>
                    <div v-else class="lb-md lb-notes-rendered" data-testid="paper-notes-rendered"
                         :title="$t('notes.clickToEdit')" @click="editNotes()" v-html="notesHtml"></div>
                </section>

                <!-- Custom Fields -->
                <section v-if="!editingMeta && paper.custom_fields && paper.custom_fields.length" class="lb-detail-section">
                    <h4 class="lb-detail-h">{{ $t('customFields.heading') }}</h4>
                    <div class="space-y-4">
                        <div v-for="cf in paper.custom_fields" :key="cf.field_id">
                            <template v-if="cf.field_type === 'text'">
                                <label class="block text-xs mb-1" style="color: var(--lb-mute);">{{ cf.name }}</label>
                                <textarea v-model="customValues[cf.field_id]"
                                          @blur="saveCustomValue(cf.field_id)"
                                          rows="3" :placeholder="$t('customFields.enterPlaceholder', { name: cf.name })"
                                          class="lb-input"></textarea>
                            </template>
                            <template v-else-if="cf.field_type === 'number'">
                                <label class="block text-xs mb-1" style="color: var(--lb-mute);">{{ cf.name }}</label>
                                <input v-model="customValues[cf.field_id]"
                                       @blur="saveCustomValue(cf.field_id)"
                                       type="number" class="lb-input" />
                            </template>
                            <template v-else-if="cf.field_type === 'progress'">
                                <label class="block text-xs mb-1" style="color: var(--lb-mute);">{{ cf.name }}: {{ customValues[cf.field_id] || 0 }}%</label>
                                <div class="flex items-center gap-3">
                                    <input v-model="customValues[cf.field_id]"
                                           @input="saveCustomValue(cf.field_id)"
                                           type="range" min="0" max="100" step="5"
                                           class="flex-1 h-2 rounded-lg appearance-none cursor-pointer" style="background: var(--lb-bg-soft); accent-color: var(--lb-accent);" />
                                    <div class="w-32 h-3 rounded-full overflow-hidden flex-shrink-0" style="background: var(--lb-bg-soft);">
                                        <div class="h-full rounded-full transition-all" style="background: var(--lb-accent);" :style="{ width: (customValues[cf.field_id] || 0) + '%' }"></div>
                                    </div>
                                </div>
                            </template>
                            <template v-else-if="cf.field_type === 'select'">
                                <label class="block text-xs mb-1" style="color: var(--lb-mute);">{{ cf.name }}</label>
                                <select v-model="customValues[cf.field_id]"
                                        @change="saveCustomValue(cf.field_id)"
                                        class="lb-input">
                                    <option value="">{{ $t('common.selectPlaceholder') }}</option>
                                    <option v-for="opt in (cf.options || '').split(',')" :key="opt" :value="opt.trim()">{{ opt.trim() }}</option>
                                </select>
                            </template>
                        </div>
                    </div>
                </section>

                <!-- Extracted References -->
                <section v-if="!editingMeta" class="lb-detail-section">
                    <div class="flex items-center justify-between" style="margin-bottom: 10px;">
                        <h4 class="lb-detail-h" style="margin: 0;">
                            {{ $t('refs.extractedHeading') }}
                            <span v-if="paperRefs.length" style="color: var(--lb-mute);">({{ paperRefs.length }})</span>
                        </h4>
                        <div class="flex items-center gap-2">
                            <button @click="startAddRef" class="lb-btn-text" style="font-size: 11.5px;">+ {{ $t('refs.manual') }}</button>
                            <button v-if="paperRefs.length" @click="reExtractReferences" :disabled="extractingRefs" class="lb-btn-text" style="font-size: 11.5px;">{{ $t('refs.reExtract') }}</button>
                        </div>
                    </div>

                    <div v-if="refExtractionResult" class="mb-4 p-3 rounded-lg text-sm" style="background: var(--lb-bg-soft); border: 1px solid var(--lb-hairline);">
                        <p v-if="refExtractionResult.status === 'ok' || refExtractionResult.status === 'already_extracted'" style="color: var(--lb-ink-2);">
                            <strong>{{ refExtractionResult.total_extracted }}</strong> {{ $t('refs.countReferences') }} &middot;
                            <strong>{{ refExtractionResult.with_doi }}</strong> {{ $t('refs.countWithDoi') }} &middot;
                            <strong>{{ refExtractionResult.in_library }}</strong> {{ $t('refs.inLibrary') }}
                        </p>
                        <p v-else style="color: var(--lb-ink-2);">{{ refExtractionResult.error || $t('refs.extractionFailed') }}</p>
                    </div>

                    <div v-if="paperRefs.length" class="space-y-2" style="max-height: 600px; overflow-y: auto;">
                        <div v-for="ref in paperRefs" :key="ref.id || ref.ref_index"
                             class="flex items-start gap-3 p-2 rounded-lg text-sm group"
                             style="border: 1px solid var(--lb-hairline); transition: background .12s;"
                             onmouseover="this.style.background='var(--lb-bg-soft)'"
                             onmouseout="this.style.background='transparent'">
                            <span class="lb-mono flex-shrink-0" style="font-size: 11px; color: var(--lb-mute); margin-top: 2px; min-width: 24px; text-align: right;">[{{ ref.ref_index }}]</span>
                            <div class="flex-1 min-w-0">
                                <p class="font-medium leading-snug" style="color: var(--lb-ink); font-family: var(--lb-font-serif); font-size: 14px;">
                                    <router-link v-if="ref.matched_paper_id" :to="'/paper/' + ref.matched_paper_id"
                                                 style="color: var(--lb-accent-ink); text-decoration: none;">{{ ref.title || $t('papers.untitled') }}</router-link>
                                    <span v-else>{{ ref.title || $t('papers.untitled') }}</span>
                                </p>
                                <p class="text-xs mt-0.5" style="color: var(--lb-ink-3);">
                                    <span v-if="ref.authors" style="font-style: italic;">{{ ref.authors }}</span>
                                    <span v-if="ref.year"> ({{ ref.year }})</span>
                                    <span v-if="ref.journal"> &middot; {{ ref.journal }}</span>
                                </p>
                                <p v-if="ref.doi" class="lb-mono text-xs mt-0.5">
                                    <a :href="'https://doi.org/' + ref.doi" target="_blank" style="color: var(--lb-accent-ink);">{{ ref.doi }}</a>
                                </p>
                                <p v-else-if="ref.title" class="text-xs mt-0.5">
                                    <a :href="refScholarUrl(ref)" target="_blank" style="color: var(--lb-accent-ink);" :title="$t('refs.scholarTitle')">{{ $t('refs.scholarLink') }} &#8599;</a>
                                </p>
                            </div>
                            <div class="flex items-center gap-2 flex-shrink-0">
                                <span v-if="ref.matched_paper_id" class="lb-tag lb-tag-cat" style="font-size: 10px;" :title="$t('refs.matchTooltip', { percent: Math.round((ref.match_confidence || 0) * 100) })">{{ $t('refs.inLibrary') }}</span>
                                <span v-else-if="ref.doi" class="lb-tag" style="font-size: 10px;">DOI</span>
                                <span v-else class="lb-tag" style="font-size: 10px; color: var(--lb-mute);">{{ $t('refs.external') }}</span>
                                <button @click="startEditRef(ref)" class="opacity-0 group-hover:opacity-100 transition-all" style="color: var(--lb-mute);" :title="$t('common.edit')">
                                    <span v-html="icons.edit"></span>
                                </button>
                                <button @click="deleteRef(ref)" class="opacity-0 group-hover:opacity-100 transition-all" style="color: var(--lb-mute);" :title="$t('common.delete')">
                                    <span v-html="icons.trash"></span>
                                </button>
                            </div>
                        </div>
                    </div>
                    <p v-else-if="!extractingRefs" class="text-sm" style="color: var(--lb-mute);">
                        Noch keine Referenzen extrahiert. Ueber das Menue &laquo;Referenzen extrahieren&raquo; ausfuehren.
                    </p>
                </section>

                </main>

                <!-- ============ ASIDE (metadata column, own scroll region) ============ -->
                <aside class="lb-modal-aside" data-testid="metadata-column">
                    <!-- OCR Result Banner — sits with the metadata it describes
                         instead of pushing the content column down. -->
                    <div v-if="ocrResult" class="lb-aside-banner" style="background: var(--lb-bg-soft);">
                        <button @click="ocrResult = null" class="absolute top-2 right-2 opacity-50 hover:opacity-100" style="color: var(--lb-mute);">
                            <span v-html="icons.x"></span>
                        </button>
                        <p class="font-medium mb-1" style="color: var(--lb-ink);">{{ ocrResult._ocr_chars > 0 ? $t('ocr.completed') : $t('ocr.noResult') }}</p>
                        <p v-if="ocrResult._ocr_chars > 0" style="color: var(--lb-ink-2);">
                            {{ $t('ocr.charsExtracted', { count: ocrResult._ocr_chars }) }}
                            <span v-if="ocrResult._ocr_searchable"> &middot; {{ $t('ocr.searchable') }}</span>
                            <span v-if="ocrResult._ocr_llm && Object.keys(ocrResult._ocr_llm).length"> &middot; {{ $t('ocr.llmUpdated') }}</span>
                        </p>
                        <p v-else style="color: var(--lb-ink-2);">{{ $t('ocr.noText') }}</p>
                    </div>

                    <!-- Validate Result Banner -->
                    <div v-if="validateResult" class="lb-aside-banner" style="background: var(--lb-accent-soft);">
                        <button @click="validateResult = null" class="absolute top-2 right-2 opacity-50 hover:opacity-100" style="color: var(--lb-accent-ink);">
                            <span v-html="icons.x"></span>
                        </button>
                        <p class="font-medium mb-1" style="color: var(--lb-accent-ink);">{{ $t('validate.done') }}</p>
                        <p style="color: var(--lb-accent-ink);">
                            {{ $t('chat.source') }}: {{ validateResult._validate_source }}
                            <span v-if="validateResult._validate_changes && Object.keys(validateResult._validate_changes).length">
                                &middot; {{ $t('validate.fieldsUpdated', { count: Object.keys(validateResult._validate_changes).length }) }}
                            </span>
                        </p>
                    </div>

                    <div class="lb-aside-block">
                        <h5 class="lb-aside-h">{{ $t('detail.metadata') }}</h5>
                        <dl class="lb-aside-dl">
                            <dt>{{ $t('common.year') }}</dt><dd>{{ paper.year || '&mdash;' }}</dd>
                            <dt>Journal</dt><dd>{{ paper.journal || '&mdash;' }}</dd>
                            <dt>{{ $t('common.publisher') }}</dt><dd>{{ paper.publisher || '&mdash;' }}</dd>
                            <dt>DOI</dt><dd class="lb-mono lb-truncate" :title="paper.doi">{{ paper.doi || '&mdash;' }}</dd>
                            <dt>ISBN</dt><dd class="lb-mono lb-truncate" :title="paper.isbn">{{ paper.isbn || '&mdash;' }}</dd>
                            <dt>{{ $t('common.citations') }}</dt><dd>{{ paper.cited_by_count != null ? paper.cited_by_count : '&mdash;' }}</dd>
                            <dt>{{ $t('detail.citeKey') }}</dt>
                            <dd v-if="!editingCiteKey" class="lb-mono lb-truncate" :title="paper.cite_key">
                                {{ paper.cite_key || '&mdash;' }}
                                <button @click="startEditCiteKey" class="text-xs ml-1" style="color: var(--lb-mute); cursor: pointer;" :title="$t('detail.editCiteKey')">&#9998;</button>
                            </dd>
                            <dd v-else>
                                <input v-model="citeKeyDraft" @keyup.enter="saveCiteKey" @keydown.esc.stop="editingCiteKey = false"
                                       class="lb-input lb-mono" style="font-size: 12px; padding: 2px 6px; width: 100%;"
                                       :disabled="citeKeySaving" ref="citeKeyInput" />
                                <div class="flex gap-1 mt-1">
                                    <button @click="saveCiteKey" :disabled="citeKeySaving" class="lb-tag" style="cursor:pointer;">{{ citeKeySaving ? '...' : 'OK' }}</button>
                                    <button @click="editingCiteKey = false" class="lb-tag" style="cursor:pointer;">{{ $t('common.cancel') }}</button>
                                </div>
                                <p v-if="citeKeyError" class="text-xs mt-1" style="color: var(--lb-danger);">{{ citeKeyError }}</p>
                            </dd>
                        </dl>
                    </div>

                    <div class="lb-aside-block">
                        <div class="flex items-center justify-between">
                            <h5 class="lb-aside-h">{{ $t('sidebar.categories') }}</h5>
                            <div class="relative">
                                <button @click="showCatAdd = !showCatAdd"
                                        class="lb-modal-close" style="width:22px;height:22px;font-weight:600;" :title="$t('categories.add')">+</button>
                                <div v-if="showCatAdd" class="absolute right-0 top-full mt-1 w-56 z-30 p-2"
                                     style="background: var(--lb-bg-elev); border: 1px solid var(--lb-hairline); border-radius: 8px; box-shadow: var(--lb-shadow-lg);">
                                    <select v-model="selectedCategoryId" @change="if(selectedCategoryId){addCategory(); showCatAdd=false;}" class="lb-input" style="font-size: 12px; padding: 6px 8px;">
                                        <option value="">{{ $t('categories.choose') }}</option>
                                        <option v-for="c in availableCategories" :key="c.id" :value="c.id">{{ c.name }}</option>
                                    </select>
                                </div>
                                <div v-if="showCatAdd" class="fixed inset-0 z-20" @click="showCatAdd=false"></div>
                            </div>
                        </div>
                        <div class="lb-aside-tags">
                            <button v-for="cat in paper.categories" :key="cat.id" class="lb-tag lb-tag-cat" style="cursor:pointer;" @click="removeCategory(cat.id)" :title="$t('categories.removeTooltip', { name: cat.name })">
                                {{ cat.name }} <span style="opacity:.5;">&times;</span>
                            </button>
                            <span v-if="!paper.categories || !paper.categories.length" class="text-xs italic" style="color: var(--lb-mute);">{{ $t('common.none') }}</span>
                        </div>
                    </div>

                    <!-- Add-on slot "item-detail-aside" (#190): keyed on the Item id,
                         so navigating to another Item remounts the component. -->
                    <div v-for="s in detailAsideSlots" :key="s.key + ':' + paper.id"
                         class="lb-aside-block" :data-addon-slot="s.key">
                        <component :is="s.component" :item-id="paper.id" :cite-key="paper.cite_key || ''" />
                    </div>
                </aside>
            </div>

                <!-- PDF-Verarbeitungsoptionen (nach Dateiauswahl, vor Upload) -->
                <div v-if="showAttachModal" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center" @click.self="cancelAttach">
                    <div class="bg-white rounded-xl shadow-xl p-6 max-w-md w-full mx-4">
                        <h3 class="text-lg font-semibold text-gray-900 mb-1">{{ $t('attach.heading') }}</h3>
                        <p class="text-sm text-gray-500 mb-4 truncate" :title="pendingPdfName">{{ pendingPdfName }}</p>
                        <div class="space-y-2 mb-5">
                            <label class="flex items-start gap-2.5 cursor-pointer">
                                <input type="checkbox" v-model="attachOptions.ocr" class="mt-0.5" :disabled="attachBusy" />
                                <span>
                                    <span class="block text-sm font-medium text-gray-900">{{ $t('attach.ocr') }}</span>
                                    <span class="block text-xs text-gray-500">{{ $t('attach.ocrHint') }}</span>
                                </span>
                            </label>
                            <label class="flex items-start gap-2.5 cursor-pointer">
                                <input type="checkbox" v-model="attachOptions.categories" class="mt-0.5" :disabled="attachBusy" />
                                <span>
                                    <span class="block text-sm font-medium text-gray-900">{{ $t('attach.categorize') }}</span>
                                    <span class="block text-xs text-gray-500">{{ $t('attach.categorizeHint') }}</span>
                                </span>
                            </label>
                            <label class="flex items-start gap-2.5 cursor-pointer">
                                <input type="checkbox" v-model="attachOptions.chunks" class="mt-0.5" :disabled="attachBusy" />
                                <span>
                                    <span class="block text-sm font-medium text-gray-900">{{ $t('attach.chunks') }}</span>
                                    <span class="block text-xs text-gray-500">{{ $t('attach.chunksHint') }}</span>
                                </span>
                            </label>
                            <label class="flex items-start gap-2.5 cursor-pointer">
                                <input type="checkbox" v-model="attachOptions.references" class="mt-0.5" :disabled="attachBusy" />
                                <span>
                                    <span class="block text-sm font-medium text-gray-900">{{ $t('refs.extract') }}</span>
                                    <span class="block text-xs text-gray-500">{{ $t('attach.referencesHint') }}</span>
                                </span>
                            </label>
                        </div>
                        <p v-if="attachBusy" class="text-sm text-gray-600 mb-3">{{ attachStatus || $t('attach.processing') }}</p>
                        <div class="flex justify-end gap-2">
                            <button @click="cancelAttach" :disabled="attachBusy" class="lb-btn lb-btn-ghost">{{ $t('common.cancel') }}</button>
                            <button @click="confirmAttach" :disabled="attachBusy" class="lb-btn lb-btn-primary">
                                {{ attachBusy ? $t('common.running') : $t('common.start') }}
                            </button>
                        </div>
                    </div>
                </div>

                <!-- Buch erkannt: auf Kapitel zuschneiden (Cover bleibt) -->
                <div v-if="showTrimModal" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center">
                    <div class="bg-white rounded-xl shadow-xl p-6 max-w-md w-full mx-4">
                        <h3 class="text-lg font-semibold text-gray-900 mb-1">{{ $t('attach.bookDetected') }}</h3>
                        <p class="text-sm text-gray-600 mb-4">
                            {{ $t('attach.bookHintBefore') }} <strong>{{ bookInfo && bookInfo.page_count }}</strong> {{ $t('attach.bookHintAfter') }}
                        </p>
                        <div v-if="bookInfo && bookInfo.range" class="mb-4 p-3 rounded-lg text-sm" style="background: var(--lb-bg-soft); border: 1px solid var(--lb-hairline);">
                            <p style="color: var(--lb-ink-2);">
                                {{ $t('attach.suggestedChapter') }}
                                <strong v-if="bookInfo.printed_start">{{ $t('papers.pagesAbbr') }} {{ bookInfo.printed_start }}&ndash;{{ bookInfo.printed_end }}</strong>
                                <strong v-else>{{ $t('attach.chapterPages', { count: bookInfo.chapter_pages }) }}</strong>
                                &middot; Cover + {{ bookInfo.chapter_pages }} Seiten
                            </p>
                            <p class="text-xs mt-1" style="color: var(--lb-mute);">
                                {{ $t('attach.detection', { method: bookInfo.method }) }} ({{ bookInfo.confidence === 'high' ? $t('attach.detectionSure') : $t('attach.detectionUnsure') }})
                            </p>
                        </div>
                        <p v-else class="mb-3 text-sm" style="color: var(--lb-mute);">
                            {{ $t('attach.chapterPagesUnknown') }}
                        </p>
                        <div class="flex items-end gap-3 mb-3">
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">{{ $t('attach.pageFrom') }}</label>
                                <input v-model.number="trimStart" type="number" min="1" :disabled="attachBusy"
                                       class="w-24 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">{{ $t('attach.pageTo') }}</label>
                                <input v-model.number="trimEnd" type="number" min="1" :disabled="attachBusy"
                                       class="w-24 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                            <label class="flex items-center gap-1.5 text-sm text-gray-700 pb-2 cursor-pointer">
                                <input type="checkbox" v-model="trimKeepCover" :disabled="attachBusy" /> {{ $t('attach.keepCover') }}
                            </label>
                        </div>
                        <p v-if="attachBusy" class="text-sm text-gray-600 mb-3">{{ attachStatus || $t('attach.processing') }}</p>
                        <div class="flex justify-end gap-2">
                            <button @click="keepWholeBook" :disabled="attachBusy" class="lb-btn lb-btn-ghost">{{ $t('attach.keepWholeBook') }}</button>
                            <button @click="trimAndAttach" :disabled="attachBusy" class="lb-btn lb-btn-primary">{{ $t('attach.trim') }}</button>
                        </div>
                    </div>
                </div>

                <!-- SSE Extraction Progress Modal -->
                <div v-if="extractingRefs" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center">
                    <div class="bg-white rounded-xl shadow-xl p-6 max-w-lg w-full mx-4">
                        <h3 class="text-lg font-semibold text-gray-900 mb-4">{{ $t('refs.extract') }}</h3>
                        <div class="mb-4">
                            <div class="w-full bg-gray-200 rounded-full h-3 mb-2">
                                <div class="bg-accent h-3 rounded-full transition-all duration-300" :style="{ width: refProgress.percent + '%' }"></div>
                            </div>
                            <p class="text-sm text-gray-600">{{ refProgress.message || $t('common.starting') }}</p>
                            <p v-if="refProgress.current && refProgress.total" class="text-xs text-gray-400 mt-1">
                                {{ $t('refs.progress', { current: refProgress.current, total: refProgress.total }) }}<span v-if="refProgress.found != null"> &middot; {{ $t('refs.progressLoaded', { found: refProgress.found, current: refProgress.current }) }}</span>
                            </p>
                        </div>
                        <div v-if="refProgress.error" class="p-3 bg-accent-soft border border-accent-soft rounded-lg text-sm text-accent-ink mb-3">
                            {{ refProgress.error }}
                        </div>
                    </div>
                </div>

                <!-- Edit Reference Modal -->
                <div v-if="editingRef" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center">
                    <div class="bg-white rounded-xl shadow-xl p-6 max-w-lg w-full mx-4">
                        <h3 class="text-lg font-semibold text-gray-900 mb-4">{{ $t('refs.editHeading') }}</h3>
                        <div class="space-y-3">
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">{{ $t('common.title') }}</label>
                                <input v-model="editRefData.title" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">{{ $t('common.authors') }}</label>
                                <input v-model="editRefData.authors" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                            <div class="grid grid-cols-2 gap-3">
                                <div>
                                    <label class="block text-xs text-gray-500 mb-1">{{ $t('common.year') }}</label>
                                    <input v-model.number="editRefData.year" type="number" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                                </div>
                                <div>
                                    <label class="block text-xs text-gray-500 mb-1">DOI</label>
                                    <input v-model="editRefData.doi" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                                </div>
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Journal</label>
                                <input v-model="editRefData.journal" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                        </div>
                        <div class="flex justify-end gap-2 mt-5">
                            <button @click="editingRef = null" class="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">{{ $t('common.cancel') }}</button>
                            <button @click="saveReference" class="px-4 py-2 text-sm text-white bg-accent rounded-lg hover:bg-accent-ink">{{ $t('common.save') }}</button>
                        </div>
                    </div>
                </div>

                <!-- Add Reference Modal -->
                <div v-if="addingRef" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center">
                    <div class="bg-white rounded-xl shadow-xl p-6 max-w-lg w-full mx-4">
                        <h3 class="text-lg font-semibold text-gray-900 mb-4">{{ $t('refs.addHeading') }}</h3>
                        <div class="space-y-3">
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">{{ $t('common.title') }} *</label>
                                <input v-model="newRefData.title" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">{{ $t('common.authors') }}</label>
                                <input v-model="newRefData.authors" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                            <div class="grid grid-cols-2 gap-3">
                                <div>
                                    <label class="block text-xs text-gray-500 mb-1">{{ $t('common.year') }}</label>
                                    <input v-model.number="newRefData.year" type="number" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                                </div>
                                <div>
                                    <label class="block text-xs text-gray-500 mb-1">DOI</label>
                                    <input v-model="newRefData.doi" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                                </div>
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Journal</label>
                                <input v-model="newRefData.journal" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                        </div>
                        <div class="flex justify-end gap-2 mt-5">
                            <button @click="addingRef = false" class="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">{{ $t('common.cancel') }}</button>
                            <button @click="saveNewReference" :disabled="!newRefData.title" class="px-4 py-2 text-sm text-white bg-accent rounded-lg hover:bg-accent-ink disabled:opacity-40">{{ $t('common.add') }}</button>
                        </div>
                    </div>
                </div>

                <!-- Metadata Validation Proposal Modal (HITL review) -->
                <div v-if="validateProposal" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center" @click.self="closeProposal">
                    <div class="bg-white rounded-xl shadow-xl max-w-2xl w-full mx-4 max-h-[90vh] flex flex-col">
                        <div class="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
                            <h3 class="text-lg font-semibold text-gray-900">{{ $t('validate.heading') }}</h3>
                            <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium"
                                  :class="validateProposal.confidence === 'high' ? 'bg-accent-soft text-accent-ink' : 'bg-accent-soft text-accent-ink'">
                                {{ validateProposal.confidence === 'high' ? $t('validate.confidenceHigh') : $t('validate.confidenceLow') }}
                            </span>
                        </div>

                        <div class="px-6 py-4 overflow-y-auto flex-1 space-y-4">
                            <!-- Warnings -->
                            <div v-if="validateProposal.warnings.length" class="bg-accent-soft border border-accent-soft rounded-lg p-3">
                                <p class="font-medium text-accent-ink text-sm mb-1">{{ $t('validate.notes') }}</p>
                                <ul class="list-disc list-inside text-sm text-accent-ink space-y-0.5">
                                    <li v-for="(w, i) in validateProposal.warnings" :key="'w'+i">{{ w }}</li>
                                </ul>
                            </div>

                            <!-- Field changes -->
                            <div v-if="proposalFieldList.length">
                                <p class="font-medium text-gray-700 text-sm mb-2">{{ $t('validate.fieldChanges') }}</p>
                                <div class="space-y-3">
                                    <div v-for="f in proposalFieldList" :key="f.field" class="border border-gray-200 rounded-lg p-3">
                                        <div class="flex items-center justify-between mb-1">
                                            <label class="inline-flex items-center gap-2 text-sm font-medium text-gray-700">
                                                <input type="checkbox" v-model="proposalAccept[f.field]" class="w-4 h-4 rounded border-gray-300 text-accent focus:ring-accent" />
                                                {{ f.label }}
                                            </label>
                                            <span class="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 font-medium">{{ f.source }}</span>
                                        </div>
                                        <div class="text-xs text-gray-500 mb-1">
                                            {{ $t('validate.current') }}: <span class="line-through">{{ f.current === null || f.current === '' ? $t('validate.empty') : f.current }}</span>
                                        </div>
                                        <textarea v-if="f.field === 'abstract'" v-model="proposalEdits[f.field]"
                                                  rows="4"
                                                  class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none"></textarea>
                                        <input v-else v-model="proposalEdits[f.field]"
                                               class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                                    </div>
                                </div>
                            </div>
                            <div v-else class="text-sm text-gray-500 italic">{{ $t('validate.noFieldChanges') }}</div>

                            <!-- Category suggestions -->
                            <div v-if="validateProposal.category_suggestions.length">
                                <p class="font-medium text-gray-700 text-sm mb-2">{{ $t('validate.categoryProposals') }}</p>
                                <div class="space-y-1.5">
                                    <label v-for="(cat, i) in validateProposal.category_suggestions" :key="'c'+i"
                                           class="flex items-center justify-between gap-2 text-sm text-gray-700 border border-gray-200 rounded-lg px-3 py-2">
                                        <div class="flex items-center gap-2">
                                            <input type="checkbox" v-model="proposalCategoriesAccept[cat.category_id]" class="w-4 h-4 rounded border-gray-300 text-accent focus:ring-accent" />
                                            <span>{{ proposalCategoryName(cat.category_id) }}</span>
                                        </div>
                                        <span class="text-xs text-gray-500">{{ Math.round((cat.confidence || 0) * 100) }}%</span>
                                    </label>
                                </div>
                            </div>
                        </div>

                        <div class="px-6 py-4 border-t border-gray-100 flex justify-end gap-2">
                            <button @click="closeProposal" class="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">{{ $t('common.cancel') }}</button>
                            <button @click="applyProposal" :disabled="applyingProposal" class="px-4 py-2 text-sm text-white bg-accent rounded-lg hover:bg-accent-ink disabled:opacity-40">
                                {{ applyingProposal ? 'Speichere...' : 'Uebernehmen' }}
                            </button>
                        </div>
                    </div>
                </div>

            </template>
        </div>
        </div>
        </div>
    `,
    data() {
        return {
            paper: null,
            allCategories: [],
            selectedCategoryId: '',
            loading: true,
            icons,
            validating: false,
            validateResult: null,
            validateProposal: null,
            proposalAccept: {},
            proposalEdits: {},
            proposalCategoriesAccept: {},
            applyingProposal: false,
            ocrRunning: false,
            ocrResult: null,
            editingMeta: false,
            editMeta: {},
            customValues: {},
            savingCustom: false,
            // Notizen (#160): Ansicht/Bearbeiten + Autosave-Status
            notesEditing: false,
            notesDraft: '',
            notesSaveState: 'idle',   // idle | dirty | saving | saved | error
            extractingRefs: false,
            generatingAbstract: false,
            actionMenuOpen: false,
            bibtexCopied: false,
            // Metadaten-Spalte im Zwei-Spalten-Band (1100-1440px). Darueber und
            // darunter entscheidet das Stylesheet, der Schalter ist dort weg.
            metaOpen: false,
            attachingPdf: false,
            // PDF-Verarbeitungsoptionen (Modal nach Dateiauswahl)
            showAttachModal: false,
            pendingPdfFile: null,
            pendingPdfName: '',
            attachBusy: false,
            attachStatus: '',
            attachOptions: { ocr: true, categories: true, chunks: true, references: true },
            // Buch-Zuschnitt (zweite Phase, nach Buch-Erkennung)
            showTrimModal: false,
            bookInfo: null,
            trimStart: null,
            trimEnd: null,
            trimKeepCover: true,
            fetchingOa: false,
            editingCiteKey: false,
            citeKeyDraft: '',
            citeKeySaving: false,
            citeKeyError: '',
            showCatAdd: false,
            refExtractionResult: null,
            paperRefs: [],
            pdfPage: null,
            refProgress: { percent: 0, message: '', current: 0, total: 0, found: null, error: null },
            editingRef: null,
            editRefData: { title: '', authors: '', year: null, doi: '', journal: '' },
            addingRef: false,
            newRefData: { title: '', authors: '', year: null, doi: '', journal: '' },
        };
    },
    computed: {
        // Active Add-ons' contributions to the metadata column (#190).
        detailAsideSlots() {
            return addonSlots('item-detail-aside');
        },
        availableCategories() {
            if (!this.paper || !this.allCategories) return [];
            const assigned = new Set((this.paper.categories || []).map(c => c.id));
            return this.allCategories.filter(c => !assigned.has(c.id));
        },
        scholarUrl() {
            if (!this.paper) return '';
            const q = (this.paper.title || this.paper.doi || '').trim();
            return 'https://scholar.google.com/scholar?q=' + encodeURIComponent(q);
        },
        notesHtml() {
            const notes = this.paper && this.paper.notes;
            return notes ? renderMarkdownSafe(notes)
                         : '<p class="lb-notes-empty">' + t('notes.empty') + '</p>';
        },
        notesStatus() {
            return {
                dirty: t('notes.unsaved'), saving: t('notes.saving'),
                saved: t('notes.saved'), error: t('notes.saveFailed'),
            }[this.notesSaveState] || '';
        },
        proposalFieldList() {
            if (!this.validateProposal) return [];
            const labels = {
                title: t('common.title'), authors: t('common.authors'),
                year: t('common.year'), doi: 'DOI', isbn: 'ISBN',
                abstract: t('common.abstract'), journal: t('common.journal'),
                publisher: t('common.publisher'),
            };
            return Object.keys(this.validateProposal.changes).map(f => ({
                field: f,
                label: labels[f] || f,
                current: this.validateProposal.current[f],
                source: this.validateProposal.source_per_field[f] || 'unbekannt',
            }));
        },
    },
    async created() {
        await this.load();
    },
    mounted() {
        this._escHandler = (e) => {
            if (e.key !== 'Escape') return;
            // Don't swallow Esc when a nested overlay is open — let it dismiss that first.
            if (this.validateProposal || this.editingRef || this.addingRef || this.extractingRefs || this.editingCiteKey) return;
            this.close();
        };
        window.addEventListener('keydown', this._escHandler);
        // Focus mode (#161): the sidebar folds to its icon rail while a paper is
        // open and unfolds again on close. The sidebar owns that decision — and
        // never writes the stored preference for it.
        window.dispatchEvent(new CustomEvent('lb-focus-mode', { detail: { on: true } }));
    },
    unmounted() {
        if (this._escHandler) window.removeEventListener('keydown', this._escHandler);
        this.teardownNotes();
        window.dispatchEvent(new CustomEvent('lb-focus-mode', { detail: { on: false } }));
    },
    watch: {
        '$route'() { this.load(); },
        // Das Metadaten-Formular ersetzt den Notiz-Block im DOM. Vorher die
        // offene Notiz sichern und den Editor abbauen, nachher wieder in den
        // Oeffnungszustand gehen.
        editingMeta(on) {
            if (on) this.teardownNotes();
            else this.$nextTick(() => this.openNotes());
        },
    },
    methods: {
        close() {
            // Slide-in modal close: return to the list. Fallback to '/' if history is empty.
            if (window.history.length > 1) {
                this.$router.back();
            } else {
                this.$router.push('/');
            }
        },
        startEditCiteKey() {
            this.citeKeyDraft = this.paper.cite_key || '';
            this.citeKeyError = '';
            this.editingCiteKey = true;
            this.$nextTick(() => { if (this.$refs.citeKeyInput) this.$refs.citeKeyInput.focus(); });
        },
        async saveCiteKey() {
            if (this.citeKeySaving) return;
            this.citeKeySaving = true;
            this.citeKeyError = '';
            try {
                const res = await api(`/api/papers/${this.paper.id}/cite-key`, {
                    method: 'PUT',
                    body: JSON.stringify({ cite_key: this.citeKeyDraft }),
                });
                this.paper.cite_key = res.cite_key;
                this.editingCiteKey = false;
            } catch (e) {
                this.citeKeyError = e.message;
            } finally {
                this.citeKeySaving = false;
            }
        },
        // --- Notizen (#160) ------------------------------------------------
        // Der Editor ist CodeMirror 5 (Markdown-Mode + continuelist-Addon) auf
        // dem Textarea darunter. Fehlt das CDN, bleibt das Textarea selbst der
        // Editor — eine Notiz zu schreiben soll an keiner Bibliothek haengen.
        openNotes() {
            // Paper ohne Notiz oeffnet direkt im Editor, Paper mit Notiz
            // gerendert. Schreiben soll keinen zusaetzlichen Klick kosten.
            if (this.paper && !(this.paper.notes || '').trim()) this.editNotes();
        },
        editNotes() {
            if (this.notesEditing || this.editingMeta) return;
            // Den Entwurf nur dann am gespeicherten Stand ausrichten, wenn
            // nichts Ungesichertes darin steht: nach einem fehlgeschlagenen
            // Save wuerde ein Wechsel Ansicht -> Bearbeiten sonst genau den
            // Text loeschen, der noch nirgends angekommen ist.
            if (this.notesSaveState === 'idle' || this.notesSaveState === 'saved') {
                this.notesDraft = (this.paper && this.paper.notes) || '';
            }
            this.notesEditing = true;
            this.$nextTick(() => this.mountNotesEditor());
        },
        showNotes() {
            this.teardownNotes();
        },
        mountNotesEditor() {
            const textarea = this.$refs.notesInput;
            if (!textarea) return;
            if (typeof CodeMirror === 'undefined' || !CodeMirror.fromTextArea) {
                textarea.focus();
                return;
            }
            this._notesCm = CodeMirror.fromTextArea(textarea, {
                mode: 'markdown',
                lineWrapping: true,
                viewportMargin: Infinity,
                extraKeys: {
                    // Aus dem continuelist-Addon: Enter in einer Liste setzt
                    // die Liste fort, statt den Marker neu tippen zu lassen.
                    Enter: 'newlineAndIndentContinueMarkdownList',
                    'Ctrl-B': () => this.wrapNotesSelection('**'),
                    'Ctrl-I': () => this.wrapNotesSelection('*'),
                },
            });
            this._notesCm.on('change', () => this.onNotesTyped(this._notesCm.getValue()));
            this._notesCm.focus();
        },
        wrapNotesSelection(marker) {
            const cm = this._notesCm;
            if (!cm) return;
            const selected = cm.getSelection();
            cm.replaceSelection(marker + selected + marker);
            if (!selected) {
                // Ohne Auswahl gehoert der Cursor zwischen die Marker, sonst
                // tippt man hinter das schliessende Sternchen weiter.
                const at = cm.getCursor();
                cm.setCursor({ line: at.line, ch: at.ch - marker.length });
            }
            cm.focus();
        },
        onNotesTyped(value) {
            if (value !== undefined) this.notesDraft = value;
            this.notesSaveState = 'dirty';
            clearTimeout(this._notesTimer);
            this._notesTimer = setTimeout(() => this.saveNotes(), 800);
        },
        saveNotes() {
            clearTimeout(this._notesTimer);
            if (!this.paper) return Promise.resolve();
            return this.persistNotes(this.paper.id, this.notesDraft);
        },
        // Schreibt genau diesen Text fuer genau dieses Paper. Laeuft schon ein
        // Request, wird der neuere Stand vorgemerkt statt verworfen, und er
        // traegt seine paper_id mit sich: sonst verliert ein Paperwechsel
        // mitten im Request die zuletzt getippten Zeichen.
        async persistNotes(paperId, value) {
            if (this._notesSaving) {
                this._notesPending = { paperId, value };
                return;
            }
            const stillOpen = () => !!this.paper && this.paper.id === paperId;
            if (stillOpen() && value === (this.paper.notes || '')) {
                if (this.notesSaveState === 'dirty') this.markNotesSaved();
                return;
            }
            if (stillOpen()) this.notesSaveState = 'saving';
            this._notesSaving = true;
            try {
                const res = await api(`/api/papers/${paperId}/notes`, {
                    method: 'PUT',
                    body: JSON.stringify({ notes: value }),
                });
                if (stillOpen()) {
                    this.paper.notes = res.notes;
                    // Waehrend des Requests weitergetippt? Dann ist es noch
                    // nicht gesichert, und der Nachlauf unten holt es nach.
                    if (this.notesDraft === res.notes) this.markNotesSaved();
                    else this.notesSaveState = 'dirty';
                }
            } catch (e) {
                console.error('notes.saveFailed', e);
                if (stillOpen()) this.notesSaveState = 'error';
            } finally {
                this._notesSaving = false;
            }
            const pending = this._notesPending;
            this._notesPending = null;
            if (pending) return this.persistNotes(pending.paperId, pending.value);
            if (stillOpen() && this.notesSaveState === 'dirty') {
                return this.persistNotes(paperId, this.notesDraft);
            }
        },
        markNotesSaved() {
            // Die Bestaetigung ist ein Signal, kein Dauerzustand: nach ein
            // paar Sekunden verschwindet sie wieder.
            this.notesSaveState = 'saved';
            clearTimeout(this._notesSavedTimer);
            this._notesSavedTimer = setTimeout(() => {
                if (this.notesSaveState === 'saved') this.notesSaveState = 'idle';
            }, 2500);
        },
        teardownNotes() {
            // Offene Aenderung sichern (fire-and-forget), dann den Editor loesen.
            clearTimeout(this._notesSavedTimer);
            if (this.notesEditing) this.saveNotes();
            if (this._notesCm) {
                this._notesCm.toTextArea();
                this._notesCm = null;
            }
            this.notesEditing = false;
        },
        async load() {
            this.teardownNotes();
            this.loading = true;
            this.validateResult = null;
            this.validateProposal = null;
            this.editingMeta = false;
            this.pdfPage = this.$route.query.page ? parseInt(this.$route.query.page) : null;
            try {
                const id = this.$route.params.id;
                const [paper, categories] = await Promise.all([
                    api(`/api/papers/${id}`),
                    api('/api/categories'),
                ]);
                this.paper = paper;
                this.allCategories = categories;
                // Initialize custom values
                this.customValues = {};
                if (paper.custom_fields) {
                    for (const cf of paper.custom_fields) {
                        this.customValues[cf.field_id] = cf.value || (cf.field_type === 'progress' ? 0 : '');
                    }
                }
                this.notesDraft = paper.notes || '';
                this.notesSaveState = 'idle';
                this.$nextTick(() => this.openNotes());
            } catch (e) {
                console.error('Load paper error:', e);
            }
            this.loading = false;
            // Load references in background
            this.loadReferences();
        },
        async openInApp() {
            try {
                await api(`/api/papers/${this.paper.id}/open`, { method: 'POST' });
            } catch (e) {
                alert(t('error.openFailed', { message: e.message }));
            }
        },
        onPdfSelected(event) {
            // Datei merken und Optionen-Modal oeffnen (Verarbeitung erst nach Bestaetigung).
            const file = event.target.files[0];
            event.target.value = '';
            if (!file || this.attachingPdf) return;
            this.pendingPdfFile = file;
            this.pendingPdfName = file.name || 'PDF';
            this.attachOptions = { ocr: true, categories: true, chunks: true, references: true };
            this.attachStatus = '';
            this.attachBusy = false;
            this.showAttachModal = true;
        },
        cancelAttach() {
            if (this.attachBusy) return;
            this.showAttachModal = false;
            this.pendingPdfFile = null;
            this.pendingPdfName = '';
        },
        async confirmAttach() {
            // Phase 1 des zweiphasigen Uploads: PDF landen (ohne Verarbeitung)
            // und auf Buch pruefen. Bei erkanntem Buch -> Zuschneide-Modal,
            // sonst direkt finalisieren.
            if (!this.pendingPdfFile || this.attachBusy) return;
            this.attachBusy = true;
            this.attachingPdf = true;
            try {
                this.attachStatus = t('attach.attaching');
                const formData = new FormData();
                formData.append('file', this.pendingPdfFile);
                const params = new URLSearchParams({
                    do_categories: 'false', do_chunks: 'false', detect_book: 'true',
                });
                const resp = await fetch(`/api/papers/${this.paper.id}/attach-pdf?${params}`, {
                    method: 'POST', body: formData,
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail || 'HTTP ' + resp.status);
                }
                const data = await resp.json();
                this.pendingPdfFile = null;
                await this.load();
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));

                if (data.book && data.book.detected) {
                    // Vorschau/Bestaetigung: Zuschneide-Modal oeffnen.
                    this.bookInfo = data.book;
                    const r = data.book.range;
                    this.trimStart = r ? r.start_page : null;
                    this.trimEnd = r ? r.end_page : null;
                    this.trimKeepCover = r ? r.keep_cover !== false : true;
                    this.showAttachModal = false;
                    this.showTrimModal = true;
                    this.attachBusy = false;
                    this.attachingPdf = false;
                    this.attachStatus = '';
                    return;
                }
                // Kein Buch -> direkt fertig verarbeiten.
                this.showAttachModal = false;
                await this.finalizeAttach(null);
            } catch (e) {
                alert(t('pdf.attachFailed', { message: e.message }));
                this.attachBusy = false;
                this.attachingPdf = false;
                this.attachStatus = '';
            }
        },
        keepWholeBook() {
            // Buch ganz behalten (kein Zuschnitt), trotzdem normal verarbeiten.
            this.showTrimModal = false;
            this.finalizeAttach(null);
        },
        trimAndAttach() {
            const start = parseInt(this.trimStart, 10);
            const end = parseInt(this.trimEnd, 10);
            if (!start || !end || end < start) {
                alert(t('attach.invalidPageRange'));
                return;
            }
            this.showTrimModal = false;
            this.finalizeAttach({ start_page: start, end_page: end, keep_cover: !!this.trimKeepCover });
        },
        async finalizeAttach(trim) {
            // Phase 2: optional zuschneiden, dann Kategorien/Chunks gemaess Wahl,
            // danach OCR/Referenzen ueber die bestehenden Endpoints nachziehen.
            this.attachBusy = true;
            this.attachingPdf = true;
            const opts = { ...this.attachOptions };
            try {
                this.attachStatus = trim ? t('attach.trimming') : t('attach.processing');
                await api(`/api/papers/${this.paper.id}/attach-finalize`, {
                    method: 'POST',
                    body: JSON.stringify({
                        trim: trim,
                        do_categories: opts.categories,
                        do_chunks: opts.chunks,
                    }),
                });
                await this.load();
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
                if (opts.ocr) {
                    await this.runOcr();
                }
                if (opts.references) {
                    await this.extractReferences();
                }
            } catch (e) {
                alert('Verarbeitung fehlgeschlagen: ' + e.message);
            } finally {
                this.attachBusy = false;
                this.attachingPdf = false;
                this.attachStatus = '';
                this.bookInfo = null;
            }
        },
        async fetchOaPdf() {
            // Versucht, ein Open-Access-PDF anhand der DOI zu finden und anzuhaengen.
            if (!this.paper || this.fetchingOa) return;
            this.fetchingOa = true;
            try {
                const resp = await fetch(`/api/papers/${this.paper.id}/fetch-oa-pdf`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: '' }),  // leer -> Server sucht OA-URL via DOI
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({ detail: 'error.noOaPdf' }));
                    throw new Error(err.detail || 'HTTP ' + resp.status);
                }
                await this.load();
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            } catch (e) {
                alert(t('pdf.oaNotFound', { message: e.message }));
            } finally {
                this.fetchingOa = false;
            }
        },
        async copyBibtex() {
            try {
                const data = await api(`/api/papers/${this.paper.id}/bibtex`);
                await navigator.clipboard.writeText(data.bibtex);
                this.bibtexCopied = true;
                setTimeout(() => { this.bibtexCopied = false; }, 2000);
            } catch (e) {
                alert(t('error.bibtexCopyFailed', { message: e.message }));
            }
        },
        async validateMetadata() {
            this.validating = true;
            this.validateResult = null;
            this.ocrResult = null;
            this.validateProposal = null;
            try {
                const proposal = await api(`/api/papers/${this.paper.id}/validate/propose`, {
                    method: 'POST',
                    body: JSON.stringify({ use_llm: true, pages: 15 }),
                });
                // Initialise the review state: every field/category accepted by
                // default, edits seeded from the proposed values.
                this.proposalAccept = {};
                this.proposalEdits = {};
                for (const k of Object.keys(proposal.changes || {})) {
                    this.proposalAccept[k] = true;
                    this.proposalEdits[k] = proposal.changes[k];
                }
                this.proposalCategoriesAccept = {};
                for (const cat of (proposal.category_suggestions || [])) {
                    if (cat && cat.category_id != null) {
                        this.proposalCategoriesAccept[cat.category_id] = true;
                    }
                }
                this.validateProposal = proposal;
            } catch (e) {
                alert('Validierung fehlgeschlagen: ' + e.message);
            }
            this.validating = false;
        },
        proposalCategoryName(catId) {
            const c = (this.allCategories || []).find(c => c.id === catId);
            return c ? c.name : t('categories.numbered', { id: catId });
        },
        closeProposal() {
            this.validateProposal = null;
            this.proposalAccept = {};
            this.proposalEdits = {};
            this.proposalCategoriesAccept = {};
        },
        async applyProposal() {
            if (!this.validateProposal) return;
            this.applyingProposal = true;
            try {
                // Collect accepted (and possibly edited) field changes.
                const acceptedChanges = {};
                for (const f of Object.keys(this.validateProposal.changes || {})) {
                    if (this.proposalAccept[f]) {
                        acceptedChanges[f] = this.proposalEdits[f];
                    }
                }
                // Collect accepted category assignments (keep original confidence).
                const acceptedCategories = (this.validateProposal.category_suggestions || [])
                    .filter(c => c && c.category_id != null && this.proposalCategoriesAccept[c.category_id])
                    .map(c => ({ category_id: c.category_id, confidence: c.confidence || 0 }));

                await api(`/api/papers/${this.paper.id}/validate/apply`, {
                    method: 'POST',
                    body: JSON.stringify({
                        changes: acceptedChanges,
                        category_assignments: acceptedCategories,
                    }),
                });

                // Surface the legacy result banner so the user gets visible feedback.
                this.validateResult = {
                    _validate_source: 'review',
                    _validate_changes: acceptedChanges,
                };
                // Reload paper data.
                this.paper = await api(`/api/papers/${this.paper.id}`);
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
                this.closeProposal();
            } catch (e) {
                alert('Uebernahme fehlgeschlagen: ' + e.message);
            }
            this.applyingProposal = false;
        },
        async runOcr() {
            this.ocrRunning = true;
            this.ocrResult = null;
            this.validateResult = null;
            try {
                const result = await api(`/api/papers/${this.paper.id}/ocr?pages=10`, {
                    method: 'POST',
                });
                this.ocrResult = result;
                // Reload paper data
                this.paper = await api(`/api/papers/${this.paper.id}`);
                // Re-init custom values
                this.customValues = {};
                if (this.paper.custom_fields) {
                    for (const cf of this.paper.custom_fields) {
                        this.customValues[cf.field_id] = cf.value || (cf.field_type === 'progress' ? 0 : '');
                    }
                }
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            } catch (e) {
                alert('OCR fehlgeschlagen: ' + e.message);
            }
            this.ocrRunning = false;
        },
        startEditMetadata() {
            this.editMeta = {
                title: this.paper.title || '',
                authors: this.paper.authors || '',
                year: this.paper.year || null,
                doi: this.paper.doi || '',
                isbn: this.paper.isbn || '',
                abstract: this.paper.abstract || '',
                journal: this.paper.journal || '',
                publisher: this.paper.publisher || '',
            };
            this.editingMeta = true;
        },
        async generateAbstract() {
            this.generatingAbstract = true;
            try {
                const resp = await fetch(`/api/papers/${this.paper.id}/generate-abstract`, { method: 'POST' });
                const data = await resp.json();
                if (data.status === 'ok' && data.abstract) {
                    this.editMeta.abstract = data.abstract;
                    this.paper.abstract = data.abstract;
                    this.paper.abstract_source = data.source;
                    const sourceLabels = { crossref: 'CrossRef', pdf: 'PDF (KI-Extraktion)', generated: 'KI-generiert' };
                    alert(`Abstract erfolgreich geladen!\nQuelle: ${sourceLabels[data.source] || data.source}`);
                } else {
                    alert(data.message ? translateDetail(data.message) : t('abstract.noneFound'));
                }
            } catch (e) {
                console.error('Abstract generation error:', e);
                alert(t('abstract.generateFailed'));
            } finally {
                this.generatingAbstract = false;
            }
        },
        async saveMetadata() {
            try {
                // Send only changed fields (including cleared fields)
                const payload = {};
                for (const [key, val] of Object.entries(this.editMeta)) {
                    const original = this.paper[key] ?? '';
                    if (String(val) !== String(original)) {
                        payload[key] = val;
                    }
                }
                if (Object.keys(payload).length === 0) {
                    this.editingMeta = false;
                    return;
                }
                this.paper = await api(`/api/papers/${this.paper.id}`, {
                    method: 'PUT',
                    body: JSON.stringify(payload),
                });
                this.editingMeta = false;
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            } catch (e) {
                alert(t('error.saveFailed', { message: e.message }));
            }
        },
        async addCategory() {
            if (!this.selectedCategoryId) return;
            try {
                await api(`/api/papers/${this.paper.id}/categories`, {
                    method: 'POST',
                    body: JSON.stringify({ category_id: parseInt(this.selectedCategoryId) }),
                });
                this.selectedCategoryId = '';
                await this.load();
                this.refreshSidebar();
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
        },
        async removeCategory(catId) {
            try {
                await api(`/api/papers/${this.paper.id}/categories/${catId}`, { method: 'DELETE' });
                await this.load();
                this.refreshSidebar();
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
        },
        async deletePaper() {
            if (!confirm(t('papers.deleteConfirm'))) return;
            try {
                await api(`/api/papers/${this.paper.id}`, { method: 'DELETE' });
                this.$router.push('/');
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
        },
        async saveCustomValue(fieldId) {
            try {
                await api(`/api/papers/${this.paper.id}/custom-values`, {
                    method: 'PUT',
                    body: JSON.stringify({ field_id: fieldId, value: String(this.customValues[fieldId] || '') }),
                });
            } catch (e) {
                console.error('Custom value save error:', e);
            }
        },
        refreshSidebar() {
            window.dispatchEvent(new CustomEvent('refresh-sidebar'));
        },
        refScholarUrl(ref) {
            return scholarSearchUrl(ref);
        },
        async loadReferences() {
            if (!this.paper) return;
            try {
                const data = await api(`/api/papers/${this.paper.id}/references`);
                this.paperRefs = data.references || [];
            } catch (e) {
                console.error('Load references error:', e);
                this.paperRefs = [];
            }
        },
        async extractReferences(force = false) {
            this.extractingRefs = true;
            this.refExtractionResult = null;
            this.refProgress = { percent: 0, message: 'Starte...', current: 0, total: 0, found: null, error: null };
            try {
                const url = `/api/papers/${this.paper.id}/extract-references?force=${force}`;
                const response = await fetch(url, { method: 'POST' });
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop();
                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;
                        try {
                            const evt = JSON.parse(line.slice(6));
                            if (evt.type === 'progress') {
                                this.refProgress = {
                                    percent: evt.percent || 0,
                                    message: evt.message ? translateDetail(evt.message) : '',
                                    current: evt.current || 0,
                                    total: evt.total || 0,
                                    found: (evt.found !== undefined) ? evt.found : null,
                                    error: null,
                                };
                            } else if (evt.type === 'error') {
                                this.refProgress.error = translateDetail(evt.message);
                                this.refExtractionResult = { status: 'error', error: translateDetail(evt.message) };
                            } else if (evt.type === 'complete') {
                                this.refExtractionResult = evt;
                                this.paperRefs = evt.references || [];
                            }
                        } catch (parseErr) { /* skip malformed SSE */ }
                    }
                }
            } catch (e) {
                this.refExtractionResult = { status: 'error', error: e.message };
            }
            this.extractingRefs = false;
        },
        reExtractReferences() {
            if (!confirm(t('refs.reextractConfirm'))) return;
            this.extractReferences(true);
        },
        startEditRef(ref) {
            this.editingRef = ref;
            this.editRefData = {
                title: ref.title || '',
                authors: ref.authors || '',
                year: ref.year || null,
                doi: ref.doi || '',
                journal: ref.journal || '',
            };
        },
        async saveReference() {
            if (!this.editingRef) return;
            try {
                const updated = await api(`/api/papers/${this.paper.id}/references/${this.editingRef.id}`, {
                    method: 'PUT',
                    body: JSON.stringify(this.editRefData),
                });
                // Update in list
                const idx = this.paperRefs.findIndex(r => r.id === this.editingRef.id);
                if (idx >= 0) this.paperRefs.splice(idx, 1, updated);
                this.editingRef = null;
            } catch (e) {
                alert(t('error.saveFailed', { message: e.message }));
            }
        },
        async deleteRef(ref) {
            if (!confirm(`Referenz "${(ref.title || '').substring(0, 60)}" wirklich loeschen?`)) return;
            try {
                await api(`/api/papers/${this.paper.id}/references/${ref.id}`, { method: 'DELETE' });
                this.paperRefs = this.paperRefs.filter(r => r.id !== ref.id);
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
        },
        startAddRef() {
            this.newRefData = { title: '', authors: '', year: null, doi: '', journal: '' };
            this.addingRef = true;
        },
        async saveNewReference() {
            if (!this.newRefData.title) return;
            try {
                const created = await api(`/api/papers/${this.paper.id}/references/add`, {
                    method: 'POST',
                    body: JSON.stringify(this.newRefData),
                });
                this.paperRefs.push(created);
                this.addingRef = false;
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
        }
    }
};


// =============================================================================
// CategoryPlanner Component
// =============================================================================

const CategoryPlanner = {
    template: `
        <div class="p-6 max-w-6xl mx-auto">
            <!-- Header -->
            <div class="flex items-center justify-between mb-6">
                <div class="flex items-center gap-3">
                    <button @click="$router.back()"
                            class="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors">
                        <span v-html="icons.back"></span>
                    </button>
                    <div>
                        <h2 class="text-xl font-semibold text-gray-900">{{ $t('planner.heading') }}</h2>
                        <p class="text-sm text-gray-500">{{ $t('planner.subheading') }}</p>
                    </div>
                </div>
                <button @click="showAddModal = true"
                        class="inline-flex items-center gap-1.5 bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                    <span v-html="icons.plus"></span>
                    {{ $t('categories.new') }}
                </button>
            </div>

            <!-- Tab Switcher -->
            <div class="flex gap-1 mb-4 bg-gray-100 rounded-lg p-1 w-fit">
                <button @click="activeTab = 'tree'"
                        class="px-4 py-2 rounded-md text-sm font-medium transition-colors"
                        :class="activeTab === 'tree' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'">
                    {{ $t('planner.treeView') }}
                </button>
                <button @click="activeTab = 'table'"
                        class="px-4 py-2 rounded-md text-sm font-medium transition-colors"
                        :class="activeTab === 'table' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'">
                    {{ $t('planner.tableView') }}
                </button>
            </div>

            <!-- Loading -->
            <div v-if="loading" class="flex justify-center py-12">
                <div class="spinner"></div>
            </div>

            <!-- Tree View -->
            <div v-else-if="activeTab === 'tree'" class="bg-white rounded-xl border border-gray-200 shadow-sm">
                <div class="p-4 border-b border-gray-100">
                    <p class="text-xs text-gray-500">
                        Ziehe Kategorien per Drag & Drop, um sie zu verschieben.
                        Lege sie auf eine andere Kategorie, um sie als Unterkategorie zuzuordnen.
                        Lege sie in den Bereich &laquo;Oberste Ebene&raquo;, um sie zur Hauptkategorie zu machen.
                    </p>
                </div>

                <!-- Root Drop Zone (make top-level) -->
                <div class="px-4 py-2 border-b border-dashed border-gray-200 text-center transition-colors"
                     :class="dropTarget === 'root' ? 'bg-accent-soft border-accent-soft' : 'bg-gray-50'"
                     @dragover.prevent="onDragOver($event, 'root', null)"
                     @dragleave="onDragLeave"
                     @drop.prevent="onDrop($event, null)">
                    <span class="text-xs text-gray-400">{{ $t('planner.topLevelHint') }}</span>
                </div>

                <!-- Tree Nodes -->
                <div class="p-2 min-h-[200px]">
                    <div v-if="tree.length === 0" class="text-center py-8 text-gray-400 text-sm">
                        {{ $t('categories.empty') }}
                    </div>
                    <template v-for="node in tree" :key="node.id">
                        <div class="planner-tree-node" 
                             :data-id="node.id">
                            <div class="flex items-center gap-2 py-2 px-3 rounded-lg cursor-grab transition-all group"
                                 :class="getNodeClass(node)"
                                 draggable="true"
                                 @dragstart="onDragStart($event, node)"
                                 @dragend="onDragEnd"
                                 @dragover.prevent="onDragOver($event, 'node', node)"
                                 @dragleave="onDragLeave"
                                 @drop.prevent="onDrop($event, node)">
                                <span class="text-gray-300 cursor-grab">
                                    <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/></svg>
                                </span>
                                <span v-html="icons.folder" class="flex-shrink-0 text-gray-400"></span>
                                <span class="font-medium text-sm text-gray-800 flex-1">{{ node.name }}</span>
                                <span class="text-xs text-gray-400 tabular-nums">{{ $tn('common.itemCount', node.paper_count || 0) }}</span>
                                <button @click.stop="confirmDelete(node)"
                                        class="p-1 text-gray-300 hover:text-accent rounded opacity-0 group-hover:opacity-100 transition-all"
                                        :title="$t('common.delete')">
                                    <span v-html="icons.trash"></span>
                                </button>
                            </div>
                            <!-- Children -->
                            <div v-if="node.children && node.children.length" class="ml-6 border-l border-gray-100 pl-1">
                                <template v-for="child in node.children" :key="child.id">
                                    <div class="planner-tree-node" :data-id="child.id">
                                        <div class="flex items-center gap-2 py-1.5 px-3 rounded-lg cursor-grab transition-all group"
                                             :class="getNodeClass(child)"
                                             draggable="true"
                                             @dragstart="onDragStart($event, child)"
                                             @dragend="onDragEnd"
                                             @dragover.prevent="onDragOver($event, 'node', child)"
                                             @dragleave="onDragLeave"
                                             @drop.prevent="onDrop($event, child)">
                                            <span class="text-gray-300 cursor-grab">
                                                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/></svg>
                                            </span>
                                            <span class="w-4 flex-shrink-0"></span>
                                            <span v-html="icons.folder" class="flex-shrink-0 text-gray-400"></span>
                                            <span class="text-sm text-gray-700 flex-1">{{ child.name }}</span>
                                            <span class="text-xs text-gray-400 tabular-nums">{{ $tn('common.itemCount', child.paper_count || 0) }}</span>
                                            <button @click.stop="confirmDelete(child)"
                                                    class="p-1 text-gray-300 hover:text-accent rounded opacity-0 group-hover:opacity-100 transition-all"
                                                    :title="$t('common.delete')">
                                                <span v-html="icons.trash"></span>
                                            </button>
                                        </div>
                                        <!-- Grandchildren -->
                                        <div v-if="child.children && child.children.length" class="ml-6 border-l border-gray-100 pl-1">
                                            <div v-for="gc in child.children" :key="gc.id"
                                                 class="planner-tree-node" :data-id="gc.id">
                                                <div class="flex items-center gap-2 py-1.5 px-3 rounded-lg cursor-grab transition-all group"
                                                     :class="getNodeClass(gc)"
                                                     draggable="true"
                                                     @dragstart="onDragStart($event, gc)"
                                                     @dragend="onDragEnd"
                                                     @dragover.prevent="onDragOver($event, 'node', gc)"
                                                     @dragleave="onDragLeave"
                                                     @drop.prevent="onDrop($event, gc)">
                                                    <span class="text-gray-300 cursor-grab">
                                                        <svg class="w-4 h-4" viewBox="0 0 24 24" fill="currentColor"><circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/></svg>
                                                    </span>
                                                    <span class="w-4 flex-shrink-0"></span>
                                                    <span class="w-4 flex-shrink-0"></span>
                                                    <span v-html="icons.folder" class="flex-shrink-0 text-gray-400"></span>
                                                    <span class="text-sm text-gray-600 flex-1">{{ gc.name }}</span>
                                                    <span class="text-xs text-gray-400 tabular-nums">{{ $tn('common.itemCount', gc.paper_count || 0) }}</span>
                                                    <button @click.stop="confirmDelete(gc)"
                                                            class="p-1 text-gray-300 hover:text-accent rounded opacity-0 group-hover:opacity-100 transition-all"
                                                            :title="$t('common.delete')">
                                                        <span v-html="icons.trash"></span>
                                                    </button>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </template>
                            </div>
                        </div>
                    </template>
                </div>

                <!-- Status bar -->
                <div v-if="dragStatus" class="px-4 py-2 border-t border-gray-100 text-xs text-accent bg-accent-soft">
                    {{ dragStatus }}
                </div>
            </div>

            <!-- Table View -->
            <div v-else-if="activeTab === 'table'" class="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
                <table class="w-full">
                    <thead>
                        <tr class="bg-gray-50 border-b border-gray-200">
                            <th class="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3 w-56">Name</th>
                            <th class="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3 w-48">{{ $t('categories.parent') }}</th>
                            <th class="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3">{{ $t('common.description') }}</th>
                            <th class="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3 w-64">Keywords</th>
                            <th class="text-center text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3 w-20">{{ $t('planner.colItems') }}</th>
                            <th class="text-center text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3 w-32">{{ $t('planner.colActions') }}</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr v-for="cat in flatTableData" :key="cat.id"
                            class="border-b border-gray-100 hover:bg-gray-50 transition-colors">
                            <td class="px-4 py-2.5">
                                <div class="flex items-center gap-1">
                                    <span v-for="d in cat.depth" :key="d" class="w-3"></span>
                                    <input v-if="editingCell === cat.id + '-name'"
                                           v-model="cat.name" @blur="saveField(cat, 'name')" @keydown.enter="saveField(cat, 'name')"
                                           class="w-full border border-accent-soft rounded px-2 py-1 text-sm focus:ring-2 focus:ring-accent outline-none"
                                           ref="editInput" />
                                    <span v-else @click="startEdit(cat, 'name')"
                                          class="text-sm text-gray-800 cursor-pointer hover:text-accent truncate"
                                          :class="cat.depth === 0 ? 'font-medium' : ''">
                                        {{ cat.name }}
                                    </span>
                                </div>
                            </td>
                            <td class="px-4 py-2.5">
                                <select v-model="cat.parent_id" @change="saveField(cat, 'parent_id')"
                                        class="w-full border border-gray-200 rounded px-2 py-1 text-sm bg-white focus:ring-2 focus:ring-accent outline-none">
                                    <option :value="null">{{ $t('planner.noParent') }}</option>
                                    <option v-for="opt in getParentOptions(cat)" :key="opt.id" :value="opt.id">{{ opt.name }}</option>
                                </select>
                            </td>
                            <td class="px-4 py-2.5">
                                <input v-if="editingCell === cat.id + '-description'"
                                       v-model="cat.description" @blur="saveField(cat, 'description')" @keydown.enter="saveField(cat, 'description')"
                                       class="w-full border border-accent-soft rounded px-2 py-1 text-sm focus:ring-2 focus:ring-accent outline-none"
                                       :placeholder="$t('planner.descriptionPlaceholder')" />
                                <span v-else @click="startEdit(cat, 'description')"
                                      class="text-sm cursor-pointer hover:text-accent block truncate"
                                      :class="cat.description ? 'text-gray-600' : 'text-gray-300 italic'">
                                    {{ cat.description || $t('planner.clickToEdit') }}
                                </span>
                            </td>
                            <td class="px-4 py-2.5">
                                <input v-if="editingCell === cat.id + '-keywords'"
                                       v-model="cat.keywords" @blur="saveField(cat, 'keywords')" @keydown.enter="saveField(cat, 'keywords')"
                                       class="w-full border border-accent-soft rounded px-2 py-1 text-sm focus:ring-2 focus:ring-accent outline-none"
                                       :placeholder="$t('planner.keywordsPlaceholder')" />
                                <span v-else @click="startEdit(cat, 'keywords')"
                                      class="text-sm cursor-pointer hover:text-accent block truncate"
                                      :class="cat.keywords ? 'text-gray-600' : 'text-gray-300 italic'">
                                    {{ cat.keywords || $t('planner.clickToEdit') }}
                                </span>
                            </td>
                            <td class="px-4 py-2.5 text-center">
                                <span class="text-sm text-gray-500 tabular-nums">{{ cat.paper_count || 0 }}</span>
                            </td>
                            <td class="px-4 py-2.5 text-center">
                                <div class="flex items-center justify-center gap-1">
                                    <button @click="llmSuggest(cat)" :disabled="cat._llmLoading"
                                            class="inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-colors"
                                            :class="cat._llmLoading ? 'bg-accent-soft text-accent cursor-wait' : 'bg-accent-soft text-accent-ink hover:bg-accent-soft border border-accent-soft'"
                                            :title="$t('planner.llmSuggestTitle')">
                                        <span v-html="icons.sparkle"></span>
                                        <span v-if="cat._llmLoading">...</span>
                                        <span v-else>LLM</span>
                                    </button>
                                    <button @click="confirmDelete(cat)"
                                            class="p-1 text-gray-300 hover:text-accent rounded transition-colors"
                                            :title="$t('common.delete')">
                                        <span v-html="icons.trash"></span>
                                    </button>
                                </div>
                            </td>
                        </tr>
                        <tr v-if="flatTableData.length === 0">
                            <td colspan="6" class="px-4 py-8 text-center text-sm text-gray-400">
                                {{ $t('categories.empty') }}
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- Add Category Modal -->
            <div v-if="showAddModal" class="fixed inset-0 bg-black/30 flex items-center justify-center z-50"
                 @click.self="showAddModal = false">
                <div class="bg-white rounded-xl shadow-xl p-6 w-full max-w-md">
                    <h4 class="text-base font-semibold text-gray-900 mb-4">{{ $t('categories.new') }}</h4>
                    <div class="space-y-3">
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">{{ $t('common.name') }}</label>
                            <input v-model="newCat.name" :placeholder="$t('categories.namePlaceholder')"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">{{ $t('categories.parent') }}</label>
                            <select v-model="newCat.parent_id"
                                    class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-accent focus:border-accent outline-none">
                                <option :value="null">{{ $t('categories.parentNone') }}</option>
                                <option v-for="c in flatCategories" :key="c.id" :value="c.id">{{ c.name }}</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">{{ $t('common.description') }}</label>
                            <input v-model="newCat.description" :placeholder="$t('categories.descriptionPlaceholder')"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">{{ $t('common.keywords') }}</label>
                            <input v-model="newCat.keywords" :placeholder="$t('categories.keywordsPlaceholder')"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                    </div>
                    <div class="flex items-center gap-2 mt-5">
                        <button @click="llmSuggestNew" :disabled="!newCat.name || newCatLlmLoading"
                                class="inline-flex items-center gap-1.5 bg-accent-soft border border-accent-soft text-accent-ink px-3 py-2 rounded-lg text-sm font-medium hover:bg-accent-soft disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            <span v-html="icons.sparkle"></span>
                            <span v-if="newCatLlmLoading">{{ $t('categories.llmThinking') }}</span>
                            <span v-else>{{ $t('categories.llmSuggest') }}</span>
                        </button>
                        <div class="flex-1"></div>
                        <button @click="showAddModal = false"
                                class="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors">
                            {{ $t('common.cancel') }}
                        </button>
                        <button @click="createCategory" :disabled="!newCat.name"
                                class="bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            {{ $t('common.create') }}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `,
    data() {
        return {
            icons,
            loading: true,
            activeTab: 'tree',
            tree: [],
            flatCategories: [],
            // Drag & Drop
            dragNode: null,
            dropTarget: null,
            dropTargetNode: null,
            dragStatus: '',
            // Table edit
            editingCell: null,
            // Add modal
            showAddModal: false,
            newCat: { name: '', parent_id: null, description: '', keywords: '' },
            newCatLlmLoading: false,
        };
    },
    computed: {
        flatTableData() {
            const result = [];
            const flatten = (nodes, depth) => {
                for (const node of nodes) {
                    result.push({
                        ...node,
                        depth,
                        _llmLoading: node._llmLoading || false,
                    });
                    if (node.children && node.children.length) {
                        flatten(node.children, depth + 1);
                    }
                }
            };
            flatten(this.tree, 0);
            return result;
        },
    },
    async created() {
        await this.load();
    },
    methods: {
        async load() {
            this.loading = true;
            try {
                const [tree, categories] = await Promise.all([
                    api('/api/categories/tree'),
                    api('/api/categories'),
                ]);
                this.tree = tree;
                this.flatCategories = categories;
            } catch (e) {
                console.error('CategoryPlanner load error:', e);
            }
            this.loading = false;
        },

        // ── Drag & Drop ──
        onDragStart(e, node) {
            this.dragNode = node;
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', String(node.id));
            this.dragStatus = 'Ziehe: ' + node.name;
        },
        onDragEnd() {
            this.dragNode = null;
            this.dropTarget = null;
            this.dropTargetNode = null;
            this.dragStatus = '';
        },
        onDragOver(e, type, node) {
            if (!this.dragNode) return;
            e.dataTransfer.dropEffect = 'move';
            if (type === 'root') {
                this.dropTarget = 'root';
                this.dropTargetNode = null;
                this.dragStatus = this.dragNode.name + ' → Oberste Ebene';
            } else if (node && node.id !== this.dragNode.id) {
                this.dropTarget = 'node';
                this.dropTargetNode = node;
                this.dragStatus = t('planner.dragToChild', { child: this.dragNode.name, parent: node.name });
            }
        },
        onDragLeave() {
            // Only clear if we're truly leaving
        },
        async onDrop(e, targetNode) {
            if (!this.dragNode) return;
            const dragId = this.dragNode.id;
            const newParentId = targetNode ? targetNode.id : null;

            // Don't drop on self
            if (targetNode && targetNode.id === dragId) {
                this.onDragEnd();
                return;
            }

            // Don't drop on own descendant
            if (targetNode && this.isDescendant(this.dragNode, targetNode.id)) {
                this.dragStatus = t('planner.dragCycle');
                setTimeout(() => this.onDragEnd(), 1500);
                return;
            }

            try {
                await api('/api/categories/' + dragId, {
                    method: 'PUT',
                    body: JSON.stringify({ parent_id: newParentId }),
                });
                this.onDragEnd();
                await this.load();
                window.dispatchEvent(new Event('refresh-sidebar'));
            } catch (e) {
                this.dragStatus = t('error.generic', { message: e.message });
                setTimeout(() => this.onDragEnd(), 2000);
            }
        },
        isDescendant(node, targetId) {
            if (!node.children) return false;
            for (const child of node.children) {
                if (child.id === targetId) return true;
                if (this.isDescendant(child, targetId)) return true;
            }
            return false;
        },
        getNodeClass(node) {
            if (this.dragNode && this.dragNode.id === node.id) {
                return 'opacity-40 bg-gray-100';
            }
            if (this.dropTargetNode && this.dropTargetNode.id === node.id) {
                return 'bg-accent-soft border-2 border-accent-soft border-dashed';
            }
            return 'hover:bg-gray-50 border-2 border-transparent';
        },

        // ── Table editing ──
        startEdit(cat, field) {
            this.editingCell = cat.id + '-' + field;
            this.$nextTick(() => {
                const input = this.$el.querySelector('input:focus, input[class*="border-accent"]');
                if (input) input.focus();
            });
        },
        async saveField(cat, field) {
            this.editingCell = null;
            try {
                const payload = {};
                if (field === 'parent_id') {
                    payload.parent_id = cat.parent_id;
                } else {
                    payload[field] = cat[field];
                }
                await api('/api/categories/' + cat.id, {
                    method: 'PUT',
                    body: JSON.stringify(payload),
                });
                if (field === 'name' || field === 'parent_id') {
                    await this.load();
                    window.dispatchEvent(new Event('refresh-sidebar'));
                }
            } catch (e) {
                alert(t('error.saveFailed', { message: e.message }));
                await this.load();
            }
        },
        getParentOptions(cat) {
            return this.flatCategories.filter(c => {
                if (c.id === cat.id) return false;
                // Prevent circular
                let check = c;
                while (check) {
                    if (check.parent_id === cat.id) return false;
                    check = this.flatCategories.find(x => x.id === check.parent_id);
                }
                return true;
            });
        },

        // ── LLM Suggest ──
        async llmSuggest(cat) {
            cat._llmLoading = true;
            try {
                const suggestion = await api('/api/categories/suggest', {
                    method: 'POST',
                    body: JSON.stringify({ name: cat.name }),
                });
                if (suggestion.description) cat.description = suggestion.description;
                if (suggestion.keywords) cat.keywords = suggestion.keywords;
                // Auto-save
                await api('/api/categories/' + cat.id, {
                    method: 'PUT',
                    body: JSON.stringify({
                        description: cat.description,
                        keywords: cat.keywords,
                    }),
                });
            } catch (e) {
                alert(t('categories.llmSuggestFailed', { message: e.message }));
            }
            cat._llmLoading = false;
        },
        async llmSuggestNew() {
            if (!this.newCat.name) return;
            this.newCatLlmLoading = true;
            try {
                const suggestion = await api('/api/categories/suggest', {
                    method: 'POST',
                    body: JSON.stringify({ name: this.newCat.name }),
                });
                if (suggestion.description) this.newCat.description = suggestion.description;
                if (suggestion.keywords) this.newCat.keywords = suggestion.keywords;
            } catch (e) {
                alert(t('categories.llmSuggestFailed', { message: e.message }));
            }
            this.newCatLlmLoading = false;
        },

        // ── CRUD ──
        async createCategory() {
            if (!this.newCat.name) return;
            try {
                await api('/api/categories', {
                    method: 'POST',
                    body: JSON.stringify(this.newCat),
                });
                this.newCat = { name: '', parent_id: null, description: '', keywords: '' };
                this.showAddModal = false;
                await this.load();
                window.dispatchEvent(new Event('refresh-sidebar'));
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
        },
        async confirmDelete(node) {
            const childCount = this.countDescendants(node);
            let msg = t('categories.deleteConfirm', { name: node.name });
            if (childCount > 0) {
                msg += ' ' + childCount + ' Unterkategorie(n) werden ebenfalls entfernt.';
            }
            if (!confirm(msg)) return;
            try {
                await api('/api/categories/' + node.id, { method: 'DELETE' });
                await this.load();
                window.dispatchEvent(new Event('refresh-sidebar'));
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
        },
        countDescendants(node) {
            let count = 0;
            if (node.children) {
                count += node.children.length;
                for (const child of node.children) {
                    count += this.countDescendants(child);
                }
            }
            return count;
        },
    },
};


// =============================================================================
// ImportPage Component
// =============================================================================

const ImportPage = {
    template: `
        <div class="p-6 max-w-4xl mx-auto">
            <h2 class="text-xl font-semibold text-gray-900 mb-2">PDF Import</h2>
            <p class="text-sm text-gray-500 mb-6">
                {{ $t('import.pageHint') }}
            </p>

            <!-- Drag & Drop Zone -->
            <div class="mb-6 border-2 border-dashed rounded-xl p-8 text-center transition-colors cursor-pointer"
                 :class="dragging ? 'border-accent bg-accent-soft' : 'border-gray-300 hover:border-gray-400'"
                 @dragover.prevent="dragging = true"
                 @dragenter.prevent="dragging = true"
                 @dragleave.prevent="dragging = false"
                 @drop.prevent="handleDrop"
                 @click="$refs.fileInput.click()">
                <input ref="fileInput" type="file" accept=".pdf" multiple class="hidden" @change="handleFileInput" />
                <div class="flex flex-col items-center gap-2">
                    <div class="w-12 h-12 rounded-full flex items-center justify-center"
                         :class="dragging ? 'bg-accent-soft text-accent' : 'bg-gray-100 text-gray-400'">
                        <span v-html="icons.upload" style="width:24px;height:24px;"></span>
                    </div>
                    <p class="text-sm font-medium" :class="dragging ? 'text-accent-ink' : 'text-gray-600'">
                        {{ dragging ? $t('import.dropHere') : $t('import.dropHint') }}
                    </p>
                    <p class="text-xs text-gray-400">{{ $t('import.multipleFiles') }}</p>
                </div>
            </div>

            <!-- Upload Results (completed papers) -->
            <div v-if="uploadResults.length" class="mb-6">
                <h3 class="text-sm font-semibold text-gray-900 mb-3">{{ $t('import.uploadResults') }}</h3>
                <div class="space-y-1">
                    <div v-for="r in uploadResults" :key="r.filename"
                         class="flex items-center gap-3 py-2 px-3 rounded-lg text-sm"
                         :class="{
                            'bg-accent-soft text-accent-ink': r.status === 'ok',
                            'bg-accent-soft text-accent-ink': r.status === 'skipped',
                            'bg-accent-soft text-accent-ink': r.status === 'error',
                         }">
                        <span v-if="r.status === 'ok'" v-html="icons.check"></span>
                        <span v-else-if="r.status === 'error'" class="text-accent">!</span>
                        <span v-else>-</span>
                        <span class="truncate flex-1">{{ r.title || r.filename }}</span>
                        <span v-if="r.paper_id" class="text-xs">
                            <router-link :to="{name: 'paper', params: {id: r.paper_id}}" class="text-accent hover:underline">{{ $t('common.open') }}</router-link>
                        </span>
                        <span v-if="r.error" class="text-xs ml-auto">{{ r.error }}</span>
                    </div>
                </div>
            </div>

            <!-- Actions -->
            <div class="flex items-center gap-3 mb-6">
                <button @click="loadPending"
                        class="inline-flex items-center gap-1.5 bg-white border border-gray-300 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors">
                    <span v-html="icons.refresh"></span>
                    {{ $t('common.refresh') }}
                </button>
                <button @click="importAllSmart" :disabled="importing || !pendingFiles.length"
                        class="inline-flex items-center gap-1.5 bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                    <span v-if="importing" class="spinner" style="width:14px;height:14px;border-width:2px;border-top-color:#fff;"></span>
                    <span v-else v-html="icons.upload"></span>
                    {{ importing ? 'Importiere...' : 'Alle importieren' }}
                </button>
            </div>

            <div v-if="loading" class="flex justify-center py-12"><div class="spinner"></div></div>

            <div v-else-if="pendingFiles.length" class="space-y-2">
                <div v-for="file in pendingFiles" :key="file.filename"
                     class="bg-white rounded-lg border border-gray-200 p-4 flex items-center gap-4">
                    <div class="flex-shrink-0 w-10 h-10 bg-accent-soft text-accent rounded-lg flex items-center justify-center">
                        <span v-html="icons.pdf"></span>
                    </div>
                    <div class="flex-1 min-w-0">
                        <p class="font-medium text-sm text-gray-900 truncate">{{ file.filename }}</p>
                        <p class="text-xs text-gray-400">{{ formatSize(file.size) }}</p>
                    </div>
                    <button @click="importSingleSmart(file)" :disabled="importing"
                            class="inline-flex items-center gap-1.5 bg-white border border-gray-300 text-gray-700 px-3 py-1.5 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-40 transition-colors">
                        <span v-html="icons.upload"></span>
                        {{ $t('import.action') }}
                    </button>
                </div>
            </div>

            <div v-else class="text-center py-16">
                <div class="text-5xl mb-3 opacity-30" v-html="icons.upload" style="display:inline-block;width:48px;height:48px;"></div>
                <p class="text-gray-400 text-sm">{{ $t('import.inputFolderEmpty') }}</p>
            </div>

            <div v-if="importResults.length" class="mt-6 relative">
                <div class="flex items-center justify-between mb-3">
                    <h3 class="text-sm font-semibold text-gray-900">{{ $t('import.importResults') }}</h3>
                    <button @click="importResults = []" class="text-gray-400 hover:text-gray-600 transition-colors" :title="$t('common.close')">
                        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                </div>
                <div class="space-y-1">
                    <div v-for="r in importResults" :key="r.filename"
                         class="flex items-center gap-3 py-2 px-3 rounded-lg text-sm"
                         :class="{ 'bg-accent-soft text-accent-ink': r.status==='ok', 'bg-accent-soft text-accent-ink': r.status==='skipped', 'bg-accent-soft text-accent-ink': r.status==='error' }">
                        <span v-if="r.status === 'ok'" v-html="icons.check"></span>
                        <span v-else-if="r.status === 'error'" class="text-accent">!</span>
                        <span v-else>-</span>
                        <span class="truncate">{{ r.filename }}</span>
                        <span v-if="r.error" class="text-xs ml-auto">{{ r.error }}</span>
                    </div>
                </div>
            </div>

            <!-- ========== UPLOAD DIALOG ========== -->
            <div v-if="showUploadDialog" class="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4">
                <div class="bg-white rounded-2xl shadow-2xl w-full max-w-xl max-h-[90vh] overflow-y-auto">
                    <!-- Header -->
                    <div class="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
                        <h3 class="text-lg font-semibold text-gray-900">
                            {{ uploadDialogPhase === 'options' ? 'Import-Optionen' : uploadDialogPhase === 'processing' ? 'Importiere...' : 'Import abgeschlossen' }}
                        </h3>
                        <button v-if="uploadDialogPhase !== 'processing'" @click="closeUploadDialog"
                                class="text-gray-400 hover:text-gray-600 transition-colors">
                            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                        </button>
                    </div>

                    <!-- Phase 1: Options -->
                    <div v-if="uploadDialogPhase === 'options'" class="p-6">
                        <p class="text-sm text-gray-600 mb-4">
                            <strong>{{ uploadQueue.length }}</strong> PDF{{ uploadQueue.length > 1 ? 's' : '' }} ausgewaehlt:
                        </p>
                        <div class="mb-4 max-h-24 overflow-y-auto space-y-1">
                            <div v-for="f in uploadQueue" :key="f.name" class="flex items-center gap-2 text-sm text-gray-700">
                                <span v-html="icons.pdf" class="flex-shrink-0 text-accent" style="width:14px;height:14px;"></span>
                                <span class="truncate">{{ f.name }}</span>
                            </div>
                        </div>
                        <h4 class="text-sm font-semibold text-gray-800 mb-3">{{ $t('import.automations') }}</h4>
                        <div class="space-y-2.5 mb-6">
                            <label class="flex items-center gap-3 cursor-pointer group">
                                <input type="checkbox" v-model="uploadOpts.doi" class="w-4 h-4 rounded border-gray-300 text-accent focus:ring-accent" />
                                <div>
                                    <span class="text-sm font-medium text-gray-700 group-hover:text-gray-900">{{ $t('import.optDoi') }}</span>
                                    <p class="text-xs text-gray-400">{{ $t('import.optDoiHint') }}</p>
                                </div>
                            </label>
                            <label class="flex items-center gap-3 cursor-pointer group">
                                <input type="checkbox" v-model="uploadOpts.validate" class="w-4 h-4 rounded border-gray-300 text-accent focus:ring-accent" />
                                <div>
                                    <span class="text-sm font-medium text-gray-700 group-hover:text-gray-900">{{ $t('import.optValidate') }}</span>
                                    <p class="text-xs text-gray-400">{{ $t('import.optValidateHint') }}</p>
                                </div>
                            </label>
                            <label class="flex items-center gap-3 cursor-pointer group">
                                <input type="checkbox" v-model="uploadOpts.categories" class="w-4 h-4 rounded border-gray-300 text-accent focus:ring-accent" />
                                <div>
                                    <span class="text-sm font-medium text-gray-700 group-hover:text-gray-900">{{ $t('import.optCategories') }}</span>
                                    <p class="text-xs text-gray-400">{{ $t('import.optCategoriesHint') }}</p>
                                </div>
                            </label>
                            <label class="flex items-center gap-3 cursor-pointer group">
                                <input type="checkbox" v-model="uploadOpts.abstract" class="w-4 h-4 rounded border-gray-300 text-accent focus:ring-accent" />
                                <div>
                                    <span class="text-sm font-medium text-gray-700 group-hover:text-gray-900">{{ $t('import.optAbstract') }}</span>
                                    <p class="text-xs text-gray-400">{{ $t('import.optAbstractHint') }}</p>
                                </div>
                            </label>
                        </div>
                        <div class="flex justify-end gap-2">
                            <button @click="closeUploadDialog" class="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">{{ $t('common.cancel') }}</button>
                            <button @click="startSmartUpload" class="px-5 py-2 text-sm text-white bg-accent rounded-lg hover:bg-accent-ink font-medium">{{ $t('import.action') }}</button>
                        </div>
                    </div>

                    <!-- Phase 2: Processing -->
                    <div v-if="uploadDialogPhase === 'processing'" class="p-6">
                        <div class="mb-2 flex items-center justify-between text-sm">
                            <span class="text-gray-600">{{ currentUploadFile }}</span>
                            <span class="text-gray-400">{{ currentUploadFileIdx + 1 }} / {{ uploadQueue.length }}</span>
                        </div>
                        <div class="w-full bg-gray-200 rounded-full h-2.5 mb-4">
                            <div class="bg-accent h-2.5 rounded-full transition-all duration-300" :style="{ width: uploadProgress + '%' }"></div>
                        </div>
                        <div class="space-y-1.5 max-h-48 overflow-y-auto">
                            <div v-for="(step, i) in uploadSteps" :key="i"
                                 class="flex items-center gap-2 text-xs" :class="step.done ? 'text-accent' : step.error ? 'text-accent' : 'text-gray-500'">
                                <span v-if="step.done" v-html="icons.check" style="width:12px;height:12px;"></span>
                                <span v-else-if="step.error" class="text-accent font-bold">!</span>
                                <span v-else class="spinner" style="width:12px;height:12px;border-width:1.5px;"></span>
                                {{ step.message }}
                            </div>
                        </div>
                    </div>

                    <!-- Phase 3: Result -->
                    <div v-if="uploadDialogPhase === 'result'" class="p-6">
                        <div v-for="(res, ri) in uploadDialogResults" :key="ri" class="mb-5 last:mb-0"
                             :class="uploadDialogResults.length > 1 ? 'pb-5 border-b border-gray-100 last:border-0' : ''">
                            <!-- Error -->
                            <div v-if="res.error" class="p-3 rounded-lg text-sm" :class="res.duplicate_paper_id ? 'bg-accent-soft border border-accent-soft text-accent-ink' : 'bg-accent-soft border border-accent-soft text-accent-ink'">
                                <p class="font-medium">{{ res.filename }}</p>
                                <p>{{ res.error }}</p>
                                <div v-if="res.duplicate_paper_id" class="mt-2 flex items-center gap-2">
                                    <span class="text-xs text-accent">{{ $t('import.alreadyPresentAs') }}</span>
                                    <router-link :to="{name: 'paper', params: {id: res.duplicate_paper_id}}"
                                                 class="text-xs font-medium text-accent hover:text-accent-ink hover:underline"
                                                 @click.native="closeUploadDialog">
                                        {{ res.duplicate_title || 'Paper oeffnen' }} &rarr;
                                    </router-link>
                                </div>
                            </div>
                            <!-- Success Result -->
                            <template v-else>
                                <div class="flex items-start gap-3 mb-3">
                                    <div class="flex-shrink-0 w-8 h-8 bg-accent-soft text-accent rounded-lg flex items-center justify-center mt-0.5">
                                        <span v-html="icons.check"></span>
                                    </div>
                                    <div class="flex-1 min-w-0">
                                        <p class="font-semibold text-gray-900 text-sm leading-snug">{{ res.title || res.filename }}</p>
                                        <p v-if="res.authors" class="text-xs text-gray-500 mt-0.5">{{ res.authors }}</p>
                                    </div>
                                </div>

                                <div class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs ml-11 mb-3">
                                    <div class="flex items-center gap-1.5">
                                        <span class="w-2 h-2 rounded-full" :class="res.doi ? 'bg-accent' : 'bg-gray-300'"></span>
                                        <span :class="res.doi ? 'text-gray-700' : 'text-gray-400'">DOI: {{ res.doi || $t('import.notFound') }}</span>
                                    </div>
                                    <div class="flex items-center gap-1.5">
                                        <span class="w-2 h-2 rounded-full" :class="res.year ? 'bg-accent' : 'bg-gray-300'"></span>
                                        <span :class="res.year ? 'text-gray-700' : 'text-gray-400'">{{ $t('common.year') }}: {{ res.year || '-' }}</span>
                                    </div>
                                    <div class="flex items-center gap-1.5">
                                        <span class="w-2 h-2 rounded-full" :class="res.abstract ? 'bg-accent' : 'bg-gray-300'"></span>
                                        <span :class="res.abstract ? 'text-gray-700' : 'text-gray-400'">
                                            {{ $t('common.abstract') }}: {{ res.abstract ? (res.abstract_source === 'generated' ? $t('abstract.aiGenerated') : res.abstract_source === 'pdf' ? $t('abstract.aiExtracted') : $t('import.present')) : $t('import.notFound') }}
                                        </span>
                                    </div>
                                    <div class="flex items-center gap-1.5">
                                        <span class="w-2 h-2 rounded-full" :class="res.journal ? 'bg-accent' : 'bg-gray-300'"></span>
                                        <span :class="res.journal ? 'text-gray-700' : 'text-gray-400'">Journal: {{ res.journal || '-' }}</span>
                                    </div>
                                </div>

                                <div v-if="res.categories && res.categories.length" class="ml-11 mb-3">
                                    <span class="text-xs text-gray-400">{{ $t('sidebar.categories') }}: </span>
                                    <span v-for="cat in res.categories" :key="cat.id"
                                          class="inline-flex items-center bg-accent-soft text-accent-ink px-2 py-0.5 rounded-full text-xs mr-1">
                                        {{ cat.name }}
                                    </span>
                                </div>
                                <div v-else class="ml-11 mb-3 text-xs text-gray-400">{{ $t('import.noCategoriesAssigned') }}</div>

                                <!-- AI steps that failed at the provider (rate limit, key, host):
                                     the server-side log is invisible to the user, so the empty
                                     fields above need their reason spelled out here. -->
                                <div v-if="res.llm_failures && res.llm_failures.length"
                                     class="ml-11 mb-3 p-2.5 rounded-lg bg-accent-soft border border-accent-soft text-xs text-accent-ink">
                                    <p class="font-medium">{{ llmFailureSummary(res.llm_failures) }}</p>
                                    <p class="mt-1 opacity-80">{{ $t('import.llm.affectedSteps', { steps: llmFailureSteps(res.llm_failures) }) }}</p>
                                </div>

                                <div v-if="res.abstract" class="ml-11 mb-3">
                                    <p class="text-xs text-gray-500 leading-relaxed line-clamp-3">{{ res.abstract }}</p>
                                </div>

                                <div class="ml-11 flex gap-2">
                                    <router-link :to="{name: 'paper', params: {id: res.paper_id}}"
                                                 class="inline-flex items-center gap-1 text-xs text-accent hover:text-accent-ink font-medium"
                                                 @click.native="closeUploadDialog">
                                        Paper oeffnen &rarr;
                                    </router-link>
                                </div>
                            </template>
                        </div>

                        <div class="flex justify-end mt-4 pt-4 border-t border-gray-100">
                            <button @click="closeUploadDialog" class="px-4 py-2 text-sm text-white bg-accent rounded-lg hover:bg-accent-ink">{{ $t('common.close') }}</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `,
    data() {
        return {
            pendingFiles: [],
            loading: true,
            importing: false,
            importResults: [],
            uploadResults: [],
            dragging: false,
            icons,
            // Upload dialog state
            showUploadDialog: false,
            uploadDialogPhase: 'options', // 'options' | 'processing' | 'result'
            uploadQueue: [],
            uploadOpts: { doi: true, validate: true, categories: true, abstract: true },
            uploadProgress: 0,
            uploadSteps: [],
            currentUploadFile: '',
            currentUploadFileIdx: 0,
            uploadDialogResults: [],
        };
    },
    created() {
        this.loadPending();
        this._globalDropHandler = (e) => {
            if (e.detail && e.detail.files && e.detail.files.length) {
                this.openUploadDialog(e.detail.files);
            }
        };
        window.addEventListener('global-pdf-drop', this._globalDropHandler);
    },
    beforeUnmount() {
        window.removeEventListener('global-pdf-drop', this._globalDropHandler);
    },
    methods: {
        handleDrop(event) {
            this.dragging = false;
            const files = Array.from(event.dataTransfer.files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
            if (files.length) this.openUploadDialog(files);
        },
        handleFileInput(event) {
            const files = Array.from(event.target.files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
            if (files.length) this.openUploadDialog(files);
            event.target.value = '';
        },
        openUploadDialog(files) {
            this.uploadQueue = files;
            this.uploadDialogPhase = 'options';
            this.uploadDialogResults = [];
            this.uploadSteps = [];
            this.uploadProgress = 0;
            this.showUploadDialog = true;
        },
        closeUploadDialog() {
            this.showUploadDialog = false;
            this.uploadQueue = [];
        },
        async startSmartUpload() {
            this.uploadDialogPhase = 'processing';
            this.uploadDialogResults = [];
            const files = this.uploadQueue;

            for (let i = 0; i < files.length; i++) {
                this.currentUploadFileIdx = i;
                this.currentUploadFile = files[i].name;
                this.uploadSteps = [{ message: files[i]._inputFolder ? t('import.starting') : t('import.uploading'), done: false }];
                this.uploadProgress = 0;

                try {
                    const params = new URLSearchParams({
                        do_doi: this.uploadOpts.doi,
                        do_categories: this.uploadOpts.categories,
                        do_abstract: this.uploadOpts.abstract,
                        do_validate: this.uploadOpts.validate,
                    });

                    let resp;
                    if (files[i]._inputFolder) {
                        // File is already in the input folder - use process-smart endpoint
                        resp = await fetch(`/api/import/process-smart/${encodeURIComponent(files[i]._inputFilename)}?` + params.toString(), {
                            method: 'POST',
                        });
                    } else {
                        // File was drag-dropped/selected - upload it
                        const formData = new FormData();
                        formData.append('file', files[i]);
                        resp = await fetch('/api/import/upload-smart?' + params.toString(), {
                            method: 'POST',
                            body: formData,
                        });
                    }

                    if (!resp.ok) {
                        const err = await resp.json().catch(() => ({}));
                        throw new Error(err.detail || 'HTTP ' + resp.status);
                    }

                    // SSE-like reader
                    const reader = resp.body.getReader();
                    const decoder = new TextDecoder();
                    let buffer = '';
                    let lastResult = null;

                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;
                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n');
                        buffer = lines.pop() || '';
                        for (const line of lines) {
                            if (!line.startsWith('data: ')) continue;
                            try {
                                const data = JSON.parse(line.slice(6));
                                if (data.type === 'progress') {
                                    this.uploadProgress = data.percent || 0;
                                    // update or add step
                                    const existing = this.uploadSteps.find(s => s.step === data.step && !s.done);
                                    if (existing) {
                                        existing.message = translateDetail(data.message);
                                        if (data.percent >= 95) existing.done = true;
                                    } else {
                                        // mark previous as done
                                        this.uploadSteps.forEach(s => { if (!s.done && !s.error) s.done = true; });
                                        this.uploadSteps.push({ step: data.step, message: translateDetail(data.message), done: false });
                                    }
                                } else if (data.type === 'complete') {
                                    this.uploadSteps.forEach(s => { if (!s.error) s.done = true; });
                                    this.uploadProgress = 100;
                                    lastResult = data;
                                } else if (data.type === 'error') {
                                    this.uploadSteps.forEach(s => { if (!s.done) s.error = true; });
                                    this.uploadSteps.push({ message: translateDetail(data.message), error: true });
                                    lastResult = { error: translateDetail(data.message), filename: files[i].name,
                                        duplicate_paper_id: data.duplicate_paper_id,
                                        duplicate_title: data.duplicate_title };
                                }
                            } catch (e) { /* ignore parse errors */ }
                        }
                    }

                    if (lastResult) {
                        if (!lastResult.error) lastResult.status = 'ok';
                        this.uploadDialogResults.push({ filename: files[i].name, ...lastResult });
                        this.uploadResults.push({ filename: files[i].name, status: lastResult.error ? 'error' : 'ok', paper_id: lastResult.paper_id, title: lastResult.title, error: lastResult.error });
                    }
                } catch (e) {
                    this.uploadDialogResults.push({ filename: files[i].name, error: e.message });
                    this.uploadResults.push({ filename: files[i].name, status: 'error', error: e.message });
                }
            }

            this.uploadDialogPhase = 'result';
            await this.loadPending();
            window.dispatchEvent(new CustomEvent('refresh-sidebar'));
        },
        // One sentence for the whole import: all failed steps share a cause
        // (the same model behind the fast role), so the first entry names it.
        llmFailureSummary(failures) {
            const first = failures[0] || {};
            const kind = ['rate_limited', 'auth', 'unavailable'].includes(first.kind) ? first.kind : 'unavailable';
            return t('import.llm.' + kind, { model: first.model || '?', status: first.status || '' });
        },
        llmFailureSteps(failures) {
            return failures
                .map(f => hasKey('import.llm.step.' + f.step) ? t('import.llm.step.' + f.step) : f.step)
                .join(', ');
        },
        async loadPending() {
            this.loading = true;
            try {
                const data = await api('/api/import/pending');
                this.pendingFiles = data.files;
            } catch (e) {
                console.error('Load pending error:', e);
            }
            this.loading = false;
        },
        importAllSmart() {
            // Open smart dialog for all pending input folder files
            const inputFiles = this.pendingFiles.map(f => ({ name: f.name || f.filename, size: f.size, _inputFolder: true, _inputFilename: f.filename }));
            this.openUploadDialog(inputFiles);
        },
        importSingleSmart(file) {
            // Open smart dialog for a single input folder file
            this.openUploadDialog([{ name: file.filename, size: file.size, _inputFolder: true, _inputFilename: file.filename }]);
        },
        formatSize(bytes) {
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / 1024 / 1024).toFixed(1) + ' MB';
        }
    }
};


// =============================================================================
// MigratePage Component (PRD #173, Slice 5) — the one path out of Zotero,
// Citavi, Mendeley & Co.
//
// Five steps, in the order a reader thinks: which manager · what would happen ·
// what comes over · run it · what happened. Nothing is written before step 4,
// and step 5 can take the whole run back, so the preview is allowed to be
// honest rather than reassuring.
// =============================================================================

// The cards the wizard offers, in the order a reader meets them. Deliberately
// NOT the adapter list: `citavi` has no adapter yet and shows as "coming soon"
// until one registers, and Mendeley has no export format of its own — it hands
// out BibTeX, so its card points at the bibtex adapter and only its hint
// differs. Availability, file kind and extensions come from
// GET /api/migration/sources.
const MIGRATE_CARDS = [
    { id: 'zotero', source: 'zotero_rdf' },
    { id: 'citavi', source: 'citavi' },
    { id: 'mendeley', source: 'bibtex' },
    { id: 'bibtex', source: 'bibtex' },
    { id: 'ris', source: 'ris' },
    { id: 'pdf_folder', source: 'pdf_folder' },
];

const MIGRATE_STEPS = ['source', 'analyze', 'options', 'run', 'result'];

// Issue defaults: everything on, every PDF that was found, an OA attempt when
// none was, attach onto an item the library already has, references afterwards.
// `llm_categorize` is the one exception — it stays off unless an LLM role is
// actually bound, because a switch that cannot work is worse than no switch.
function migrateDefaultOptions() {
    return {
        import_abstract: true,
        import_notes: true,
        import_collections: true,
        import_tags: true,
        import_date_added: true,
        keep_cite_keys: true,
        fill_missing: true,
        llm_categorize: false,
        attach_pdfs: 'all',
        oa_fallback: true,
        duplicates: 'attach',
        extract_references: 'later',
    };
}

const MigratePage = {
    template: `
        <div class="p-6 max-w-5xl mx-auto" data-testid="migrate-page">
            <h2 class="text-xl font-semibold text-gray-900 mb-2">{{ $t('migrate.heading') }}</h2>
            <p class="text-sm text-gray-500 mb-6">{{ $t('migrate.intro') }}</p>

            <ol class="lb-migrate-rail" data-testid="migrate-rail">
                <li v-for="(s, i) in steps" :key="s" class="lb-migrate-rail-step"
                    :class="{ 'is-active': s === step, 'is-done': i < stepIndex }">
                    <span class="lb-migrate-rail-dot">{{ i + 1 }}</span>
                    <span class="lb-migrate-rail-label">{{ $t('migrate.step.' + s) }}</span>
                </li>
            </ol>

            <!-- ================= Step 1: Source ================= -->
            <section v-if="step === 'source'" data-testid="migrate-step-source">
                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-6">
                    <button v-for="card in cards" :key="card.id" type="button"
                            class="lb-migrate-card" data-testid="migrate-source-card"
                            :data-card="card.id"
                            :class="{ 'is-selected': card.id === cardId, 'is-disabled': !isAvailable(card) }"
                            :disabled="!isAvailable(card)"
                            @click="selectCard(card)">
                        <span class="lb-migrate-card-head">
                            <span class="lb-migrate-card-title">{{ cardLabel(card) }}</span>
                            <span v-if="!isAvailable(card)" class="lb-migrate-badge">{{ $t('migrate.source.comingSoon') }}</span>
                        </span>
                        <span class="lb-migrate-card-hint">{{ $t('migrate.source.' + card.id + '.hint') }}</span>
                    </button>
                </div>

                <div v-if="activeCard" class="bg-white rounded-xl border border-gray-200 shadow-sm p-5"
                     data-testid="migrate-path-panel">
                    <label class="block text-sm font-medium text-gray-700 mb-1">{{ $t('migrate.path.label') }}</label>
                    <input v-model="path" type="text" data-testid="migrate-path"
                           :placeholder="isFolderSource ? $t('migrate.path.placeholderFolder') : $t('migrate.path.placeholderFile')"
                           class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                    <p class="text-xs text-gray-400 mt-1">{{ $t('migrate.path.hint') }}</p>

                    <div class="flex items-center gap-3 mt-3 flex-wrap">
                        <label class="inline-flex items-center gap-2 bg-white border border-gray-300 text-gray-700 px-3 py-1.5 rounded-lg text-sm cursor-pointer hover:bg-gray-50">
                            <span v-html="icons.upload"></span>
                            {{ $t('migrate.path.pick') }}
                            <input type="file" class="hidden" :accept="acceptAttr" @change="pickFile" />
                        </label>
                        <span class="text-xs text-gray-400">{{ $t('migrate.path.pickHint') }}</span>
                    </div>

                    <label v-if="isFolderSource" class="flex items-center gap-2 mt-3 cursor-pointer">
                        <input type="checkbox" v-model="recursive" data-testid="migrate-recursive"
                               class="w-4 h-4 rounded border-gray-300 text-accent focus:ring-accent" />
                        <span class="text-sm text-gray-700">{{ $t('migrate.recursive') }}</span>
                        <span class="text-xs text-gray-400">{{ $t('migrate.recursiveHint') }}</span>
                    </label>

                    <p v-if="analyzeError" class="mt-3 text-sm text-accent-ink bg-accent-soft rounded-lg px-3 py-2"
                       data-testid="migrate-analyze-error">{{ analyzeError }}</p>

                    <div class="flex justify-end mt-4">
                        <button @click="analyze" :disabled="analyzing || !path.trim()"
                                data-testid="migrate-analyze"
                                class="inline-flex items-center gap-2 bg-accent text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-40 disabled:cursor-not-allowed">
                            <span v-if="analyzing" class="spinner" style="width:14px;height:14px;border-width:2px;border-top-color:#fff;"></span>
                            {{ analyzing ? $t('migrate.analyzing') : $t('migrate.analyze') }}
                        </button>
                    </div>
                </div>
            </section>

            <!-- ================= Step 2: Preview ================= -->
            <section v-else-if="step === 'analyze' && analysis" data-testid="migrate-step-analyze">
                <!-- Metadata export: counts, collections, tags, per-item checklist -->
                <template v-if="!isPdfFolder">
                    <div class="lb-migrate-tiles mb-5">
                        <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ analysis.total }}</span><span>{{ $t('migrate.summary.total') }}</span></div>
                        <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ analysis.matched }}</span><span>{{ $t('migrate.summary.matched') }}</span></div>
                        <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ analysis.new }}</span><span>{{ $t('migrate.summary.new') }}</span></div>
                        <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ analysis.attachments_found }}</span><span>{{ $t('migrate.summary.attachmentsFound') }}</span></div>
                        <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ analysis.attachments_missing }}</span><span>{{ $t('migrate.summary.attachmentsMissing') }}</span></div>
                        <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ (analysis.collections || []).length }}</span><span>{{ $t('migrate.summary.collections') }}</span></div>
                        <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ (analysis.tags || []).length }}</span><span>{{ $t('migrate.summary.tags') }}</span></div>
                    </div>

                    <div v-if="(analysis.collections || []).length" class="mb-4">
                        <p class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">{{ $t('migrate.collections.heading') }}</p>
                        <span v-for="(c, i) in analysis.collections" :key="i"
                              class="inline-block bg-accent-soft text-accent-ink px-2 py-0.5 rounded-full text-xs mr-1 mb-1">{{ c.join(' / ') }}</span>
                    </div>
                    <div v-if="(analysis.tags || []).length" class="mb-4">
                        <p class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">{{ $t('migrate.tags.heading') }}</p>
                        <span v-for="tag in analysis.tags" :key="tag"
                              class="inline-block bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full text-xs mr-1 mb-1">{{ tag }}</span>
                    </div>

                    <div class="flex items-center gap-2 mb-2 flex-wrap">
                        <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider mr-2">{{ $t('migrate.items.heading') }}</span>
                        <button @click="selectAll" data-testid="migrate-select-all" class="lb-migrate-chip">{{ $t('migrate.select.all') }}</button>
                        <button @click="selectNewOnly" data-testid="migrate-select-new" class="lb-migrate-chip">{{ $t('migrate.select.newOnly') }}</button>
                        <button @click="selectNone" data-testid="migrate-select-none" class="lb-migrate-chip">{{ $t('migrate.select.none') }}</button>
                        <span class="text-xs text-gray-400" data-testid="migrate-selected-count">{{ $t('migrate.select.count', { count: selectedKeys.length, total: analysis.total }) }}</span>
                    </div>

                    <div class="border border-gray-200 rounded-lg overflow-hidden max-h-96 overflow-y-auto mb-5">
                        <div v-for="item in analysis.items" :key="item.key"
                             class="flex items-center gap-3 px-3 py-2 border-b border-gray-100 last:border-0 text-sm">
                            <input type="checkbox" :checked="!!selected[item.key]" @change="toggleItem(item.key)"
                                   class="w-4 h-4 rounded border-gray-300 text-accent focus:ring-accent" />
                            <span class="flex-1 truncate text-gray-800">{{ item.title || $t('migrate.items.untitled') }}</span>
                            <span class="text-xs text-gray-400 w-12 text-right">{{ item.year || '' }}</span>
                            <span class="text-xs px-2 py-0.5 rounded-full"
                                  :class="item.matched_paper_id ? 'bg-gray-100 text-gray-500' : 'bg-accent-soft text-accent-ink'">
                                {{ item.matched_paper_id ? $t('migrate.items.matched') : $t('migrate.items.new') }}
                            </span>
                            <span v-if="(item.attachments || []).length" class="text-xs text-gray-500">{{ $t('migrate.items.attachments', { count: item.attachments.length }) }}</span>
                            <span v-if="(item.attachments_missing || []).length" class="text-xs text-accent">{{ $t('migrate.items.missing', { count: item.attachments_missing.length }) }}</span>
                        </div>
                    </div>
                </template>

                <!-- PDF folder: the match table IS the checklist; step 3 is skipped -->
                <template v-else>
                    <div class="lb-migrate-tiles mb-5">
                        <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ analysis.scanned }}</span><span>{{ $t('migrate.pdf.scanned') }}</span></div>
                        <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ analysis.matched }}</span><span>{{ $t('migrate.pdf.matched') }}</span></div>
                        <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ analysis.unmatched_count }}</span><span>{{ $t('migrate.pdf.unmatched') }}</span></div>
                        <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ analysis.already_owned_count }}</span><span>{{ $t('migrate.pdf.alreadyOwned') }}</span></div>
                    </div>

                    <div class="flex items-center gap-2 mb-2 flex-wrap">
                        <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider mr-2">{{ $t('migrate.pdf.heading') }}</span>
                        <button @click="selectAll" data-testid="migrate-select-all" class="lb-migrate-chip">{{ $t('migrate.select.all') }}</button>
                        <button @click="selectNone" data-testid="migrate-select-none" class="lb-migrate-chip">{{ $t('migrate.select.none') }}</button>
                        <span class="text-xs text-gray-400" data-testid="migrate-selected-count">{{ $t('migrate.select.count', { count: selectedMatches.length, total: analysis.matched }) }}</span>
                    </div>

                    <div class="border border-gray-200 rounded-lg overflow-hidden max-h-96 overflow-y-auto mb-5">
                        <div v-for="m in analysis.matches" :key="m.pdf_path"
                             class="flex items-center gap-3 px-3 py-2 border-b border-gray-100 last:border-0 text-sm">
                            <input type="checkbox" :checked="!!selected[m.pdf_path]" @change="toggleItem(m.pdf_path)"
                                   class="w-4 h-4 rounded border-gray-300 text-accent focus:ring-accent" />
                            <span class="flex-1 truncate text-gray-600">{{ m.pdf_name }}</span>
                            <span class="flex-1 truncate text-gray-800">{{ m.paper_title }}</span>
                            <span class="text-xs text-gray-400">{{ m.strategy }} · {{ Math.round((m.confidence || 0) * 100) }}%</span>
                        </div>
                    </div>

                    <div v-if="(analysis.unmatched || []).length" class="mb-5">
                        <p class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">{{ $t('migrate.pdf.unmatchedHeading') }}</p>
                        <p class="text-xs text-gray-400 mb-2">{{ $t('migrate.pdf.importUnmatchedHint') }}</p>
                        <button @click="importUnmatched" :disabled="importingUnmatched"
                                data-testid="migrate-import-unmatched"
                                class="inline-flex items-center gap-2 bg-white border border-gray-300 text-gray-700 px-3 py-1.5 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-40">
                            <span v-html="icons.upload"></span>
                            {{ $t('migrate.pdf.importUnmatched') }}
                        </button>
                        <p v-if="unmatchedImported" class="text-xs text-gray-500 mt-2" data-testid="migrate-unmatched-done">{{ $t('migrate.pdf.imported', { count: unmatchedImported }) }}</p>
                    </div>
                </template>

                <div class="flex justify-between">
                    <button @click="step = 'source'" class="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">{{ $t('migrate.back') }}</button>
                    <button @click="afterPreview" data-testid="migrate-continue"
                            class="bg-accent text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink">
                        {{ isPdfFolder ? $t('migrate.start') : $t('migrate.next') }}
                    </button>
                </div>
            </section>

            <!-- ================= Step 3: Options ================= -->
            <section v-else-if="step === 'options'" data-testid="migrate-step-options">
                <div class="bg-white rounded-xl border border-gray-200 shadow-sm p-5 mb-4">
                    <h3 class="text-base font-semibold text-gray-900 mb-3">{{ $t('migrate.options.dataHeading') }}</h3>
                    <div class="space-y-2.5">
                        <label v-for="flag in dataFlags" :key="flag.key" class="flex items-start gap-3 cursor-pointer">
                            <input type="checkbox" v-model="options[flag.key]" :data-testid="'migrate-opt-' + flag.key"
                                   :disabled="flag.key === 'llm_categorize' && !llmReady"
                                   class="w-4 h-4 mt-0.5 rounded border-gray-300 text-accent focus:ring-accent" />
                            <span>
                                <span class="text-sm font-medium text-gray-700">{{ $t(flag.label) }}</span>
                                <span class="block text-xs text-gray-400">{{ $t(flag.hint) }}</span>
                                <span v-if="flag.key === 'llm_categorize' && !llmReady" class="block text-xs text-accent">{{ $t('migrate.options.llmOffHint') }}</span>
                            </span>
                        </label>
                    </div>
                </div>

                <div class="bg-white rounded-xl border border-gray-200 shadow-sm p-5 mb-4">
                    <h3 class="text-base font-semibold text-gray-900 mb-3">{{ $t('migrate.options.pdfHeading') }}</h3>
                    <div class="space-y-2.5">
                        <label v-for="mode in ['all', 'selected', 'none']" :key="mode" class="flex items-start gap-3 cursor-pointer">
                            <input type="radio" v-model="options.attach_pdfs" :value="mode"
                                   :data-testid="'migrate-attach-' + mode"
                                   class="w-4 h-4 mt-0.5 border-gray-300 text-accent focus:ring-accent" />
                            <span>
                                <span class="text-sm font-medium text-gray-700">{{ $t('migrate.options.attach.' + mode) }}</span>
                                <span class="block text-xs text-gray-400">{{ $t('migrate.options.attach.' + mode + 'Hint') }}</span>
                            </span>
                        </label>
                        <label class="flex items-start gap-3 cursor-pointer pt-1">
                            <input type="checkbox" v-model="options.oa_fallback" data-testid="migrate-opt-oa_fallback"
                                   class="w-4 h-4 mt-0.5 rounded border-gray-300 text-accent focus:ring-accent" />
                            <span>
                                <span class="text-sm font-medium text-gray-700">{{ $t('migrate.options.oaFallback') }}</span>
                                <span class="block text-xs text-gray-400">{{ $t('migrate.options.oaFallbackHint') }}</span>
                            </span>
                        </label>
                    </div>
                    <h4 class="text-sm font-semibold text-gray-800 mt-4 mb-2">{{ $t('migrate.options.duplicatesHeading') }}</h4>
                    <div class="space-y-2.5">
                        <label v-for="mode in ['attach', 'skip']" :key="mode" class="flex items-start gap-3 cursor-pointer">
                            <input type="radio" v-model="options.duplicates" :value="mode"
                                   :data-testid="'migrate-duplicates-' + mode"
                                   class="w-4 h-4 mt-0.5 border-gray-300 text-accent focus:ring-accent" />
                            <span>
                                <span class="text-sm font-medium text-gray-700">{{ $t('migrate.options.duplicates.' + mode) }}</span>
                                <span class="block text-xs text-gray-400">{{ $t('migrate.options.duplicates.' + mode + 'Hint') }}</span>
                            </span>
                        </label>
                    </div>
                </div>

                <div class="bg-white rounded-xl border border-gray-200 shadow-sm p-5 mb-4">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">{{ $t('migrate.options.refsHeading') }}</h3>
                    <p class="text-xs text-gray-500 mb-3" data-testid="migrate-refs-cost">{{ $t('migrate.options.refsCost', { count: pdfCandidateCount }) }}</p>
                    <div class="space-y-2.5">
                        <label v-for="mode in ['all', 'later', 'none']" :key="mode" class="flex items-start gap-3 cursor-pointer">
                            <input type="radio" v-model="options.extract_references" :value="mode"
                                   :data-testid="'migrate-refs-' + mode"
                                   class="w-4 h-4 mt-0.5 border-gray-300 text-accent focus:ring-accent" />
                            <span>
                                <span class="text-sm font-medium text-gray-700">{{ $t('migrate.options.refs.' + mode) }}</span>
                                <span class="block text-xs text-gray-400">{{ $t('migrate.options.refs.' + mode + 'Hint') }}</span>
                            </span>
                        </label>
                    </div>
                </div>

                <div class="flex justify-between">
                    <button @click="step = 'analyze'" class="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">{{ $t('migrate.back') }}</button>
                    <button @click="startRun" data-testid="migrate-start"
                            class="bg-accent text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink">{{ $t('migrate.start') }}</button>
                </div>
            </section>

            <!-- ================= Step 4: Run ================= -->
            <section v-else-if="step === 'run'" data-testid="migrate-step-run">
                <div class="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
                    <div class="flex items-center justify-between mb-2">
                        <h3 class="text-base font-semibold text-gray-900">{{ $t('migrate.run.heading') }}</h3>
                        <span class="text-sm text-gray-400">{{ runPercent }}%</span>
                    </div>
                    <div class="w-full bg-gray-200 rounded-full h-2.5 mb-4">
                        <div class="bg-accent h-2.5 rounded-full transition-all duration-300" :style="{ width: runPercent + '%' }"></div>
                    </div>
                    <div class="space-y-1.5 max-h-64 overflow-y-auto" data-testid="migrate-run-log">
                        <div v-for="(s, i) in runSteps" :key="i" class="flex items-center gap-2 text-xs"
                             :class="s.error ? 'text-accent' : s.done ? 'text-accent' : 'text-gray-500'">
                            <span v-if="s.done" v-html="icons.check" style="width:12px;height:12px;"></span>
                            <span v-else-if="s.error" class="text-accent font-bold">!</span>
                            <span v-else class="spinner" style="width:12px;height:12px;border-width:1.5px;"></span>
                            {{ s.message }}
                        </div>
                    </div>
                    <div class="flex justify-end mt-4">
                        <button v-if="running" @click="cancelRun" data-testid="migrate-cancel"
                                class="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">{{ $t('migrate.run.cancel') }}</button>
                    </div>
                </div>
            </section>

            <!-- ================= Step 5: Result ================= -->
            <section v-else-if="step === 'result'" data-testid="migrate-step-result">
                <div v-if="cancelled" class="mb-4 text-sm text-accent-ink bg-accent-soft rounded-lg px-3 py-2"
                     data-testid="migrate-cancelled">{{ $t('migrate.run.cancelled') }}</div>
                <div v-if="runError" class="mb-4 text-sm text-accent-ink bg-accent-soft rounded-lg px-3 py-2"
                     data-testid="migrate-run-error">{{ runError }}</div>

                <h3 class="text-base font-semibold text-gray-900 mb-3">{{ $t('migrate.result.heading') }}</h3>
                <div class="lb-migrate-tiles mb-5">
                    <div class="lb-migrate-tile" data-testid="migrate-result-created"><span class="lb-migrate-tile-num">{{ runCounts.created }}</span><span>{{ $t('migrate.result.created') }}</span></div>
                    <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ runCounts.matched }}</span><span>{{ $t('migrate.result.matched') }}</span></div>
                    <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ runCounts.attached }}</span><span>{{ $t('migrate.result.attached') }}</span></div>
                    <div class="lb-migrate-tile"><span class="lb-migrate-tile-num">{{ runCounts.failed }}</span><span>{{ $t('migrate.result.failed') }}</span></div>
                </div>

                <div v-if="runResults.length" class="border border-gray-200 rounded-lg overflow-hidden max-h-96 overflow-y-auto mb-5">
                    <div v-for="(r, i) in runResults" :key="i"
                         class="flex items-center gap-3 px-3 py-2 border-b border-gray-100 last:border-0 text-sm">
                        <span class="flex-1 truncate text-gray-800">{{ r.title || $t('migrate.items.untitled') }}</span>
                        <span class="text-xs text-gray-500">{{ $t('migrate.result.status.' + r.status) }}</span>
                        <router-link v-if="r.paper_id" :to="{ name: 'paper', params: { id: r.paper_id } }"
                                     class="text-xs text-accent hover:underline">{{ $t('common.open') }}</router-link>
                        <span v-if="r.error" class="text-xs text-accent truncate max-w-xs">{{ r.error }}</span>
                    </div>
                </div>

                <!-- References: requested up front, or offered here when the
                     reader chose "later". -->
                <div v-if="!isPdfFolder && (refs.state !== 'idle' || options.extract_references === 'later')"
                     class="bg-white rounded-xl border border-gray-200 shadow-sm p-5 mb-4" data-testid="migrate-refs-panel">
                    <h4 class="text-sm font-semibold text-gray-900 mb-2">{{ $t('migrate.refs.heading') }}</h4>
                    <div v-if="refs.state === 'running'">
                        <div class="w-full bg-gray-200 rounded-full h-2 mb-2">
                            <div class="bg-accent h-2 rounded-full" :style="{ width: refs.percent + '%' }"></div>
                        </div>
                        <p class="text-xs text-gray-500">{{ refs.message || $t('migrate.refs.running') }}</p>
                    </div>
                    <p v-else-if="refs.state === 'done'" class="text-sm text-gray-600" data-testid="migrate-refs-done">
                        {{ $t('migrate.refs.done', { processed: refs.processed, refs: refs.total_references, inLibrary: refs.total_in_library }) }}
                    </p>
                    <p v-else-if="refs.state === 'failed'" class="text-sm text-accent">{{ $t('migrate.refs.failed') }}</p>
                    <button v-else @click="extractReferences" :disabled="!referencePaperIds.length"
                            data-testid="migrate-refs-start"
                            class="inline-flex items-center gap-2 bg-white border border-gray-300 text-gray-700 px-3 py-1.5 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-40">
                        <span v-html="icons.sparkle"></span>
                        {{ referencePaperIds.length ? $t('migrate.refs.start') : $t('migrate.refs.none') }}
                    </button>
                </div>

                <div class="flex items-center gap-3 flex-wrap">
                    <button @click="undo" :disabled="undoing || !runId" data-testid="migrate-undo"
                            class="inline-flex items-center gap-2 bg-white border border-gray-300 text-gray-700 px-4 py-2 rounded-lg text-sm hover:bg-gray-50 disabled:opacity-40">
                        <span v-html="icons.back"></span>
                        {{ $t('migrate.result.undo') }}
                    </button>
                    <button v-if="missingAttachments" @click="matchPdfFolder" data-testid="migrate-match-folder"
                            class="inline-flex items-center gap-2 bg-white border border-gray-300 text-gray-700 px-4 py-2 rounded-lg text-sm hover:bg-gray-50">
                        <span v-html="icons.folder"></span>
                        {{ $t('migrate.result.matchPdfFolder') }}
                    </button>
                    <button @click="restart" class="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">{{ $t('migrate.result.again') }}</button>
                    <span v-if="missingAttachments" class="text-xs text-gray-400">{{ $t('migrate.result.matchPdfFolderHint', { count: missingAttachments }) }}</span>
                    <span v-if="undoResult" class="text-xs text-gray-500" data-testid="migrate-undone">{{ $t('migrate.result.undone', { count: undoResult.deleted }) }}</span>
                </div>
            </section>
        </div>
    `,
    data() {
        return {
            icons,
            steps: MIGRATE_STEPS,
            cards: MIGRATE_CARDS,
            sources: [],
            step: 'source',
            cardId: '',
            path: '',
            recursive: false,
            analyzing: false,
            analysis: null,
            analyzeError: '',
            selected: {},
            options: migrateDefaultOptions(),
            llmReady: false,
            running: false,
            cancelled: false,
            runPercent: 0,
            runSteps: [],
            runResults: [],
            runCounts: { created: 0, matched: 0, attached: 0, failed: 0 },
            runId: '',
            runError: '',
            missingAttachments: 0,
            undoing: false,
            undoResult: null,
            importingUnmatched: false,
            unmatchedImported: 0,
            refs: { state: 'idle', percent: 0, message: '', processed: 0, total_references: 0, total_in_library: 0 },
            dataFlags: [
                { key: 'import_abstract', label: 'migrate.options.abstract', hint: 'migrate.options.abstractHint' },
                { key: 'import_notes', label: 'migrate.options.notes', hint: 'migrate.options.notesHint' },
                { key: 'import_collections', label: 'migrate.options.collections', hint: 'migrate.options.collectionsHint' },
                { key: 'import_tags', label: 'migrate.options.tags', hint: 'migrate.options.tagsHint' },
                { key: 'import_date_added', label: 'migrate.options.dateAdded', hint: 'migrate.options.dateAddedHint' },
                { key: 'keep_cite_keys', label: 'migrate.options.citeKeys', hint: 'migrate.options.citeKeysHint' },
                { key: 'fill_missing', label: 'migrate.options.fillMissing', hint: 'migrate.options.fillMissingHint' },
                { key: 'llm_categorize', label: 'migrate.options.llmCategorize', hint: 'migrate.options.llmCategorizeHint' },
            ],
            _abort: null,
        };
    },
    computed: {
        stepIndex() { return MIGRATE_STEPS.indexOf(this.step); },
        activeCard() { return MIGRATE_CARDS.find(c => c.id === this.cardId) || null; },
        sourceId() { return this.activeCard ? this.activeCard.source : ''; },
        sourceMeta() { return this.sources.find(s => s.id === this.sourceId) || null; },
        isPdfFolder() { return this.sourceId === 'pdf_folder'; },
        isFolderSource() {
            const meta = this.sourceMeta;
            return !!meta && (meta.kind === 'folder' || meta.accepts_folder);
        },
        acceptAttr() {
            const meta = this.sourceMeta;
            return meta && meta.extensions ? meta.extensions.join(',') : '';
        },
        selectedKeys() {
            if (!this.analysis || !this.analysis.items) return [];
            return this.analysis.items.filter(i => this.selected[i.key]).map(i => i.key);
        },
        selectedMatches() {
            if (!this.analysis || !this.analysis.matches) return [];
            return this.analysis.matches.filter(m => this.selected[m.pdf_path]);
        },
        // What the reference option costs: every selected entry that brings a
        // file, because only those end up with a PDF to read.
        pdfCandidateCount() {
            if (!this.analysis || !this.analysis.items) return 0;
            return this.analysis.items.filter(
                i => this.selected[i.key] && (i.attachments || []).length).length;
        },
        // Exactly the items of THIS run that now hold a PDF — what the bulk
        // extraction is pointed at, so it never wanders into the library.
        referencePaperIds() {
            const ids = [];
            for (const r of this.runResults) {
                if (!r.paper_id) continue;
                if (r.attached || (r.status === 'matched' && this.itemHasPdf(r.key))) {
                    if (!ids.includes(r.paper_id)) ids.push(r.paper_id);
                }
            }
            return ids;
        },
    },
    async created() {
        try {
            const data = await api('/api/migration/sources');
            this.sources = data.sources || [];
        } catch (e) { this.sources = []; }
        try {
            const status = await api('/api/llm/status');
            this.llmReady = !!(status && status.reasoning);
        } catch (e) { this.llmReady = false; }
        // A categorisation pass needs a bound role; offering it without one
        // would tick a box that silently does nothing.
        this.options.llm_categorize = this.llmReady;
        const wanted = this.$route.query.source;
        if (wanted) {
            const card = MIGRATE_CARDS.find(c => c.id === wanted)
                || MIGRATE_CARDS.find(c => c.source === wanted);
            if (card && this.isAvailable(card)) this.cardId = card.id;
        }
    },
    beforeUnmount() {
        if (this._abort) this._abort.abort();
    },
    methods: {
        isAvailable(card) { return this.sources.some(s => s.id === card.source); },
        // A card whose id IS an adapter id wears the server's label; the two
        // cards without an adapter of their own (Citavi, Mendeley) carry theirs
        // in the catalog.
        cardLabel(card) {
            const meta = this.sources.find(s => s.id === card.source);
            if (meta && card.id === card.source) return t(meta.label_key);
            return t('migrate.source.' + card.id + '.label');
        },
        selectCard(card) {
            if (!this.isAvailable(card)) return;
            this.cardId = card.id;
            this.analyzeError = '';
        },
        pickFile(event) {
            const file = event.target.files && event.target.files[0];
            // Browsers hand over a name, packaged runtimes a real path. Either
            // way the field stays editable — the hint says so.
            if (file) this.path = file.path || file.name;
            event.target.value = '';
        },
        async analyze() {
            this.analyzing = true;
            this.analyzeError = '';
            try {
                this.analysis = await api('/api/migration/analyze', {
                    method: 'POST',
                    body: JSON.stringify({ source: this.sourceId, path: this.path.trim(),
                                           recursive: this.recursive }),
                });
                this.selectAll();
                this.step = 'analyze';
            } catch (e) {
                this.analyzeError = e.message;
            }
            this.analyzing = false;
        },
        rows() {
            if (!this.analysis) return [];
            return this.isPdfFolder ? (this.analysis.matches || []) : (this.analysis.items || []);
        },
        rowKey(row) { return this.isPdfFolder ? row.pdf_path : row.key; },
        selectAll() {
            const next = {};
            for (const row of this.rows()) next[this.rowKey(row)] = true;
            this.selected = next;
        },
        selectNewOnly() {
            const next = {};
            for (const row of this.rows()) next[this.rowKey(row)] = !row.matched_paper_id;
            this.selected = next;
        },
        selectNone() { this.selected = {}; },
        toggleItem(key) { this.selected = { ...this.selected, [key]: !this.selected[key] }; },
        itemHasPdf(key) {
            const items = (this.analysis && this.analysis.items) || [];
            const item = items.find(i => i.key === key);
            return !!(item && item.matched_has_pdf);
        },
        // The PDF-folder run has no data/PDF/reference options to ask about —
        // the match table already said everything.
        afterPreview() {
            if (this.isPdfFolder) this.startRun();
            else this.step = 'options';
        },
        commitBody() {
            if (this.isPdfFolder) {
                return {
                    source: this.sourceId,
                    path: this.analysis.path,
                    recursive: this.recursive,
                    matches: this.selectedMatches.map(m => ({ pdf_path: m.pdf_path, paper_id: m.paper_id })),
                };
            }
            return {
                source: this.sourceId,
                path: this.path.trim(),
                recursive: this.recursive,
                options: { ...this.options },
                selected_keys: this.selectedKeys,
                attachment_keys: this.options.attach_pdfs === 'selected' ? this.selectedKeys : null,
            };
        },
        async startRun() {
            this.step = 'run';
            this.running = true;
            this.cancelled = false;
            this.runPercent = 0;
            this.runSteps = [];
            this.runResults = [];
            this.runError = '';
            this.runId = '';
            this.refs = { state: 'idle', percent: 0, message: '', processed: 0, total_references: 0, total_in_library: 0 };
            this.undoResult = null;
            this._abort = new AbortController();
            let complete = null;
            try {
                const resp = await fetch('/api/migration/commit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.commitBody()),
                    signal: this._abort.signal,
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(translateDetail(err.detail, resp.status));
                }
                await readSseStream(resp, (evt) => {
                    if (evt.type === 'progress') {
                        this.runPercent = evt.percent || 0;
                        this.runSteps.forEach(s => { if (!s.done && !s.error) s.done = true; });
                        this.runSteps.push({ message: translateDetail(evt.message), done: false });
                    } else if (evt.type === 'complete') {
                        this.runSteps.forEach(s => { if (!s.error) s.done = true; });
                        this.runPercent = 100;
                        complete = evt;
                    } else if (evt.type === 'error') {
                        this.runSteps.push({ message: translateDetail(evt.message), error: true });
                        this.runError = translateDetail(evt.message);
                    }
                });
            } catch (e) {
                // An aborted fetch is the cancel button doing its job, not a
                // failure: the server finishes the entry it is on and stops.
                if (e.name === 'AbortError') this.cancelled = true;
                else this.runError = e.message;
            }
            this._abort = null;
            this.running = false;
            if (complete) {
                this.runId = complete.run_id || '';
                this.runResults = complete.results || [];
                this.runCounts = {
                    created: complete.created || 0,
                    matched: complete.matched || 0,
                    attached: complete.attached || 0,
                    failed: complete.failed || 0,
                };
                this.missingAttachments = this.isPdfFolder ? 0 : ((this.analysis && this.analysis.attachments_missing) || 0);
            }
            this.step = 'result';
            window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            if (complete && this.options.extract_references === 'all' && !this.isPdfFolder) {
                await this.extractReferences();
            }
        },
        cancelRun() { if (this._abort) this._abort.abort(); },
        async extractReferences() {
            const paperIds = this.referencePaperIds;
            if (!paperIds.length) return;
            this.refs = { state: 'running', percent: 0, message: '', processed: 0, total_references: 0, total_in_library: 0 };
            try {
                const resp = await fetch('/api/papers/bulk-extract-references', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ paper_ids: paperIds }),
                });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                let done = null;
                await readSseStream(resp, (evt) => {
                    if (evt.type === 'progress') {
                        this.refs.percent = evt.percent || 0;
                        this.refs.message = evt.message ? translateDetail(evt.message) : '';
                    } else if (evt.type === 'complete') {
                        done = evt;
                    }
                });
                this.refs = {
                    state: 'done', percent: 100, message: '',
                    processed: (done && done.processed) || 0,
                    total_references: (done && done.total_references) || 0,
                    total_in_library: (done && done.total_in_library) || 0,
                };
            } catch (e) {
                this.refs = { ...this.refs, state: 'failed' };
            }
        },
        async undo() {
            if (!this.runId || !confirm(t('migrate.result.undoConfirm'))) return;
            this.undoing = true;
            try {
                this.undoResult = await api('/api/migration/undo', {
                    method: 'POST',
                    body: JSON.stringify({ run_id: this.runId }),
                });
                this.runId = '';
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            } catch (e) {
                alert(t('migrate.result.undoFailed') + ' ' + e.message);
            }
            this.undoing = false;
        },
        async importUnmatched() {
            const paths = (this.analysis.unmatched || []).map(u => u.pdf_path);
            if (!paths.length) return;
            this.importingUnmatched = true;
            try {
                const result = await api('/api/migration/pdf-folder/import-unmatched', {
                    method: 'POST',
                    body: JSON.stringify({ paths }),
                });
                this.unmatchedImported = result.count || 0;
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
            this.importingUnmatched = false;
        },
        // "The PDFs were named but not found" has exactly one repair: point the
        // wizard at the folder they actually live in.
        matchPdfFolder() {
            this.restart();
            this.cardId = 'pdf_folder';
        },
        restart() {
            this.step = 'source';
            this.cardId = '';
            this.path = '';
            this.analysis = null;
            this.selected = {};
            this.runResults = [];
            this.runId = '';
            this.undoResult = null;
            this.unmatchedImported = 0;
            this.options = migrateDefaultOptions();
            this.options.llm_categorize = this.llmReady;
            this.refs = { state: 'idle', percent: 0, message: '', processed: 0, total_references: 0, total_in_library: 0 };
        },
    },
};


// =============================================================================
// SettingsPage Component
// =============================================================================

// =============================================================================
// Add-on settings section (slot "settings", #190)
// -----------------------------------------------------------------------------
// The settings of one installed Add-on, in its Marketplace slide-over (#193;
// Settings no longer carries Add-on cards). The values live in
// the Add-on's own namespace of plugins.json (GET/PUT /api/plugins/{id}/
// settings). An Add-on that registers a "settings" component gets
// `{addonId, fields, values, save}` and draws its own form; otherwise the core
// renders the Manifest's declared fields: string, path (text), bool
// (checkbox), secret (masked — never sent back, only `key_hint` shown; an
// empty secret input means "unchanged" and goes out as null).
// =============================================================================

const AddonSettingsSection = {
    props: { addonId: { type: String, required: true } },
    template: `
        <div v-if="loaded && (custom || fields.length)" class="mt-2 mb-2 pl-3 border-l-2 border-gray-100"
             :data-testid="'addon-settings-' + addonId">
            <h4 class="text-xs font-semibold uppercase text-mute mb-2">{{ $t('marketplace.settings') }}</h4>
            <component v-if="custom" :is="custom.component" :addon-id="addonId"
                       :fields="fields" :values="values" :save="save" />
            <form v-else class="space-y-3" @submit.prevent="saveGeneric">
                <div v-for="f in fields" :key="f.key">
                    <label v-if="f.type === 'bool'" class="inline-flex items-center gap-2 text-sm text-gray-700">
                        <input type="checkbox" v-model="drafts[f.key]" :data-field="f.key" />
                        {{ fieldLabel(f) }}
                    </label>
                    <template v-else>
                        <label class="block text-sm font-medium text-gray-700 mb-1" :for="inputId(f)">{{ fieldLabel(f) }}</label>
                        <input :id="inputId(f)" :data-field="f.key" v-model="drafts[f.key]"
                               :type="f.type === 'secret' ? 'password' : 'text'" autocomplete="off"
                               :placeholder="f.type === 'secret' && secretSet(f) ? $t('settings.addons.secretKeep') : ''"
                               class="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent" />
                        <p v-if="f.type === 'secret' && secretSet(f)" class="text-xs text-gray-500 mt-1" :data-secret-hint="f.key">
                            {{ $t('settings.addons.secretStored', { hint: values[f.key].key_hint }) }}
                        </p>
                    </template>
                </div>
                <div class="flex items-center gap-3">
                    <button type="submit" :disabled="saving" :data-testid="'addon-settings-save-' + addonId"
                            class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 hover:bg-gray-50">
                        {{ $t('settings.addons.save') }}
                    </button>
                    <span v-if="saved" class="text-xs text-gray-500">{{ $t('settings.addons.saved') }}</span>
                    <span v-if="error" class="text-xs text-red-600">{{ error }}</span>
                </div>
            </form>
        </div>
    `,
    data() {
        return { loaded: false, fields: [], values: {}, drafts: {}, saving: false, saved: false, error: '' };
    },
    computed: {
        custom() {
            return addonSlots('settings').find((s) => s.addon === this.addonId) || null;
        },
    },
    created() {
        this.load();
    },
    methods: {
        async load() {
            try {
                const data = await api(`/api/plugins/${encodeURIComponent(this.addonId)}/settings`);
                this.apply(data);
            } catch (e) {
                this.fields = [];
            }
            this.loaded = true;
        },
        apply(data) {
            this.fields = Array.isArray(data.fields) ? data.fields : [];
            this.values = data.values || {};
            const drafts = {};
            for (const f of this.fields) {
                const v = this.values[f.key];
                if (f.type === 'secret') drafts[f.key] = '';
                else if (f.type === 'bool') drafts[f.key] = v === true || v === 'true';
                else drafts[f.key] = v == null ? '' : String(v);
            }
            this.drafts = drafts;
        },
        inputId(f) {
            return `addon-${this.addonId}-${f.key}`;
        },
        // Declared label key, then the convention `<id>.settings.<key>`, then the key.
        fieldLabel(f) {
            for (const key of [f.label, `${this.addonId}.settings.${f.key}`]) {
                if (key && inNamespace(key, this.addonId)) {
                    const text = t(key);
                    if (!text.startsWith('⟦')) return text;
                }
            }
            return f.key;
        },
        secretSet(f) {
            const v = this.values[f.key];
            return !!(v && typeof v === 'object' && v.has_key);
        },
        // The one write path, also handed to a custom component.
        async save(values) {
            const data = await api(`/api/plugins/${encodeURIComponent(this.addonId)}/settings`, {
                method: 'PUT',
                body: JSON.stringify({ values }),
            });
            this.apply(data);
            return this.values;
        },
        async saveGeneric() {
            const values = {};
            for (const f of this.fields) {
                const d = this.drafts[f.key];
                if (f.type === 'secret') values[f.key] = d ? d : null;
                else if (f.type === 'bool') values[f.key] = !!d;
                else values[f.key] = d == null ? '' : String(d);
            }
            this.saving = true;
            this.saved = false;
            this.error = '';
            try {
                await this.save(values);
                this.saved = true;
            } catch (e) {
                this.error = t('error.generic', { message: e.message || t('error.unknown') });
            }
            this.saving = false;
        },
    },
};

const SettingsPage = {
    template: `
        <div class="p-6 max-w-3xl mx-auto">
            <h2 class="text-xl font-semibold text-gray-900 mb-6">{{ $t('nav.settings') }}</h2>

            <!-- Tab Navigation -->
            <div class="flex border-b border-gray-200 mb-6 flex-wrap">
                <button @click="activeTab = 'general'"
                        class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px"
                        :class="activeTab === 'general' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'">
                    {{ $t('settings.tabGeneral') }}
                </button>
                <button @click="activeTab = 'llm'"
                        data-testid="llm-tab"
                        class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px"
                        :class="activeTab === 'llm' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'">
                    {{ $t('settings.tabLlm') }}
                </button>
                <button @click="activeTab = 'appearance'"
                        class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px"
                        :class="activeTab === 'appearance' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'">
                    {{ $t('settings.tabAppearance') }}
                </button>
                <button @click="activeTab = 'columns'"
                        class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px"
                        :class="activeTab === 'columns' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'">
                    {{ $t('settings.tabColumns') }}
                </button>
                <button @click="activeTab = 'export'"
                        class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px"
                        :class="activeTab === 'export' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'">
                    {{ $t('settings.tabExportImport') }}
                </button>
                <button @click="activeTab = 'license'"
                        data-testid="license-tab"
                        class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px"
                        :class="activeTab === 'license' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'">
                    {{ $t('settings.tabLicense') }}
                </button>
            </div>

            <!-- ========== Allgemein Tab ========== -->
            <div v-if="activeTab === 'general'">

                <!-- Wartung -->
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">{{ $t('settings.maintenance') }}</h3>
                    <p class="text-sm text-gray-500 mb-5">{{ $t('settings.maintenanceHint') }}</p>

                    <!-- Komplett-Refresh -->
                    <div class="mb-5 pb-5 border-b border-gray-100">
                        <p class="text-sm font-medium text-gray-700 mb-1">{{ $t('settings.fullRefresh') }}</p>
                        <p class="text-xs text-gray-400 mb-3">{{ $t('settings.maintenanceDetail') }}</p>
                        <button @click="fullRefresh"
                                :disabled="fullRefreshRunning"
                                class="flex items-center gap-2 bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-50 transition-colors">
                            <div v-if="fullRefreshRunning" class="spinner" style="width:14px;height:14px;border-width:2px;border-color:white transparent transparent transparent"></div>
                            <span v-else v-html="icons.refresh"></span>
                            {{ $t('settings.startFullRefresh') }}
                        </button>

                        <!-- Fortschrittsanzeige -->
                        <div v-if="fullRefreshRunning || fullRefreshDone" class="mt-4">
                            <div class="flex items-center justify-between text-xs text-gray-500 mb-1">
                                <span>{{ fullRefreshMessage }}</span>
                                <span>{{ fullRefreshPercent }}%</span>
                            </div>
                            <div class="w-full bg-gray-200 rounded-full h-2 mb-3">
                                <div class="bg-accent h-2 rounded-full transition-all duration-300"
                                     :style="{ width: fullRefreshPercent + '%' }"></div>
                            </div>
                            <!-- Statistiken -->
                            <div v-if="fullRefreshStats" class="grid grid-cols-3 gap-2 text-xs">
                                <div class="bg-gray-50 rounded-lg p-2 text-center">
                                    <div class="font-semibold text-gray-800">{{ fullRefreshStats.page_count }}</div>
                                    <div class="text-gray-500">{{ $t('settings.statPageCount') }}</div>
                                </div>
                                <div class="bg-gray-50 rounded-lg p-2 text-center">
                                    <div class="font-semibold text-gray-800">{{ fullRefreshStats.validated }}</div>
                                    <div class="text-gray-500">{{ $t('detail.metadata') }}</div>
                                </div>
                                <div class="bg-gray-50 rounded-lg p-2 text-center">
                                    <div class="font-semibold text-gray-800">{{ fullRefreshStats.abstracts }}</div>
                                    <div class="text-gray-500">{{ $t('settings.statAbstracts') }}</div>
                                </div>
                                <div class="bg-gray-50 rounded-lg p-2 text-center">
                                    <div class="font-semibold text-gray-800">{{ fullRefreshStats.categorized }}</div>
                                    <div class="text-gray-500">{{ $t('settings.statCategorised') }}</div>
                                </div>
                                <div class="bg-gray-50 rounded-lg p-2 text-center">
                                    <div class="font-semibold text-gray-800">{{ fullRefreshStats.openalex }}</div>
                                    <div class="text-gray-500">OpenAlex</div>
                                </div>
                                <div class="bg-gray-50 rounded-lg p-2 text-center">
                                    <div class="font-semibold text-gray-800">{{ fullRefreshStats.chunks }}</div>
                                    <div class="text-gray-500">{{ $t('settings.statRagChunks') }}</div>
                                </div>
                            </div>
                            <p v-if="fullRefreshDone" class="text-sm text-accent font-medium mt-3">✓ {{ $t('settings.refreshDone') }}</p>
                            <p v-if="fullRefreshStats && fullRefreshStats.errors > 0" class="text-xs text-accent mt-1">{{ $t('settings.refreshErrors', { count: fullRefreshStats.errors }) }}</p>
                        </div>
                    </div>

                    <!-- Nur Seitenanzahl -->
                    <div class="flex items-center gap-3">
                        <button @click="updatePageCounts"
                                :disabled="pageCountUpdating || fullRefreshRunning"
                                class="flex items-center gap-2 bg-gray-100 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-200 disabled:opacity-50 transition-colors">
                            <div v-if="pageCountUpdating" class="spinner" style="width:14px;height:14px;border-width:2px"></div>
                            <span v-else v-html="icons.refresh"></span>
                            {{ $t('settings.pageCountOnly') }}
                        </button>
                        <span v-if="pageCountResult" class="text-sm" :class="pageCountResult.ok ? 'text-accent' : 'text-accent'">
                            {{ pageCountResult.msg }}
                        </span>
                    </div>

                    <!-- Verknüpfungen neu aufbauen -->
                    <div class="flex items-center gap-3 mt-3 pt-3 border-t border-gray-100">
                        <button @click="rebuildLinks"
                                :disabled="rebuildLinksRunning || fullRefreshRunning"
                                class="flex items-center gap-2 bg-gray-100 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-200 disabled:opacity-50 transition-colors">
                            <div v-if="rebuildLinksRunning" class="spinner" style="width:14px;height:14px;border-width:2px"></div>
                            <span v-else>🔗</span>
                            {{ $t('settings.rebuildLinks') }}
                        </button>
                        <span v-if="rebuildLinksResult" class="text-sm" :class="rebuildLinksResult.ok ? 'text-accent' : 'text-accent'">
                            {{ rebuildLinksResult.msg }}
                        </span>
                    </div>

                    <!-- Semantischer Index -->
                    <div class="mt-3 pt-3 border-t border-gray-100">
                        <p class="text-sm font-medium text-gray-700 mb-1">{{ $t('settings.semanticIndex') }}</p>
                        <p class="text-xs text-gray-400 mb-3">
                            <template v-if="embStatus && embStatus.model">
                                {{ $t('settings.embedStatus', { model: embStatus.model, indexed: embStatus.indexed, total: embStatus.total, chunksIndexed: embStatus.chunks_indexed, chunksTotal: embStatus.chunks_total }) }}
                            </template>
                            <template v-else>
                                {{ $t('settings.embedNotConfigured') }}
                            </template>
                        </p>
                        <div class="flex items-center gap-3 flex-wrap">
                            <button @click="reindexEmbeddings('papers')"
                                    :disabled="embRunning || fullRefreshRunning"
                                    class="flex items-center gap-2 bg-gray-100 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-200 disabled:opacity-50 transition-colors">
                                <div v-if="embRunning === 'papers'" class="spinner" style="width:14px;height:14px;border-width:2px"></div>
                                <span v-else>🧭</span>
                                {{ $t('settings.indexItems') }}
                            </button>
                            <button @click="reindexEmbeddings('chunks')"
                                    :disabled="embRunning || fullRefreshRunning"
                                    class="flex items-center gap-2 bg-gray-100 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-200 disabled:opacity-50 transition-colors">
                                <div v-if="embRunning === 'chunks'" class="spinner" style="width:14px;height:14px;border-width:2px"></div>
                                <span v-else>🧩</span>
                                {{ $t('settings.indexPassages') }}
                            </button>
                            <span v-if="embResult" class="text-sm text-accent">{{ embResult.msg }}</span>
                        </div>
                        <p v-if="embRunning" class="text-xs text-gray-400 mt-2">
                            {{ $t('settings.indexRunningHint') }}
                        </p>
                    </div>
                </section>

                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">{{ $t('settings.configuration') }}</h3>
                    <p class="text-sm text-gray-500 mb-5">{{ $t('settings.envHint') }}</p>

                    <!-- PDF-Schutz Toggle -->
                    <div class="flex items-center justify-between mb-5 pb-5 border-b border-gray-100">
                        <div>
                            <p class="text-sm font-medium text-gray-700">{{ $t('settings.unlockPdfs') }}</p>
                            <p class="text-xs text-gray-400 mt-0.5">{{ $t('settings.unlockPdfsHint') }}</p>
                        </div>
                        <button @click="toggleUnlockPdfs"
                                class="relative w-14 h-7 rounded-full transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2"
                                :class="unlockPdfs ? 'bg-accent' : 'bg-gray-300'">
                            <span class="lb-knob absolute left-0.5 top-0.5 w-6 h-6 bg-white rounded-full shadow transform transition-transform duration-200 flex items-center justify-center"
                                  :class="unlockPdfs ? 'translate-x-7' : 'translate-x-0'">
                                <svg v-if="unlockPdfs" xmlns="http://www.w3.org/2000/svg" class="text-accent" style="width:14px;height:14px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/></svg>
                                <svg v-else xmlns="http://www.w3.org/2000/svg" class="text-gray-400" style="width:14px;height:14px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                            </span>
                        </button>
                    </div>

                    <div class="space-y-4">
                        <div v-for="field in fields" :key="field.key">
                            <label class="block text-sm font-medium text-gray-700 mb-1">{{ field.label }}</label>
                            <input v-model="settings[field.key]"
                                   :type="field.secret ? 'password' : 'text'"
                                   :placeholder="field.placeholder"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                                          focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                            <p class="text-xs text-gray-400 mt-1">{{ field.help }}</p>
                        </div>
                    </div>

                    <div class="flex items-center gap-3 mt-5">
                        <button @click="saveSettings"
                                class="bg-accent text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                            {{ $t('common.save') }}
                        </button>
                        <span v-if="settingsSaved" class="text-accent text-sm save-success">{{ $t('settings.saved') }}</span>
                    </div>
                </section>
            </div>

            <!-- ========== LLM Tab ==========
                 Drei Rollen (Reasoning / Simple / Embedding), je an EINE
                 Verbindung + Modell gebunden; darunter die Verbindungen. Ein
                 Speichern-Button fuer den ganzen Tab: das Dokument ist atomar
                 (PUT /api/llm/config), es gibt keinen halbgespeicherten Zustand.
                 Keys kommen nie vom Server (nur has_key/key_hint); api_key null
                 heisst "unveraendert", "" heisst "entfernen". -->
            <div v-if="activeTab === 'llm'" data-testid="llm-panel">
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">{{ $t('settings.llm.rolesTitle') }}</h3>
                    <p class="text-sm text-gray-500 mb-5">{{ $t('settings.llm.rolesHint') }}</p>
                    <p v-if="!llmDoc.connections.length" data-testid="llm-empty"
                       class="text-sm text-gray-400 mb-4">{{ $t('settings.llm.emptyState') }}</p>

                    <div class="space-y-5">
                        <div v-for="tier in llmTiers" :key="tier" :data-testid="'llm-role-' + tier">
                            <div class="flex items-center justify-between mb-1">
                                <label class="text-sm font-medium text-gray-700">{{ $t('settings.llm.role.' + tier) }}</label>
                                <span class="text-xs" :class="llmStatus[tier] ? 'text-accent' : 'text-gray-400'">
                                    {{ llmStatus[tier] ? $t('settings.llm.ready') : $t('settings.llm.notReady') }}
                                </span>
                            </div>
                            <p class="text-xs text-gray-400 mb-2">{{ $t('settings.llm.role.' + tier + 'Help') }}</p>
                            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                <select v-model="llmDoc.roles[tier].connection_id"
                                        :disabled="!llmDoc.connections.length"
                                        @change="onRoleConnectionChange(tier)"
                                        class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white
                                               focus:ring-2 focus:ring-accent focus:border-accent outline-none disabled:opacity-50">
                                    <option value="">{{ $t('settings.llm.noConnection') }}</option>
                                    <option v-for="c in llmDoc.connections" :key="c.id" :value="c.id">{{ connectionLabel(c) }}</option>
                                </select>
                                <!-- Kurze Listen: natives Dropdown. Lange Listen (OpenRouter:
                                     Hunderte): Combobox — tippen filtert und klappt die Liste
                                     sofort auf, ein Klick waehlt. Ein natives <select> laesst
                                     sich nicht per Skript oeffnen, daher das eigene Panel. -->
                                <select v-if="modelOptions(tier).length && modelOptions(tier).length <= 12"
                                        v-model="llmDoc.roles[tier].model"
                                        :data-testid="'llm-model-select-' + tier"
                                        class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white
                                               focus:ring-2 focus:ring-accent focus:border-accent outline-none">
                                    <option value="">{{ $t('settings.llm.noModel') }}</option>
                                    <option v-for="m in modelOptions(tier)" :key="m" :value="m">{{ m }}</option>
                                </select>
                                <div v-else-if="modelOptions(tier).length" class="relative">
                                    <input :value="llmModelOpen[tier] ? llmModelFilter[tier] : llmDoc.roles[tier].model"
                                           @focus="openModelPicker(tier)"
                                           @input="llmModelFilter[tier] = $event.target.value; llmModelOpen[tier] = true"
                                           @keydown.esc.prevent="closeModelPicker(tier)"
                                           @keydown.enter.prevent="pickFirstModel(tier)"
                                           @blur="closeModelPicker(tier)"
                                           type="text" autocomplete="off"
                                           :placeholder="$t('settings.llm.modelFilterPlaceholder', { count: modelOptions(tier).length })"
                                           :data-testid="'llm-model-filter-' + tier"
                                           class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono
                                                  focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                                    <ul v-if="llmModelOpen[tier]"
                                        :data-testid="'llm-model-panel-' + tier"
                                        class="absolute z-20 left-0 right-0 mt-1 max-h-64 overflow-auto bg-white border border-gray-200
                                               rounded-lg shadow-lg text-sm">
                                        <li @mousedown.prevent="pickModel(tier, '')"
                                            class="px-3 py-1.5 text-gray-400 hover:bg-gray-50 cursor-pointer">
                                            {{ $t('settings.llm.noModel') }}
                                        </li>
                                        <li v-for="m in filteredModelOptions(tier)" :key="m"
                                            @mousedown.prevent="pickModel(tier, m)"
                                            data-testid="llm-model-option"
                                            class="px-3 py-1.5 font-mono cursor-pointer hover:bg-gray-50"
                                            :class="m === llmDoc.roles[tier].model ? 'bg-accent/10 text-accent' : 'text-gray-800'">
                                            {{ m }}
                                        </li>
                                        <li v-if="modelFilterHasNoMatch(tier)" class="px-3 py-1.5 text-gray-400 italic">
                                            {{ $t('settings.llm.modelFilterNoMatch') }}
                                        </li>
                                    </ul>
                                </div>
                                <input v-else
                                       v-model="llmDoc.roles[tier].model"
                                       :disabled="!llmDoc.roles[tier].connection_id"
                                       :placeholder="$t('settings.llm.modelPlaceholder')"
                                       class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                                              focus:ring-2 focus:ring-accent focus:border-accent outline-none disabled:opacity-50" />
                            </div>
                            <!-- Leeres Dropdown ohne Begruendung ist eine Sackgasse: der Grund
                                 steht sonst nur im Log, das im gebauten .exe niemand sieht. -->
                            <p v-if="llmDoc.roles[tier].connection_id && modelsErrorFor(llmDoc.roles[tier].connection_id)"
                               class="text-xs text-red-500 mt-1 leading-relaxed">
                                {{ $t('settings.modelListUnavailableShort') }}<br>{{ modelsErrorFor(llmDoc.roles[tier].connection_id) }}
                            </p>
                            <div v-if="tier === 'reasoning'" class="mt-2">
                                <button @click="suggestModels" :disabled="suggestingModels || !llmDoc.roles.reasoning.connection_id"
                                        data-testid="llm-suggest"
                                        class="text-xs px-3 py-1.5 rounded-lg border border-accent text-accent
                                               hover:bg-accent hover:text-white transition-colors disabled:opacity-50">
                                    <span v-if="suggestingModels">{{ $t('settings.determining') }}</span>
                                    <span v-else>✨ {{ $t('settings.suggestModels') }}</span>
                                </button>
                                <p v-if="modelSuggestion" class="text-xs text-gray-500 mt-2 leading-relaxed">
                                    <span class="font-medium">{{ $t('settings.suggestion') }} {{ modelSuggestion.source === 'llm' ? $t('settings.suggestionFromAi') : $t('settings.suggestionFromPattern') }}:</span><br>
                                    {{ $t('settings.llm.role.reasoning') }}: <span class="font-mono">{{ modelSuggestion.reasoning }}</span>
                                    <template v-if="modelSuggestion.reasoning_reason"> — {{ modelSuggestion.reasoning_reason }}</template><br>
                                    {{ $t('settings.llm.role.fast') }}: <span class="font-mono">{{ modelSuggestion.fast }}</span>
                                    <template v-if="modelSuggestion.fast_reason"> — {{ modelSuggestion.fast_reason }}</template>
                                </p>
                                <p v-if="modelSuggestError" class="text-xs text-red-500 mt-2">{{ modelSuggestError }}</p>
                            </div>
                        </div>
                    </div>
                </section>

                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">{{ $t('settings.llm.connectionsTitle') }}</h3>
                    <p class="text-sm text-gray-500 mb-5">{{ $t('settings.llm.connectionsHint') }}</p>

                    <div v-for="c in llmDoc.connections" :key="c.id"
                         :data-testid="'llm-connection-' + c.id"
                         class="border border-gray-200 rounded-lg p-4 mb-3">
                        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            <div>
                                <label class="block text-xs font-medium text-gray-700 mb-1">{{ $t('settings.llm.label') }}</label>
                                <input v-model="c.label" data-testid="llm-conn-label"
                                       :placeholder="providerLabel(c.provider)"
                                       class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                                              focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                            </div>
                            <div>
                                <label class="block text-xs font-medium text-gray-700 mb-1">{{ $t('settings.field.provider') }}</label>
                                <select v-model="c.provider" data-testid="llm-conn-provider"
                                        @change="invalidateModels(c)"
                                        class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white
                                               focus:ring-2 focus:ring-accent focus:border-accent outline-none">
                                    <option v-for="p in availableProviders" :key="p.id" :value="p.id">{{ p.label }}</option>
                                </select>
                            </div>
                            <div class="sm:col-span-2">
                                <label class="block text-xs font-medium text-gray-700 mb-1">{{ $t('settings.field.baseUrl') }}</label>
                                <input v-model="c.base_url" data-testid="llm-conn-base-url"
                                       @change="invalidateModels(c)"
                                       :placeholder="providerBaseUrl(c.provider) || 'http://localhost:11434/v1'"
                                       class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono
                                              focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                                <p class="text-xs text-gray-400 mt-1">{{ $t('settings.llm.baseUrlHelp') }}</p>
                            </div>
                            <div class="sm:col-span-2">
                                <label class="block text-xs font-medium text-gray-700 mb-1">{{ $t('settings.field.apiKey') }}</label>
                                <div class="flex items-center gap-2">
                                    <input type="password" data-testid="llm-conn-api-key"
                                           :value="c.api_key === null ? '' : c.api_key"
                                           @input="c.api_key = $event.target.value; invalidateModels(c)"
                                           :placeholder="c.api_key === null && c.has_key
                                               ? $t('settings.llm.apiKeyUnchanged', { hint: c.key_hint })
                                               : $t('settings.llm.apiKeyNone')"
                                           class="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm
                                                  focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                                    <button v-if="c.has_key && c.api_key === null" @click="c.api_key = ''"
                                            class="text-xs text-gray-500 hover:text-gray-700 underline whitespace-nowrap">
                                        {{ $t('settings.llm.apiKeyRemove') }}
                                    </button>
                                </div>
                                <p class="text-xs text-gray-400 mt-1">{{ $t('settings.llm.apiKeyHelp') }}</p>
                            </div>
                        </div>
                        <div class="flex items-center gap-3 mt-3 flex-wrap">
                            <button @click="loadModelsFor(c)" :disabled="llmModelsLoading[c.id]"
                                    data-testid="llm-conn-load-models"
                                    class="text-xs px-3 py-1.5 rounded-lg bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-50 transition-colors">
                                <span v-if="llmModelsLoading[c.id]">{{ $t('settings.determining') }}</span>
                                <span v-else>{{ $t('settings.llm.loadModels') }}</span>
                            </button>
                            <span v-if="llmModels[c.id] && llmModels[c.id].models.length" class="text-xs text-accent">
                                ✓ {{ $t('settings.llm.modelsLoaded', { count: llmModels[c.id].models.length }) }}
                            </span>
                            <span v-else-if="llmModels[c.id] && llmModels[c.id].error" class="text-xs text-red-500">
                                {{ llmModels[c.id].error }}
                            </span>
                            <span class="flex-1"></span>
                            <span v-if="boundRolesOf(c.id).length" class="text-xs text-gray-400">
                                {{ $t('settings.llm.removeBoundHint', { roles: boundRolesOf(c.id).map(r => $t('settings.llm.role.' + r)).join(', ') }) }}
                            </span>
                            <button @click="removeConnection(c)" :disabled="boundRolesOf(c.id).length > 0"
                                    data-testid="llm-conn-remove"
                                    class="text-xs text-red-600 hover:underline disabled:opacity-40 disabled:cursor-not-allowed">
                                {{ $t('settings.llm.remove') }}
                            </button>
                        </div>
                    </div>

                    <button @click="addConnection" data-testid="llm-add-connection"
                            class="text-sm px-4 py-2 rounded-lg border border-dashed border-gray-300 text-gray-600 hover:border-accent hover:text-accent transition-colors w-full">
                        + {{ $t('settings.llm.addConnection') }}
                    </button>
                </section>

                <div class="flex items-center gap-3">
                    <button @click="saveLlmConfig" :disabled="llmSaving" data-testid="llm-save"
                            class="bg-accent text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors disabled:opacity-50">
                        {{ $t('common.save') }}
                    </button>
                    <span v-if="llmSaved" class="text-accent text-sm save-success">{{ $t('settings.saved') }}</span>
                    <span v-if="llmError" data-testid="llm-error" class="text-sm text-red-500">{{ llmError }}</span>
                </div>
            </div>

            <!-- ========== Erscheinungsbild Tab ========== -->
            <div v-if="activeTab === 'appearance'">
                <!-- Sprache (ADR-0018). Wirkt sofort: beide Kataloge liegen im
                     Speicher, ein Neustart waere hier reine Schikane. -->
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <div class="flex items-center justify-between gap-4">
                        <div>
                            <h3 class="text-base font-semibold text-gray-900 mb-1">{{ $t('settings.language') }}</h3>
                            <p class="text-sm text-gray-500">{{ $t('settings.languageHint') }}</p>
                        </div>
                        <select :value="$lang()" @change="changeLanguage($event.target.value)"
                                class="border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-accent focus:border-accent outline-none">
                            <option value="en">{{ $t('settings.languageEn') }}</option>
                            <option value="de">{{ $t('settings.languageDe') }}</option>
                        </select>
                    </div>
                </section>

                <!-- Dark Mode -->
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-base font-semibold text-gray-900 mb-1">Dark Mode</h3>
                            <p class="text-sm text-gray-500">{{ $t('settings.darkModeHint') }}</p>
                        </div>
                        <button @click="toggleDarkMode"
                                class="relative w-14 h-7 rounded-full transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2"
                                :class="darkMode ? 'bg-accent' : 'bg-gray-300'">
                            <span class="lb-knob absolute left-0.5 top-0.5 w-6 h-6 bg-white rounded-full shadow transform transition-transform duration-200 flex items-center justify-center"
                                  :class="darkMode ? 'translate-x-7' : 'translate-x-0'">
                                <svg v-if="darkMode" xmlns="http://www.w3.org/2000/svg" class="text-accent" style="width:14px;height:14px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
                                <svg v-else xmlns="http://www.w3.org/2000/svg" class="text-accent" style="width:14px;height:14px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
                            </span>
                        </button>
                    </div>
                </section>

                <!-- Thesis-Modus Toggle -->
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-base font-semibold text-gray-900 mb-1">{{ $t('nav.thesis') }}</h3>
                            <p class="text-sm text-gray-500">{{ $t('settings.thesisModeHint') }}</p>
                        </div>
                        <button @click="toggleThesisMode"
                                class="relative w-14 h-7 rounded-full transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2"
                                :class="thesisMode ? 'bg-accent' : 'bg-gray-300'">
                            <span class="lb-knob absolute left-0.5 top-0.5 w-6 h-6 bg-white rounded-full shadow transform transition-transform duration-200 flex items-center justify-center"
                                  :class="thesisMode ? 'translate-x-7' : 'translate-x-0'">
                                <svg v-if="thesisMode" xmlns="http://www.w3.org/2000/svg" class="text-accent" style="width:14px;height:14px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>
                                <svg v-else xmlns="http://www.w3.org/2000/svg" class="text-gray-400" style="width:14px;height:14px" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>
                            </span>
                        </button>
                    </div>
                </section>

                <!-- Icon Import -->
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">{{ $t('settings.icon') }}</h3>
                    <p class="text-sm text-gray-500 mb-4">{{ $t('settings.customIconHint') }}</p>

                    <div class="flex items-start gap-6">
                        <div class="flex-shrink-0">
                            <div class="w-20 h-20 rounded-xl border-2 border-dashed border-gray-300 flex items-center justify-center bg-gray-50 overflow-hidden">
                                <img v-if="customIconPath" :src="customIconPath" class="w-full h-full object-contain" />
                                <span v-else v-html="icons.image" class="text-gray-300"></span>
                            </div>
                        </div>
                        <div class="flex-1">
                            <div class="flex items-center gap-2 mb-3">
                                <label class="inline-flex items-center gap-2 bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink cursor-pointer transition-colors">
                                    <span v-html="icons.upload"></span>
                                    {{ $t('settings.uploadIcon') }}
                                    <input type="file" accept=".png,.ico,.svg,.jpg,.jpeg,.webp" @change="uploadIcon" class="hidden" />
                                </label>
                                <button v-if="customIconPath" @click="deleteIcon"
                                        class="inline-flex items-center gap-2 bg-white border border-accent-soft text-accent px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-soft transition-colors">
                                    <span v-html="icons.trash"></span>
                                    {{ $t('common.remove') }}
                                </button>
                            </div>
                            <p class="text-xs text-gray-400">{{ $t('settings.iconFormats') }}</p>
                        </div>
                    </div>
                </section>

                <div class="mt-4 text-xs text-gray-400 text-center">
                    {{ $t('settings.appearanceAutosaved') }}
                </div>
            </div>

            <!-- ========== Spalten Tab ========== -->
            <div v-if="activeTab === 'columns'">
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">{{ $t('settings.customColumns') }}</h3>
                    <p class="text-sm text-gray-500 mb-5">
                        {{ $t('settings.customColumnsHint') }}
                    </p>

                    <!-- Existing fields -->
                    <div v-if="customFields.length" class="space-y-2 mb-5">
                        <div v-for="cf in customFields" :key="cf.id"
                             class="flex items-center gap-3 bg-gray-50 rounded-lg p-3">
                            <span v-html="icons.columns" class="text-gray-400 flex-shrink-0"></span>
                            <div class="flex-1 min-w-0">
                                <p class="text-sm font-medium text-gray-900">{{ cf.name }}</p>
                                <p class="text-xs text-gray-400">
                                    {{ $t('settings.fieldType') }}: {{ fieldTypeLabel(cf.field_type) }}
                                    <span v-if="cf.options"> &middot; {{ $t('settings.optionsLabel') }}: {{ cf.options }}</span>
                                </p>
                            </div>
                            <button @click="deleteField(cf)"
                                    class="p-1.5 text-gray-400 hover:text-accent rounded transition-colors">
                                <span v-html="icons.trash"></span>
                            </button>
                        </div>
                    </div>
                    <div v-else class="text-sm text-gray-400 mb-5">
                        {{ $t('settings.noCustomColumns') }}
                    </div>

                    <!-- Add new field -->
                    <div class="border-t border-gray-200 pt-5">
                        <h4 class="text-sm font-medium text-gray-900 mb-3">{{ $t('settings.addField') }}</h4>
                        <div class="grid grid-cols-2 gap-3 mb-3">
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">{{ $t('common.name') }}</label>
                                <input v-model="newField.name" :placeholder="$t('settings.fieldNamePlaceholder')"
                                       class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">{{ $t('settings.fieldType') }}</label>
                                <select v-model="newField.field_type"
                                        class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-accent focus:border-accent outline-none">
                                    <option value="text">{{ $t('settings.fieldTypeText') }}</option>
                                    <option value="number">{{ $t('settings.fieldTypeNumber') }}</option>
                                    <option value="progress">{{ $t('settings.fieldTypeProgressRange') }}</option>
                                    <option value="select">{{ $t('settings.fieldTypeSelect') }}</option>
                                </select>
                            </div>
                        </div>
                        <div v-if="newField.field_type === 'select'" class="mb-3">
                            <label class="block text-xs text-gray-500 mb-1">{{ $t('settings.optionsCommaSeparated') }}</label>
                            <input v-model="newField.options" :placeholder="$t('settings.optionsPlaceholder')"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <button @click="createField" :disabled="!newField.name"
                                class="inline-flex items-center gap-1.5 bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            <span v-html="icons.plus"></span>
                            {{ $t('settings.createField') }}
                        </button>
                    </div>
                </section>
            </div>

            <!-- ========== Export/Import Tab ========== -->
            <div v-if="activeTab === 'export'">
                <!-- Export Section -->
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">RIS Export</h3>
                    <p class="text-sm text-gray-500 mb-4">{{ $t('settings.risExportHint') }}</p>

                    <!-- Export Filters -->
                    <div class="grid grid-cols-2 gap-3 mb-4">
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">{{ $t('filters.category') }}</label>
                            <select v-model="exportCategoryId" @change="loadExportPreview"
                                    class="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white focus:ring-2 focus:ring-accent focus:border-accent outline-none">
                                <option value="">{{ $t('filters.allCategories') }}</option>
                                <option v-for="c in allCategories" :key="c.id" :value="c.id">{{ c.name }}</option>
                            </select>
                        </div>
                    </div>

                    <div class="flex items-center gap-3 mb-4">
                        <a :href="exportUrl" download="literatur_export.ris"
                           class="inline-flex items-center gap-2 bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                            <span v-html="icons.download"></span>
                            {{ $t('ris.exportFile') }}
                        </a>
                        <a :href="bibtexExportUrl" download="literatur_export.bib"
                           class="inline-flex items-center gap-2 bg-white border border-accent text-accent px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-soft transition-colors">
                            <span v-html="icons.download"></span>
                            BibTeX exportieren (.bib)
                        </a>
                        <span class="text-sm font-semibold text-gray-700">{{ $tn('common.itemCount', exportPreview.count ?? stats.paper_count ?? 0) }}</span>
                        <span v-if="exportCategoryId" class="text-xs text-gray-400">{{ $t('settings.filtered') }}</span>
                    </div>

                    <!-- Export Preview -->
                    <div v-if="exportPreview.papers && exportPreview.papers.length > 0" class="border border-gray-200 rounded-lg overflow-hidden">
                        <div class="bg-gray-50 px-4 py-2 border-b border-gray-200 flex items-center justify-between">
                            <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">{{ $t('settings.previewCount', { count: exportPreview.count }) }}</span>
                            <button @click="exportPreviewExpanded = !exportPreviewExpanded" class="text-xs text-accent hover:text-accent-ink">
                                {{ exportPreviewExpanded ? 'Einklappen' : 'Alle anzeigen' }}
                            </button>
                        </div>
                        <div class="max-h-64 overflow-y-auto" :class="{ 'max-h-none': exportPreviewExpanded }">
                            <table class="w-full text-sm">
                                <tbody>
                                    <tr v-for="paper in exportPreview.papers" :key="paper.id" class="border-b border-gray-100 hover:bg-gray-50">
                                        <td class="px-4 py-1.5 text-gray-800 truncate max-w-md">{{ paper.title || $t('papers.untitled') }}</td>
                                        <td class="px-4 py-1.5 text-gray-500 truncate max-w-[180px]">{{ paper.authors || '' }}</td>
                                        <td class="px-4 py-1.5 text-gray-400 w-16 text-center">{{ paper.year || '-' }}</td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </section>

                <!-- Coming from another manager: the wizard does the whole
                     move (preview, PDFs, collections, undo); the single-file
                     RIS upload below stays for the one-off case. -->
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">{{ $t('settings.migrateLink') }}</h3>
                    <p class="text-sm text-gray-500 mb-4">{{ $t('settings.migrateLinkHint') }}</p>
                    <router-link to="/migrate" data-testid="settings-migrate-link"
                                 class="inline-flex items-center gap-2 bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                        {{ $t('settings.migrateOpen') }}
                    </router-link>
                </section>

                <!-- Import Section -->
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">RIS Import</h3>
                    <p class="text-sm text-gray-500 mb-4">{{ $t('ris.importHint') }}</p>

                    <div class="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center"
                         :class="{ 'border-accent bg-accent-soft': dragOver }"
                         @dragover.prevent="dragOver = true"
                         @dragleave="dragOver = false"
                         @drop.prevent="handleDrop">
                        <div v-if="!importing">
                            <span v-html="icons.upload" class="inline-block text-gray-400 mb-3" style="width:32px;height:32px;"></span>
                            <p class="text-sm text-gray-600 mb-2">{{ $t('ris.dropHint') }}</p>
                            <label class="inline-flex items-center gap-2 bg-white border border-gray-300 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-50 cursor-pointer transition-colors">
                                <span v-html="icons.upload"></span>
                                {{ $t('ris.chooseFile') }}
                                <input type="file" accept=".ris" @change="handleFileSelect" class="hidden" />
                            </label>
                        </div>
                        <div v-else class="flex items-center justify-center gap-3">
                            <div class="spinner"></div>
                            <span class="text-sm text-gray-600">{{ $t('ris.importing') }}</span>
                        </div>
                    </div>

                    <div v-if="importResult" class="mt-5">
                        <div class="p-4 rounded-lg" :class="importResult.errors > 0 ? 'bg-accent-soft border border-accent-soft' : 'bg-accent-soft border border-accent-soft'">
                            <p class="font-medium text-sm mb-2" :class="importResult.errors > 0 ? 'text-accent-ink' : 'text-accent-ink'">
                                {{ $t('ris.importDone') }}
                            </p>
                            <div class="text-sm space-y-0.5" :class="importResult.errors > 0 ? 'text-accent-ink' : 'text-accent-ink'">
                                <p>{{ $t('ris.resultTotal', { count: importResult.total }) }}</p>
                                <p>{{ $t('ris.resultImported', { count: importResult.imported }) }}</p>
                                <p v-if="importResult.skipped">{{ $t('ris.resultSkipped', { count: importResult.skipped }) }}</p>
                                <p v-if="importResult.errors">{{ $t('ris.resultErrors', { count: importResult.errors }) }}</p>
                            </div>
                        </div>
                    </div>
                </section>
            </div>

            <!-- ========== Lizenz Tab (ADR-0015) ========== -->
            <div v-if="activeTab === 'license'" data-testid="license-panel">
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">{{ $t('license.keyHeading') }}</h3>

                    <!-- Aktiviert: einmal geprüft, danach nie wieder online. -->
                    <div v-if="licenseActivated" data-testid="license-activated"
                         class="flex items-center gap-2 mt-4 text-sm text-accent-ink bg-accent-soft border border-accent-soft rounded-lg px-4 py-3">
                        <span>✓</span>
                        <span class="font-medium">{{ $t('license.activated') }}<span v-if="licenseKeyMasked"> — {{ $t('license.keyMasked', { key: licenseKeyMasked }) }}</span>.</span>
                    </div>
                    <p v-if="licenseActivated" class="text-sm text-gray-500 mt-3">
                        {{ $t('license.checkedOnce') }}
                    </p>

                    <!-- Noch nicht aktiviert: Eingabe + Kauflink. -->
                    <template v-else>
                        <!-- Laufende Testphase (#144). Fehlt sie — Quellinstallation —
                             steht hier nichts: dort gibt es nichts abzuzaehlen. -->
                        <p v-if="licenseTrial && !licenseTrial.expired"
                           data-testid="license-trial"
                           class="text-sm text-accent-ink bg-accent-soft border border-accent-soft rounded-lg px-4 py-3 mb-4">
                            {{ $tn('license.trialRunning', licenseTrial.days_remaining) }}
                        </p>
                        <!-- Altbestand aus dem früheren Shop: der Weg zum kostenlosen
                             Code steht hier, nicht erst hinter einer Sperre. -->
                        <p v-if="licenseLegacyNote"
                           data-testid="license-legacy-note"
                           class="text-sm bg-amber-50 border border-amber-200 text-amber-900 rounded-lg px-4 py-3 mb-4">
                            {{ licenseLegacyNote }}
                        </p>
                        <p class="text-sm text-gray-500 mb-5">
                            {{ $t('license.enterKeyHint') }}
                        </p>
                        <div class="flex gap-3 items-start mb-3">
                            <input v-model="licenseKey"
                                   data-testid="license-key-input"
                                   type="text"
                                   spellcheck="false"
                                   placeholder="LB--XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"
                                   @keyup.enter="activateLicense"
                                   class="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                            <button @click="activateLicense"
                                    data-testid="license-activate"
                                    :disabled="licenseLoading || !licenseKey"
                                    class="bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-50 transition-colors">
                                <span v-if="licenseLoading">{{ $t('license.activating') }}</span>
                                <span v-else>{{ $t('license.activate') }}</span>
                            </button>
                        </div>
                        <p v-if="licenseCheckoutUrl" class="text-sm text-gray-500">
                            {{ $t('gate.noLicenseYet') }}
                            <a :href="licenseCheckoutUrl" target="_blank" rel="noopener"
                               data-testid="license-buy-link"
                               class="text-accent underline">{{ $t('license.buy') }}</a>
                        </p>
                    </template>

                    <p v-if="licenseMessage"
                       data-testid="license-message"
                       class="text-sm mt-3"
                       :class="licenseError ? 'text-red-600' : 'text-accent'">
                        {{ licenseMessage }}
                    </p>
                </section>
            </div>
        </div>
    `,
    data() {
        return {
            activeTab: 'general',
            settings: {},
            settingsSaved: false,
            pageCountUpdating: false,
            pageCountResult: null,
            rebuildLinksRunning: false,
            rebuildLinksResult: null,
            embStatus: null,
            embRunning: '',
            embResult: null,
            fullRefreshRunning: false,
            fullRefreshDone: false,
            fullRefreshMessage: '',
            fullRefreshPercent: 0,
            fullRefreshStats: null,
            unlockPdfs: true,
            icons,
            darkMode: localStorage.getItem('darkMode') === 'true',
            thesisMode: localStorage.getItem('thesisMode') === 'true',
            customIconPath: null,
            // Columns
            customFields: [],
            newField: { name: '', field_type: 'text', options: '' },
            // Export/Import
            stats: {},
            allCategories: [],
            exportCategoryId: '',
            exportPreview: { count: null, papers: [] },
            exportPreviewExpanded: false,
            importing: false,
            importResult: null,
            dragOver: false,
            // Lizenz (ADR-0015)
            licenseKey: '',
            licenseKeyMasked: '',
            licenseLoading: false,
            licenseMessage: '',
            licenseError: false,
            licenseActivated: false,
            licenseCheckoutUrl: '',
            licenseTrial: null,
            licenseLegacyNote: '',
            // LLM-Tab (Verbindungen + Rollen, llm.json)
            llmTiers: ['reasoning', 'fast', 'embedding'],
            llmDoc: {
                connections: [],
                roles: {
                    reasoning: { connection_id: '', model: '' },
                    fast: { connection_id: '', model: '' },
                    embedding: { connection_id: '', model: '' },
                },
            },
            llmStatus: {},
            llmModels: {},          // connection id -> { models, error }
            llmModelFilter: { reasoning: '', fast: '', embedding: '' },
            llmModelOpen: { reasoning: false, fast: false, embedding: false },
            llmModelsLoading: {},
            llmSaving: false,
            llmSaved: false,
            llmError: '',
            availableProviders: [],
            suggestingModels: false,
            modelSuggestion: null,
            modelSuggestError: '',
        };
    },
    computed: {
        exportUrl() {
            let url = '/api/export/ris?';
            if (this.exportCategoryId) url += `category_id=${this.exportCategoryId}&`;
            return url;
        },
        bibtexExportUrl() {
            let url = '/api/export/bibtex?';
            if (this.exportCategoryId) url += `category_id=${this.exportCategoryId}&`;
            return url;
        },
        // Die .env-Felder tragen uebersetzten Text, also sind sie computed und
        // nicht data: `data()` laeuft einmal, ein Sprachwechsel danach wuerde
        // die Beschriftungen nie wieder anfassen.
        fields() {
            return [
                { key: 'LITERATUR_BASE_DIR', label: t('settings.field.baseDir'), secret: false,
                  placeholder: 'C:\\Users\\...\\Library', help: t('settings.field.baseDirHelp') },
                { key: 'LINK_MODE', label: t('settings.field.linkMode'), secret: false,
                  placeholder: 'symlink', help: t('settings.field.linkModeHelp') },
                { key: 'CROSSREF_MAILTO', label: t('settings.field.crossrefMail'), secret: false,
                  placeholder: 'you@example.com', help: t('settings.field.crossrefMailHelp') },
                { key: 'OPENALEX_API_KEY', label: t('settings.field.openalexKey'), secret: true,
                  placeholder: t('settings.field.openalexKeyPlaceholder'),
                  help: t('settings.field.openalexKeyHelp') },
                { key: 'WATCH_INTERVAL', label: t('settings.field.watchInterval'), secret: false,
                  placeholder: '5', help: t('settings.field.watchIntervalHelp') },
                { key: 'MAX_OCR_PAGES', label: t('settings.field.maxOcrPages'), secret: false,
                  placeholder: '50000', help: t('settings.field.maxOcrPagesHelp') },
            ];
        },
    },
    async created() {
        await Promise.all([
            this.loadSettings(),
            this.loadIcon(),
            this.loadCustomFields(),
            this.loadExportMeta(),
            this.loadLicenseStatus(),
            this.loadLlmConfig(),
            this.loadProviders(),
            this.loadEmbeddingStatus(),
        ]);
        await this.loadExportPreview();
    },
    methods: {
        async loadSettings() {
            try {
                this.settings = await api('/api/settings');
                // UNLOCK_PDFS: default true wenn nicht gesetzt
                this.unlockPdfs = this.settings.UNLOCK_PDFS !== 'false';
            } catch (e) {
                console.error('Settings load error:', e);
            }
        },
        async loadProviders() {
            try {
                const data = await api('/api/llm/providers');
                this.availableProviders = data.providers || [];
            } catch (e) {
                console.error('Providers load error:', e);
            }
        },
        // --- LLM-Tab: Verbindungen + Rollen (llm.json) ---------------------
        // Der Server liefert das Dokument ohne Keys; jede Karte startet mit
        // api_key = null ("unveraendert"). Erst eine Eingabe macht daraus einen
        // String, der beim Speichern mitgeht.
        async loadLlmConfig() {
            try {
                this.applyLlmConfig(await api('/api/llm/config'));
            } catch (e) {
                console.error('LLM config load error:', e);
            }
        },
        applyLlmConfig(data) {
            const roles = {};
            for (const tier of this.llmTiers) {
                const r = (data.roles || {})[tier] || {};
                roles[tier] = { connection_id: r.connection_id || '', model: r.model || '' };
            }
            this.llmDoc = {
                connections: (data.connections || []).map((c) => ({ ...c, api_key: null, _new: false })),
                roles,
            };
            this.llmStatus = data.status || {};
        },
        providerLabel(id) {
            const p = this.availableProviders.find((x) => x.id === id);
            return p ? p.label : (id || '');
        },
        providerBaseUrl(id) {
            const p = this.availableProviders.find((x) => x.id === id);
            return p ? (p.base_url || '') : '';
        },
        connectionLabel(c) {
            return (c.label || '').trim() || this.providerLabel(c.provider) || c.id;
        },
        boundRolesOf(connectionId) {
            return this.llmTiers.filter((tier) => this.llmDoc.roles[tier].connection_id === connectionId);
        },
        // Modell-Liste der Verbindung, an die die Rolle gebunden ist. Fuer die
        // Embedding-Rolle stehen embedding-artige Modelle vorn — die Listen
        // bestehen ueberwiegend aus Chat-Modellen, die hier selten gemeint sind.
        modelOptions(tier) {
            const cid = this.llmDoc.roles[tier].connection_id;
            const entry = cid ? this.llmModels[cid] : null;
            const models = entry ? [...entry.models] : [];
            if (tier === 'embedding') {
                models.sort((a, b) => {
                    const ea = a.toLowerCase().includes('embed') ? 0 : 1;
                    const eb = b.toLowerCase().includes('embed') ? 0 : 1;
                    return ea - eb || a.localeCompare(b);
                });
            }
            const current = this.llmDoc.roles[tier].model;
            if (current && models.length && !models.includes(current)) models.unshift(current);
            return models;
        },
        // Filter ueber die Modell-Liste: jedes Leerzeichen-getrennte Wort muss
        // vorkommen (Gross/Klein egal). Das aktuell gebundene Modell faellt
        // nie heraus, sonst zeigte das Dropdown einen Wert, den es nicht kennt.
        filteredModelOptions(tier) {
            const models = this.modelOptions(tier);
            const words = (this.llmModelFilter[tier] || '').toLowerCase().split(/\s+/).filter(Boolean);
            if (!words.length) return models;
            const current = this.llmDoc.roles[tier].model;
            return models.filter((m) => m === current || words.every((w) => m.toLowerCase().includes(w)));
        },
        // "Kein Treffer" auch dann, wenn nur das gebundene Modell uebrig bleibt.
        modelFilterHasNoMatch(tier) {
            if (!(this.llmModelFilter[tier] || '').trim()) return false;
            const current = this.llmDoc.roles[tier].model;
            return this.filteredModelOptions(tier).every((m) => m === current);
        },
        openModelPicker(tier) {
            this.llmModelFilter[tier] = '';
            this.llmModelOpen[tier] = true;
        },
        closeModelPicker(tier) {
            this.llmModelOpen[tier] = false;
            this.llmModelFilter[tier] = '';
        },
        pickModel(tier, model) {
            this.llmDoc.roles[tier].model = model;
            this.closeModelPicker(tier);
        },
        // Enter nimmt den ersten Treffer, der nicht bloss das gebundene Modell ist.
        pickFirstModel(tier) {
            const current = this.llmDoc.roles[tier].model;
            const words = (this.llmModelFilter[tier] || '').trim();
            const hit = this.filteredModelOptions(tier).find((m) => !words || m !== current)
                || this.filteredModelOptions(tier)[0];
            if (hit) this.pickModel(tier, hit);
        },
        modelsErrorFor(connectionId) {
            const entry = this.llmModels[connectionId];
            return entry && !entry.models.length ? (entry.error || '') : '';
        },
        // Was der Server fuer eine Verbindung braucht, um ihre Modelle zu laden:
        // die gespeicherte per id (der Key bleibt auf dem Server), eine neue
        // oder mit geaendertem Key als Entwurf — nur dann reist der Key mit.
        probeBody(c) {
            if (!c._new && c.api_key === null) {
                return { connection_id: c.id, provider: c.provider, base_url: c.base_url || '' };
            }
            return { provider: c.provider, base_url: c.base_url || '', api_key: c.api_key || '' };
        },
        async loadModelsFor(c) {
            this.llmModelsLoading[c.id] = true;
            try {
                const data = await api('/api/llm/models', { method: 'POST', body: JSON.stringify(this.probeBody(c)) });
                this.llmModels[c.id] = {
                    models: data.models || [],
                    error: data.error ? translateDetail(data.error) : '',
                };
            } catch (e) {
                this.llmModels[c.id] = { models: [], error: e.message };
            } finally {
                this.llmModelsLoading[c.id] = false;
            }
        },
        invalidateModels(c) {
            delete this.llmModels[c.id];
        },
        onRoleConnectionChange(tier) {
            this.llmDoc.roles[tier].model = '';
            this.closeModelPicker(tier);
            const cid = this.llmDoc.roles[tier].connection_id;
            const c = this.llmDoc.connections.find((x) => x.id === cid);
            if (c && !this.llmModels[cid]) this.loadModelsFor(c);
        },
        addConnection() {
            const first = this.availableProviders[0];
            this.llmDoc.connections.push({
                id: Math.random().toString(16).slice(2, 10),
                label: '',
                provider: first ? first.id : 'custom',
                base_url: '',
                api_key: '',
                has_key: false,
                key_hint: '',
                _new: true,
            });
        },
        removeConnection(c) {
            if (this.boundRolesOf(c.id).length) return;
            this.llmDoc.connections = this.llmDoc.connections.filter((x) => x.id !== c.id);
            delete this.llmModels[c.id];
        },
        async saveLlmConfig() {
            this.llmSaving = true;
            this.llmError = '';
            try {
                const payload = {
                    connections: this.llmDoc.connections.map((c) => ({
                        id: c.id, label: c.label, provider: c.provider,
                        base_url: c.base_url || '', api_key: c.api_key,
                    })),
                    roles: this.llmDoc.roles,
                };
                const data = await api('/api/llm/config', { method: 'PUT', body: JSON.stringify(payload) });
                this.applyLlmConfig(data);
                this.llmSaved = true;
                setTimeout(() => this.llmSaved = false, 2500);
                await this.loadEmbeddingStatus();
            } catch (e) {
                this.llmError = e.message;
            } finally {
                this.llmSaving = false;
            }
        },
        // Vorschlag laeuft auf der Verbindung der Reasoning-Rolle und fuellt
        // Reasoning + Simple aus DEREN Liste; Simple wird nur mitgezogen, wenn
        // es nicht an eine andere Verbindung gebunden ist.
        async suggestModels() {
            const cid = this.llmDoc.roles.reasoning.connection_id;
            const c = this.llmDoc.connections.find((x) => x.id === cid);
            if (!c) return;
            this.suggestingModels = true;
            this.modelSuggestError = '';
            try {
                const data = await api('/api/llm/suggest-models', { method: 'POST', body: JSON.stringify(this.probeBody(c)) });
                if (data.suggestion) {
                    this.modelSuggestion = data.suggestion;
                    // Dropdowns nur vorbefuellen – Nutzer speichert selbst.
                    this.llmDoc.roles.reasoning.model = data.suggestion.reasoning;
                    const fast = this.llmDoc.roles.fast;
                    if (!fast.connection_id || fast.connection_id === cid) {
                        fast.connection_id = cid;
                        fast.model = data.suggestion.fast;
                    }
                } else {
                    this.modelSuggestion = null;
                    this.modelSuggestError = data.error ? translateDetail(data.error) : t('settings.noSuggestion');
                }
            } catch (e) {
                this.modelSuggestError = t('error.generic', { message: e.message });
            } finally {
                this.suggestingModels = false;
            }
        },
        async saveSettings() {
            try {
                await api('/api/settings', {
                    method: 'PUT',
                    body: JSON.stringify(this.settings),
                });
                this.settingsSaved = true;
                setTimeout(() => this.settingsSaved = false, 2500);
            } catch (e) {
                alert(t('error.saveFailed', { message: e.message }));
            }
        },
        async updatePageCounts() {
            this.pageCountUpdating = true;
            this.pageCountResult = null;
            try {
                const res = await api('/api/maintenance/update-page-counts', { method: 'POST' });
                this.pageCountResult = { ok: true, msg: `✓ ${res.updated} Paper aktualisiert, ${res.failed} fehlgeschlagen` };
            } catch (e) {
                this.pageCountResult = { ok: false, msg: t('error.generic', { message: e.message }) };
            } finally {
                this.pageCountUpdating = false;
            }
        },
        async rebuildLinks() {
            this.rebuildLinksRunning = true;
            this.rebuildLinksResult = null;
            try {
                const res = await api('/api/maintenance/rebuild-links', { method: 'POST' });
                this.rebuildLinksResult = { ok: true, msg: '✓ ' + t('settings.rebuildLinksDone', { rebuilt: res.rebuilt, removed: res.removed }) };
            } catch (e) {
                this.rebuildLinksResult = { ok: false, msg: t('error.generic', { message: e.message }) };
            } finally {
                this.rebuildLinksRunning = false;
            }
        },
        async loadEmbeddingStatus() {
            // Degradiert still: ohne Embedding-Modell liefert der Endpunkt
            // Nullwerte, der Abschnitt zeigt dann den Konfigurations-Hinweis.
            try {
                this.embStatus = await api('/api/maintenance/embeddings/status');
            } catch (e) {
                this.embStatus = null;
            }
        },
        async reindexEmbeddings(scope) {
            // scope: 'papers' (ein Vektor je Paper) | 'chunks' (Passagen-Suche).
            // Beide Laeufe sind idempotent — Bereits-Indexiertes wird uebersprungen.
            this.embRunning = scope;
            this.embResult = null;
            try {
                const url = scope === 'chunks'
                    ? '/api/maintenance/embeddings/reindex-chunks'
                    : '/api/maintenance/embeddings/reindex';
                const res = await api(url, { method: 'POST' });
                if (!res.model) {
                    this.embResult = { ok: false, msg: t('settings.noEmbeddingModel') };
                } else {
                    const what = scope === 'chunks' ? t('settings.reindexPassages') : t('settings.reindexItems');
                    let msg = '✓ ' + t('settings.reindexDone', { indexed: res.indexed, what: what, skipped: res.skipped });
                    if (res.errors > 0) msg += t('settings.reindexErrors', { count: res.errors });
                    this.embResult = { ok: res.errors === 0, msg };
                }
                await this.loadEmbeddingStatus();
            } catch (e) {
                this.embResult = { ok: false, msg: t('error.generic', { message: e.message }) };
            } finally {
                this.embRunning = '';
            }
        },
        async fullRefresh() {
            this.fullRefreshRunning = true;
            this.fullRefreshDone = false;
            this.fullRefreshMessage = 'Starte Refresh...';
            this.fullRefreshPercent = 0;
            this.fullRefreshStats = null;

            try {
                const resp = await fetch('/api/maintenance/full-refresh', { method: 'POST' });
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';
                    for (const line of lines) {
                        const trimmed = line.trim();
                        if (!trimmed.startsWith('data: ')) continue;
                        try {
                            const evt = JSON.parse(trimmed.slice(6));
                            if (evt.type === 'progress') {
                                this.fullRefreshMessage = evt.message;
                                this.fullRefreshPercent = evt.percent || 0;
                                if (evt.stats) this.fullRefreshStats = evt.stats;
                            } else if (evt.type === 'done') {
                                this.fullRefreshPercent = 100;
                                this.fullRefreshMessage = 'Fertig!';
                                if (evt.stats) this.fullRefreshStats = evt.stats;
                                this.fullRefreshDone = true;
                            }
                        } catch (e) { /* ignore */ }
                    }
                }
            } catch (e) {
                this.fullRefreshMessage = t('error.generic', { message: e.message });
            } finally {
                this.fullRefreshRunning = false;
            }
        },
        // Sprache umschalten: erst sichtbar, dann persistiert. Die Reihenfolge
        // ist Absicht - ein langsamer PUT darf die Oberflaeche nicht haengen
        // lassen, und schlaegt er fehl, sagen wir es statt es zu verschlucken.
        async changeLanguage(lang) {
            setUiLang(lang);
            try {
                await api('/api/settings', {
                    method: 'PUT',
                    body: JSON.stringify({ UI_LANGUAGE: lang }),
                });
            } catch (e) {
                alert(t('settings.languageSaveFailed', { message: e.message }));
            }
        },
        toggleDarkMode() {
            this.darkMode = !this.darkMode;
            localStorage.setItem('darkMode', this.darkMode);
            applyDarkMode(this.darkMode);
        },
        toggleThesisMode() {
            this.thesisMode = !this.thesisMode;
            localStorage.setItem('thesisMode', this.thesisMode);
            window.dispatchEvent(new CustomEvent('refresh-sidebar'));
        },
        async toggleUnlockPdfs() {
            this.unlockPdfs = !this.unlockPdfs;
            try {
                await api('/api/settings', {
                    method: 'PUT',
                    body: JSON.stringify({ UNLOCK_PDFS: this.unlockPdfs ? 'true' : 'false' }),
                });
            } catch (e) {
                this.unlockPdfs = !this.unlockPdfs;
                alert(t('error.saveFailed', { message: e.message }));
            }
        },
        async loadIcon() {
            try {
                const data = await api('/api/appearance/icon');
                this.customIconPath = data.path;
            } catch (e) {}
        },
        async uploadIcon(event) {
            const file = event.target.files[0];
            if (!file) return;
            const formData = new FormData();
            formData.append('file', file);
            try {
                const resp = await fetch('/api/appearance/icon', {
                    method: 'POST',
                    body: formData,
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail);
                }
                const data = await resp.json();
                this.customIconPath = data.path;
                setFavicon(data.path);
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            } catch (e) {
                alert(t('error.uploadFailed', { message: e.message }));
            }
        },
        async deleteIcon() {
            try {
                await api('/api/appearance/icon', { method: 'DELETE' });
                this.customIconPath = null;
                setFavicon(null);
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
        },
        // Custom Fields
        async loadCustomFields() {
            try {
                this.customFields = await api('/api/custom-fields');
            } catch (e) {}
        },
        async createField() {
            if (!this.newField.name) return;
            try {
                await api('/api/custom-fields', {
                    method: 'POST',
                    body: JSON.stringify(this.newField),
                });
                this.newField = { name: '', field_type: 'text', options: '' };
                await this.loadCustomFields();
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
        },
        async deleteField(cf) {
            if (!confirm(`Feld "${cf.name}" wirklich loeschen? Alle gespeicherten Werte gehen verloren.`)) return;
            try {
                await api(`/api/custom-fields/${cf.id}`, { method: 'DELETE' });
                await this.loadCustomFields();
            } catch (e) {
                alert(t('error.generic', { message: e.message }));
            }
        },
        fieldTypeLabel(type) {
            const labels = {
                text: t('settings.fieldTypeText'), number: t('settings.fieldTypeNumber'),
                progress: t('settings.fieldTypeProgress'), select: t('settings.fieldTypeSelect'),
            };
            return labels[type] || type;
        },
        // Export/Import
        async loadExportMeta() {
            try {
                const [stats, categories] = await Promise.all([
                    api('/api/stats'),
                    api('/api/categories'),
                ]);
                this.stats = stats;
                this.allCategories = categories;
            } catch (e) {}
        },
        async loadExportPreview() {
            try {
                let url = '/api/export/preview?';
                if (this.exportCategoryId) url += `category_id=${this.exportCategoryId}&`;
                this.exportPreview = await api(url);
                this.exportPreviewExpanded = false;
            } catch (e) {
                this.exportPreview = { count: null, papers: [] };
            }
        },
        handleDrop(event) {
            this.dragOver = false;
            const file = event.dataTransfer.files[0];
            if (file && file.name.endsWith('.ris')) {
                this.uploadRisFile(file);
            }
        },
        handleFileSelect(event) {
            const file = event.target.files[0];
            if (file) {
                this.uploadRisFile(file);
            }
        },
        async uploadRisFile(file) {
            this.importing = true;
            this.importResult = null;
            try {
                const formData = new FormData();
                formData.append('file', file);
                const resp = await fetch('/api/import/ris', {
                    method: 'POST',
                    body: formData,
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail || 'Import fehlgeschlagen');
                }
                this.importResult = await resp.json();
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            } catch (e) {
                alert('Import fehlgeschlagen: ' + e.message);
            }
            this.importing = false;
        },
        async loadLicenseStatus() {
            try {
                const data = await api('/api/license/status');
                this.applyLicenseStatus(data);
            } catch(e) { /* silent */ }
        },
        applyLicenseStatus(data) {
            if (!data) return;
            this.licenseActivated = !!data.activated;
            this.licenseKeyMasked = data.key || '';
            this.licenseTrial = data.trial || null;
            this.licenseLegacyNote = data.legacy_note ? translateDetail(data.legacy_note) : '';
            if (data.checkout_url) this.licenseCheckoutUrl = data.checkout_url;
        },
        async activateLicense() {
            if (!this.licenseKey || this.licenseLoading) return;
            this.licenseLoading = true;
            this.licenseMessage = '';
            try {
                const result = await api('/api/license/activate', {
                    method: 'POST',
                    body: JSON.stringify({ key: this.licenseKey }),
                });
                if (result.activated) {
                    this.applyLicenseStatus(result);
                    this.licenseKey = '';
                    this.licenseError = false;
                    this.licenseMessage = t('license.activatedThanks');
                    window.dispatchEvent(new CustomEvent('license-activated'));
                } else {
                    this.licenseError = true;
                    this.licenseMessage = result.error ? translateDetail(result.error) : t('license.activationFailed');
                }
            } catch(e) {
                this.licenseError = true;
                this.licenseMessage = t('license.activationFailedRetry');
            } finally {
                this.licenseLoading = false;
            }
        },
    },
};


// =============================================================================
// App Component
// =============================================================================

const App = {
    components: { Sidebar },
    template: `
        <div class="lb-root"
             @dragover.prevent="onGlobalDragOver"
             @dragleave.prevent="onGlobalDragLeave"
             @drop.prevent="onGlobalDrop">
            <Sidebar ref="sidebar" :license-activated="licenseActivated" />
            <div class="lb-main">
                <!-- Update-Hinweis (#148): in der installierten Version genügt ein
                     Klick — die App lädt den Installer, startet ihn und beendet
                     sich; der Installer bringt sie neu hoch. Alles, was dabei
                     schiefgehen kann, endet beim manuellen Link daneben, der
                     immer da ist. -->
                <div v-if="updateAvailable"
                     data-testid="update-banner"
                     class="bg-accent-soft border-b border-accent-soft px-4 py-2 text-sm text-accent-ink flex items-center justify-between flex-shrink-0">
                    <span>🆕 {{ $t('update.available') }} <strong>{{ latestVersion }}</strong></span>
                    <div class="flex items-center gap-3">
                        <span v-if="updateMessage"
                              data-testid="update-message"
                              :class="updateFailed ? 'text-red-700' : 'text-accent-ink'"
                              class="text-xs">{{ updateMessage }}</span>
                        <button v-if="canAutoUpdate"
                                @click="installUpdate"
                                :disabled="updateRunning"
                                data-testid="update-now"
                                class="bg-accent text-white text-xs font-semibold px-3 py-1.5 rounded-md hover:bg-accent-ink disabled:opacity-50 transition-colors">
                            <span v-if="updateRunning">{{ $t('update.running') }}</span>
                            <span v-else>{{ $t('update.now') }}</span>
                        </button>
                        <a v-if="downloadUrl"
                           :href="downloadUrl" target="_blank" rel="noopener"
                           data-testid="update-manual-link"
                           class="text-accent-ink underline hover:text-accent-ink font-medium">
                            {{ installerUrl ? $t('update.downloadInstaller') : $t('update.openReleasePage') }}
                        </a>
                        <a v-if="!isFrozen"
                           href="https://github.com/tobiasbartlog/localbib" target="_blank" rel="noopener"
                           class="text-accent text-xs">git pull</a>
                    </div>
                </div>
                <!-- Purchase-prompt banner -->
                <div v-if="bannerVisible"
                     class="px-4 py-3 flex items-center justify-between flex-shrink-0"
                     style="background:var(--lb-accent);color:#fff;">
                    <div class="flex items-center gap-3">
                        <span class="text-sm font-medium" data-testid="banner-message">
                            {{ bannerMessage }}
                        </span>
                        <!-- Das Pill sitzt auf dem Akzentband, nicht auf dem Karton:
                             weiss bleibt weiss, und die Schrift darauf bleibt der
                             *helle* Accent-Ink. Das Dark-Token (#ffb084) ist Tinte
                             fuer dunkle Flaechen und waere auf Weiss unlesbar. -->
                        <a v-if="bannerUrl" :href="bannerUrl" target="_blank" rel="noopener"
                           class="text-xs font-semibold px-3 py-1 rounded-full transition-colors"
                           style="background:#fff;color:#7a2808;">
                            {{ $t('banner.buyNow') }}
                        </a>
                    </div>
                    <button @click="dismissBanner"
                            class="hover:text-white transition-colors ml-4 text-lg leading-none"
                            style="color:rgba(255,255,255,0.75);"
                            :aria-label="$t('common.close')">×</button>
                </div>
                <!-- LLM-Funktionen aus, weil kein Anbieter konfiguriert ist (#140).
                     Kein Fehler, nur eine Ansage samt Weg dorthin. -->
                <div v-if="llmOffHintVisible"
                     data-testid="llm-off-hint"
                     class="px-4 py-2 text-sm flex items-center justify-between flex-shrink-0 bg-amber-50 border-b border-amber-200 text-amber-900">
                    <span>
                        {{ $t('banner.llmOff') }}
                        <a href="#/settings" class="underline font-medium">{{ $t('banner.setUpNow') }}</a>
                    </span>
                    <button @click="llmOffHintVisible = false"
                            class="ml-4 text-lg leading-none text-amber-700 hover:text-amber-900"
                            :aria-label="$t('common.close')">×</button>
                </div>
                <main class="flex-1 overflow-y-auto">
                    <router-view />
                </main>
            </div>
            <!-- /version-check notification + main content wrapper -->
            <!-- Abgelaufene Testphase im fertigen Build (#144, ADR-0015):
                 blockierender Aktivierungsdialog. Der Server entscheidet, ob er
                 kommt: blocked im Lizenzstatus — eine Quellinstallation sieht ihn nie, denn
                 sie wird weder getestet noch gesperrt. Kein Schliessen-Knopf:
                 hier geht es nur mit Schlüssel weiter. -->
            <div v-if="licenseBlocked || gateConfirming"
                 data-testid="license-gate"
                 class="fixed inset-0 z-[60] flex items-center justify-center p-4"
                 style="background:rgba(0,0,0,0.6);">
                <div class="bg-white rounded-2xl shadow-xl w-full max-w-lg p-6">
                    <!-- Erfolgreiche Aktivierung (#156): eine Bestaetigung, die der
                         Kunde selbst wegklickt, statt dass der Dialog wortlos
                         verschwindet — genau der Moment, in dem er gerade bezahlt hat. -->
                    <template v-if="gateConfirming">
                        <div data-testid="license-gate-confirm">
                            <h2 class="text-lg font-semibold text-gray-900 mb-1">
                                {{ $t('gate.unlockedHeading') }}
                            </h2>
                            <p class="text-sm text-gray-500 mb-3">
                                {{ $t('gate.unlockedBody') }}
                            </p>
                            <p v-if="gateConfirmKey" class="text-sm text-gray-700 mb-3">
                                {{ $t('gate.keyLabel') }} <code data-testid="license-gate-confirm-key">{{ gateConfirmKey }}</code>
                            </p>
                            <!-- Nur wenn die Aktivierungsantwort die Zahl tatsaechlich
                                 mitliefert (#156) — geraten wird hier nichts, das Limit
                                 setzt Polar durch. -->
                            <p v-if="gateConfirmRemaining !== null"
                               data-testid="license-gate-confirm-remaining"
                               class="text-sm text-gray-500 mb-3">
                                {{ $t('gate.remainingDevices', { remaining: gateConfirmRemaining }) }}
                            </p>
                            <p class="text-sm text-gray-500 italic mb-5">{{ $t('gate.enjoy') }}</p>
                            <div class="flex justify-end">
                                <button @click="dismissActivationConfirm"
                                        data-testid="license-gate-confirm-dismiss"
                                        class="bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                                    {{ $t('gate.getStarted') }}
                                </button>
                            </div>
                        </div>
                    </template>
                    <template v-else>
                        <h2 class="text-lg font-semibold text-gray-900 mb-1">{{ $t('gate.trialOverHeading') }}</h2>
                        <p class="text-sm text-gray-500 mb-5">
                            {{ $t('gate.trialOverBody', { price: licensePriceDisplay }) }}
                        </p>

                        <p v-if="licenseLegacyNote"
                           data-testid="license-gate-legacy"
                           class="text-sm bg-amber-50 border border-amber-200 text-amber-900 rounded-lg px-4 py-3 mb-5">
                            {{ licenseLegacyNote }}
                        </p>

                        <div class="flex gap-3 items-start mb-3">
                            <input v-model="gateKey"
                                   data-testid="license-gate-input"
                                   type="text"
                                   spellcheck="false"
                                   placeholder="LB--XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"
                                   @keyup.enter="activateFromGate"
                                   class="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none">
                            <button @click="activateFromGate"
                                    data-testid="license-gate-activate"
                                    :disabled="gateLoading || !gateKey"
                                    class="bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-50 transition-colors">
                                <span v-if="gateLoading">{{ $t('license.activating') }}</span>
                                <span v-else>{{ $t('license.activate') }}</span>
                            </button>
                        </div>

                        <p v-if="gateMessage"
                           data-testid="license-gate-message"
                           class="text-sm mb-3 text-red-600">{{ gateMessage }}</p>

                        <p class="text-sm text-gray-500">
                            {{ $t('gate.noLicenseYet') }}
                            <a v-if="bannerUrl" :href="bannerUrl" target="_blank" rel="noopener"
                               data-testid="license-gate-buy"
                               class="text-accent underline">{{ $t('license.buy') }}</a>
                        </p>
                        <p class="text-xs text-gray-400 mt-4">
                            {{ $t('gate.agplNote') }}
                        </p>
                    </template>
                </div>
            </div>
            <!-- First-run onboarding (#140): eigener LLM-Anbieter + eigene
                 CrossRef-Adresse. Ueberspringbar — die App bleibt nutzbar,
                 nur die LLM-Funktionen bleiben aus. -->
            <div v-if="onboardingVisible"
                 data-testid="onboarding-dialog"
                 class="fixed inset-0 z-50 flex items-center justify-center p-4"
                 style="background:rgba(0,0,0,0.45);">
                <div class="bg-white rounded-2xl shadow-xl w-full max-w-lg p-6">
                    <template v-if="onboardingPage === 1">
                    <h2 class="text-lg font-semibold text-gray-900 mb-1">{{ $t('onboarding.heading') }}</h2>
                    <p class="text-sm text-gray-500 mb-5">
                        {{ $t('onboarding.intro') }}
                    </p>

                    <label class="block text-xs font-medium text-gray-700 mb-1">{{ $t('settings.language') }}</label>
                    <select :value="$lang()" @change="changeLanguage($event.target.value)"
                            data-testid="onboarding-language"
                            class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4">
                        <option value="en">{{ $t('settings.languageEn') }}</option>
                        <option value="de">{{ $t('settings.languageDe') }}</option>
                    </select>

                    <label class="block text-xs font-medium text-gray-700 mb-1">{{ $t('settings.field.provider') }}</label>
                    <select v-model="onboardingProvider"
                            data-testid="onboarding-provider"
                            class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4">
                        <option value="">{{ $t('onboarding.setUpLater') }}</option>
                        <option v-for="p in onboardingProviders" :key="p.id" :value="p.id">{{ p.label }}</option>
                    </select>

                    <template v-if="onboardingProvider === 'custom'">
                        <label class="block text-xs font-medium text-gray-700 mb-1">{{ $t('settings.field.baseUrl') }}</label>
                        <input v-model="onboardingBaseUrl"
                               data-testid="onboarding-base-url"
                               type="text" placeholder="http://localhost:11434/v1"
                               class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4 font-mono">
                    </template>

                    <label class="block text-xs font-medium text-gray-700 mb-1">{{ $t('settings.field.apiKey') }}</label>
                    <input v-model="onboardingApiKey"
                           data-testid="onboarding-api-key"
                           type="password" placeholder="sk-…"
                           class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4">

                    <label class="block text-xs font-medium text-gray-700 mb-1">
                        {{ $t('onboarding.mailtoLabel') }}
                    </label>
                    <input v-model="onboardingMailto"
                           data-testid="onboarding-mailto"
                           type="email" placeholder="you@university.example"
                           class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-1">
                    <p class="text-xs text-gray-400 mb-5">
                        {{ $t('onboarding.mailtoHint') }}
                    </p>

                    <div class="flex items-center justify-end gap-3">
                        <button @click="skipOnboarding"
                                data-testid="onboarding-skip"
                                class="text-sm text-gray-500 hover:text-gray-700">
                            {{ $t('onboarding.skip') }}
                        </button>
                        <button @click="saveOnboarding"
                                data-testid="onboarding-save"
                                :disabled="onboardingSaving"
                                class="bg-accent text-white text-sm font-semibold px-4 py-2 rounded-lg hover:bg-accent-ink transition-colors disabled:opacity-50">
                            {{ $t('common.save') }}
                        </button>
                    </div>
                    </template>

                    <!-- Page two (#178): the migration offer. Shown after page
                         one was answered either way — ONBOARDING_COMPLETED is
                         written by then, so this page neither blocks nor is
                         ever asked again. -->
                    <template v-else>
                        <h2 class="text-lg font-semibold text-gray-900 mb-1">{{ $t('migrate.onboarding.heading') }}</h2>
                        <p class="text-sm text-gray-500 mb-5">{{ $t('migrate.onboarding.intro') }}</p>
                        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-5">
                            <button v-for="src in onboardingMigrationSources" :key="src.id"
                                    type="button"
                                    @click="startMigration(src.id)"
                                    :data-testid="'onboarding-migrate-' + src.id"
                                    class="border border-gray-200 rounded-lg px-4 py-3 text-sm font-medium text-gray-800 text-left hover:border-accent hover:text-accent-ink transition-colors">
                                {{ $t(src.labelKey) }}
                            </button>
                        </div>
                        <div class="flex items-center justify-end">
                            <button @click="closeOnboarding"
                                    data-testid="onboarding-migrate-later"
                                    class="text-sm text-gray-500 hover:text-gray-700">
                                {{ $t('migrate.onboarding.notNow') }}
                            </button>
                        </div>
                    </template>
                </div>
            </div>
            <!-- Global drag-drop overlay (shown outside ImportPage) -->
            <div v-if="globalDragging" class="fixed inset-0 bg-accent/20 z-40 flex items-center justify-center pointer-events-none">
                <div class="bg-white rounded-2xl shadow-xl p-8 text-center pointer-events-none">
                    <div class="w-16 h-16 mx-auto mb-3 bg-accent-soft text-accent rounded-full flex items-center justify-center">
                        <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                    </div>
                    <p class="text-lg font-semibold text-gray-900">{{ $t('import.dropHere') }}</p>
                    <p class="text-sm text-gray-500 mt-1">{{ $t('import.autoImported') }}</p>
                </div>
            </div>
        </div>
    `,
    data() {
        return {
            globalDragging: false,
            _dragLeaveTimer: null,
            updateAvailable: false,
            latestVersion: '',
            releaseUrl: '',
            downloadUrl: '',
            installerUrl: '',
            canAutoUpdate: false,
            updateRunning: false,
            updateFailed: false,
            updateMessage: '',
            isFrozen: false,
            licenseActivated: false,
            licenseTrial: null,
            licenseBlocked: false,
            licenseLegacyNote: '',
            licensePriceAmount: null,
            licensePriceCurrency: 'EUR',
            gateKey: '',
            gateLoading: false,
            gateMessage: '',
            gateConfirming: false,
            gateConfirmKey: '',
            gateConfirmRemaining: null,
            bannerVisible: false,
            bannerUrl: '',
            onboardingVisible: false,
            onboardingPage: 1,
            llmOffHintVisible: false,
            onboardingSaving: false,
            onboardingProvider: '',
            onboardingBaseUrl: '',
            onboardingApiKey: '',
            onboardingMailto: '',
            onboardingProviders: ONBOARDING_FALLBACK_PROVIDERS,
            onboardingMigrationSources: ONBOARDING_MIGRATION_SOURCES,
        };
    },
    computed: {
        // Der Preis kommt als Zahl plus Waehrung vom Server; das
        // Dezimaltrennzeichen entscheidet die gewaehlte Sprache, nicht das
        // Backend (ADR-0018).
        licensePriceDisplay() {
            if (this.licensePriceAmount == null) return '';
            return new Intl.NumberFormat(uiLocale(), {
                style: 'currency', currency: this.licensePriceCurrency,
            }).format(this.licensePriceAmount);
        },
        // Der Kaufhinweis spiegelt die Testphase (#144). Ohne Testphase —
        // Quellinstallation oder bereits aktiviert — bleibt der neutrale Satz.
        bannerMessage() {
            const trial = this.licenseTrial;
            const price = this.licensePriceDisplay;
            if (trial && !trial.expired) {
                return '✨ ' + tn('banner.trialLeft', trial.days_remaining)
                    + ' ' + t('banner.unlockFor', { price: price });
            }
            return '✨ ' + t('banner.buyFor', { price: price });
        },
    },
    methods: {
        async checkVersion() {
            try {
                const result = await api('/api/version-check');
                this.isFrozen = !!result.is_frozen;
                if (result && result.update_available) {
                    this.updateAvailable = true;
                    this.latestVersion = result.latest || '';
                    this.releaseUrl = result.release_url || '';
                    this.downloadUrl = result.download_url || '';
                    this.installerUrl = result.installer_url || '';
                    // Der Server entscheidet, ob der Ein-Klick-Weg offensteht
                    // (fertige Installation + Installer im Release) — die SPA
                    // rechnet nichts nach.
                    this.canAutoUpdate = !!result.can_auto_update;
                }
            } catch(e) { /* silent degradation */ }
        },
        // Ein Klick, ausdrücklich vom Nutzer: Installer laden, starten, App
        // beenden. Kein Hintergrunddienst, kein stilles Auto-Update (#148).
        async installUpdate() {
            if (this.updateRunning) return;
            this.updateRunning = true;
            this.updateFailed = false;
            this.updateMessage = t('update.downloading');
            try {
                const result = await api('/api/update/install', { method: 'POST' });
                this.updateMessage = (result && result.message)
                    || t('update.installing');
            } catch (e) {
                this.updateRunning = false;
                this.updateFailed = true;
                this.updateMessage = e.message || 'Update fehlgeschlagen.';
            }
        },
        onGlobalDragOver(e) {
            // Don't show overlay if we're on import or thesis page (they handle their own drops)
            if (this.$route && (this.$route.name === 'import' || this.$route.name === 'thesis')) return;
            if (e.dataTransfer && e.dataTransfer.types && e.dataTransfer.types.includes('Files')) {
                clearTimeout(this._dragLeaveTimer);
                this.globalDragging = true;
            }
        },
        onGlobalDragLeave() {
            this._dragLeaveTimer = setTimeout(() => { this.globalDragging = false; }, 100);
        },
        onGlobalDrop(e) {
            this.globalDragging = false;
            if (this.$route && (this.$route.name === 'import' || this.$route.name === 'thesis' || this.$route.name === 'research-chat')) return; // Import/Thesis/Chat handle their own
            const files = Array.from(e.dataTransfer.files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
            if (!files.length) return;
            // Navigate to import page and dispatch event with files
            this.$router.push({ name: 'import' }).then(() => {
                setTimeout(() => {
                    window.dispatchEvent(new CustomEvent('global-pdf-drop', { detail: { files } }));
                }, 200);
            });
        },
        async checkLicenseStatus() {
            try {
                const data = await api('/api/license/status');
                this.applyLicenseStatus(data);
            } catch(e) { /* silent */ }
        },
        // Der Server entscheidet ueber Testphase und Sperre — die SPA rechnet
        // nichts nach, sie zeigt an (#144).
        applyLicenseStatus(data) {
            if (!data) return;
            this.licenseActivated = !!data.activated;
            this.licenseTrial = data.trial || null;
            this.licenseBlocked = !!data.blocked;
            this.licenseLegacyNote = data.legacy_note ? translateDetail(data.legacy_note) : '';
            if (data.price_amount != null) this.licensePriceAmount = data.price_amount;
            if (data.price_currency) this.licensePriceCurrency = data.price_currency;
            if (data.checkout_url) this.bannerUrl = data.checkout_url;
        },
        async checkBannerState() {
            if (this.licenseActivated) return;
            try {
                const result = await api('/api/banner/state');
                this.bannerVisible = !!result.show;
                if (result.trial) this.licenseTrial = result.trial;
            } catch(e) { /* silent */ }
        },
        // Aktivierung aus dem blockierenden Dialog. Bewusst eigenstaendig: der
        // Dialog steht vor allem anderen — in die Einstellungen kommt der
        // Nutzer an ihm vorbei gar nicht.
        async activateFromGate() {
            if (!this.gateKey || this.gateLoading) return;
            this.gateLoading = true;
            this.gateMessage = '';
            try {
                const result = await api('/api/license/activate', {
                    method: 'POST',
                    body: JSON.stringify({ key: this.gateKey }),
                });
                if (result.activated) {
                    this.applyLicenseStatus(result);
                    this.licenseActivated = true;
                    this.licenseBlocked = false;
                    this.bannerVisible = false;
                    this.gateKey = '';
                    // Dialog bleibt stehen, zeigt aber die Bestaetigung statt sich
                    // wortlos zu schliessen (#156) — der Kunde klickt sie selbst weg.
                    this.gateConfirmKey = result.key || '';
                    this.gateConfirmRemaining =
                        typeof result.activations_remaining === 'number'
                            ? result.activations_remaining
                            : null;
                    this.gateConfirming = true;
                    window.dispatchEvent(new CustomEvent('license-activated'));
                } else {
                    this.gateMessage = result.error ? translateDetail(result.error) : t('license.activationFailed');
                }
            } catch(e) {
                this.gateMessage = t('license.activationFailedRetry');
            } finally {
                this.gateLoading = false;
            }
        },
        // Bestaetigung selbst wegklicken (#156) — landet in der App, nicht
        // zurueck im Aktivierungsformular: der Dialog ist bereits weg.
        dismissActivationConfirm() {
            this.gateConfirming = false;
        },
        async dismissBanner() {
            this.bannerVisible = false;
            try {
                await api('/api/banner/dismiss', { method: 'POST' });
            } catch(e) { /* silent */ }
        },
        // --- First-run onboarding (#140) -----------------------------------
        async checkOnboarding() {
            let settings = {};
            let status = {};
            try {
                settings = await api('/api/settings') || {};
                status = await api('/api/llm/status') || {};
            } catch(e) { return; /* silent: nie den Start blockieren */ }
            // "Eingerichtet" heisst: es gibt eine Verbindung. Ob sie einen Key
            // hat, ist keine Frage mehr — Ollama hat keinen und laeuft trotzdem.
            const configured = (status.connections || 0) > 0;
            if (settings.ONBOARDING_COMPLETED === 'true') {
                // Schon gefragt: nicht erneut fragen, aber sagen, dass die
                // LLM-Funktionen aus sind, solange keine Rolle bereit ist.
                this.llmOffHintVisible = !status.reasoning;
                return;
            }
            // Bestehende Installationen mit konfigurierter Verbindung nie behelligen.
            if (configured) return;
            this.onboardingMailto = settings.CROSSREF_MAILTO || '';
            // Vorschlag aus der Browsersprache, aber nur beim First Run: ein
            // spaeter gesetztes UI_LANGUAGE ist eine Entscheidung und wird
            // nicht ueberstimmt.
            if (!settings.UI_LANGUAGE) setUiLang(browserLanguage());
            this.onboardingVisible = true;
            try {
                const data = await api('/api/llm/providers');
                if (data && data.providers && data.providers.length) {
                    this.onboardingProviders = data.providers;
                }
            } catch(e) { /* Fallback-Liste bleibt stehen */ }
        },
        // `connection` (optional): die eine Verbindung aus dem Dialog. Sie
        // wird als "default" angelegt und an alle drei Rollen gebunden — die
        // Modelle waehlt der Nutzer unter Einstellungen -> LLM, bis dahin
        // bleiben die LLM-Funktionen aus und der Hinweis sagt das.
        async persistOnboarding(payload, connection = null) {
            this.onboardingSaving = true;
            let ready = false;
            try {
                await api('/api/settings', {
                    method: 'PUT',
                    body: JSON.stringify({ ...payload, ONBOARDING_COMPLETED: 'true' }),
                });
                if (connection) {
                    const conn = { id: 'default', ...connection };
                    const bind = { connection_id: 'default', model: '' };
                    const data = await api('/api/llm/config', {
                        method: 'PUT',
                        body: JSON.stringify({
                            connections: [conn],
                            roles: { reasoning: bind, fast: bind, embedding: bind },
                        }),
                    });
                    ready = !!(data && data.status && data.status.reasoning);
                }
            } catch(e) { /* silent: der Dialog darf nie zur Sackgasse werden */ }
            this.onboardingSaving = false;
            // Answered — on to the migration offer (#178). The dialog stays up
            // for one more page; ONBOARDING_COMPLETED is already written, so
            // closing it from there costs nothing.
            this.onboardingPage = 2;
            this.llmOffHintVisible = !ready;
        },
        closeOnboarding() {
            this.onboardingVisible = false;
        },
        // A source button is a shortcut into the wizard, not a decision: the
        // card is preselected there and every step stays undoable.
        startMigration(source) {
            this.onboardingVisible = false;
            this.$router.push({ path: '/migrate', query: { source } });
        },
        skipOnboarding() {
            // Nur merken, dass gefragt wurde, und die gewaehlte Sprache — sie
            // ist keine LLM-Konfiguration und geht beim Ueberspringen nicht
            // verloren.
            return this.persistOnboarding({ UI_LANGUAGE: uiLang.value });
        },
        changeLanguage(lang) {
            setUiLang(lang);
        },
        saveOnboarding() {
            const payload = { UI_LANGUAGE: uiLang.value };
            payload.CROSSREF_MAILTO = (this.onboardingMailto || '').trim();
            let connection = null;
            if (this.onboardingProvider) {
                const preset = this.onboardingProviders.find((p) => p.id === this.onboardingProvider);
                connection = {
                    label: preset ? preset.label : this.onboardingProvider,
                    provider: this.onboardingProvider,
                    base_url: (this.onboardingBaseUrl || '').trim(),
                    api_key: this.onboardingApiKey || '',
                };
            }
            return this.persistOnboarding(payload, connection);
        },
    },
    mounted() {
        this.checkVersion();
        this.checkOnboarding();
        window.addEventListener('refresh-sidebar', () => {
            if (this.$refs.sidebar) {
                this.$refs.sidebar.load();
            }
        });
        // Dark Mode (schon beim Script-Load angewandt, hier nochmal sicherheitshalber)
        applyDarkMode(localStorage.getItem('darkMode') === 'true');
        // Custom Icon als Favicon laden
        api('/api/appearance/icon').then(data => {
            if (data && data.path) {
                let link = document.querySelector("link[rel~='icon']");
                if (!link) {
                    link = document.createElement('link');
                    link.rel = 'icon';
                    document.head.appendChild(link);
                }
                link.href = data.path;
            }
        }).catch(() => {});
        // Lizenzstatus zuerst (rein lokal), dann den Kaufhinweis pruefen —
        // wer aktiviert hat, wird gar nicht erst gefragt.
        this.checkLicenseStatus().then(() => this.checkBannerState());
        // Aktivierung in den Einstellungen blendet den Kaufhinweis sofort aus.
        window.addEventListener('license-activated', () => {
            this.licenseActivated = true;
            this.licenseBlocked = false;
            this.licenseTrial = null;
            this.bannerVisible = false;
        });
    }
};


// =============================================================================
// Global Analysis State (persists across route changes)
// =============================================================================
const analysisStore = {
    networkData: null,
    stats: null,
    selectedNode: null,
    depth: 1,
    activeTab: 'network',
    sortKey: 'referenced_by_count',
    sortDir: -1,
    ownSortKey: 'cited_by_count',
    ownSortDir: -1,
    refSortKey: 'cited_by_count',
    refSortDir: -1,
    refFilter: 'all',
    minRefs: 1,
    minCitations: 0,
    topNRefs: 0,
    bulkExtractResult: null,
    chatMessages: [],
    chatContextPaper: null,
};

// =============================================================================
// ResearchChatPage Component (RAG-basierter Paper-Chat)
// =============================================================================

// Persistent chat state (survives route changes)
const chatStore = {
    messages: [],
    paperIds: [],
    selectedPapers: [],
    totalChunks: 0,
};

const ResearchChatPage = {
    template: `
        <div class="flex flex-col h-[calc(100vh-2rem)] max-w-5xl mx-auto p-6">
            <!-- Back + Header -->
            <button @click="$router.back()"
                    class="flex items-center gap-1.5 text-sm text-accent hover:text-accent-ink mb-3 transition-colors flex-shrink-0">
                <span v-html="icons.back"></span>
                {{ $t('common.back') }}
            </button>
            <div class="flex items-center justify-between mb-4 flex-shrink-0">
                <div>
                    <h2 class="text-xl font-semibold text-gray-900 flex items-center gap-2">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-6 h-6 text-accent" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                        {{ $t('nav.researchChat') }}
                    </h2>
                    <p class="text-sm text-gray-500 mt-0.5">{{ $t('chat.subheading') }}</p>
                </div>
                <div class="flex items-center gap-2">
                    <span v-if="paperIds.length" class="text-xs bg-accent-soft text-accent-ink px-2 py-1 rounded-full font-medium">
                        {{ paperIds.length }} Paper &middot; {{ totalChunks }} Chunks
                    </span>
                    <button v-if="messages.length" @click="startNewChat"
                            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-colors"
                            :title="$t('chat.newChat')">
                        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                        {{ $t('chat.newChatShort') }}
                    </button>
                    <button @click="showPaperSelector = true"
                            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-colors">
                        <span v-html="icons.papers"></span>
                        {{ $t('chat.chooseItems') }}
                    </button>
                </div>
            </div>

            <!-- Paper Chips -->
            <div v-if="selectedPapers.length > 0" class="flex flex-wrap gap-1.5 mb-3 flex-shrink-0">
                <span v-for="p in selectedPapers" :key="p.id"
                      class="inline-flex items-center gap-1 bg-accent-soft text-accent-ink text-xs px-2 py-0.5 rounded-full">
                    {{ p.title ? p.title.substring(0, 40) : 'Paper ' + p.id }}{{ p.title && p.title.length > 40 ? '...' : '' }}
                    <button @click="removePaper(p.id)" class="ml-0.5 text-accent hover:text-accent-ink">&times;</button>
                </span>
            </div>

            <!-- Add-on slot "research-chat-context" (#190): each component renders its
                 own toggle; while enabled, its block travels as extra_context. -->
            <div v-for="s in chatContextSlots" :key="s.key" :data-addon-slot="s.key"
                 class="flex items-center gap-2 mb-3 flex-shrink-0 px-3 py-2 bg-gray-50 rounded-lg border border-gray-200">
                <component :is="s.component" :enabled="!!(addonChat[s.addon] && addonChat[s.addon].enabled)"
                           @update:enabled="setAddonChat(s.addon, { enabled: !!$event })"
                           @update:context="setAddonChat(s.addon, { context: $event })" />
            </div>
            <!-- Chunking Progress -->
            <div v-if="chunking" class="bg-accent-soft border border-accent-soft rounded-lg p-3 mb-3 flex items-center gap-2 flex-shrink-0">
                <div class="spinner-sm"></div>
                <span class="text-sm text-accent-ink">{{ $t('chat.preparingTexts') }} {{ chunkProgress }}</span>
            </div>

            <!-- Chat Messages -->
            <div class="flex-1 overflow-y-auto bg-white rounded-lg border border-gray-200 mb-3 p-4 space-y-4" ref="chatMessages">
                <div v-if="messages.length === 0" class="flex flex-col items-center justify-center h-full text-gray-400">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-12 h-12 mb-3 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                    <p class="text-sm">{{ $t('chat.emptyTitle') }}</p>
                    <p class="text-xs mt-1">{{ $t('chat.emptyHint') }}</p>
                </div>

                <div v-for="(msg, i) in messages" :key="i" :data-msg-idx="i"
                     :class="msg.role === 'user' ? 'flex justify-end' : 'flex justify-start'">
                    <div :class="msg.role === 'user'
                        ? 'bg-accent text-white rounded-2xl rounded-br-md px-4 py-2.5 max-w-[75%]'
                        : 'bg-gray-100 text-gray-800 rounded-2xl rounded-bl-md px-4 py-2.5 max-w-[85%]'">
                        <div class="text-sm whitespace-pre-wrap leading-relaxed" v-html="formatMessage(msg.content, msg.sources)"></div>
                        <!-- Sources -->
                        <div v-if="msg.sources && msg.sources.length" class="mt-2 pt-2 border-t"
                             :class="msg.role === 'user' ? 'border-accent' : 'border-gray-200'">
                            <p class="text-xs font-medium mb-1" :class="msg.role === 'user' ? 'text-accent-soft' : 'text-gray-500'">{{ $t('chat.sources') }}:</p>
                            <div v-for="(s, si) in msg.sources" :key="si" class="text-xs mb-1 group/src relative cursor-pointer"
                                 :class="msg.role === 'user' ? 'text-accent-soft' : 'text-gray-500'"
                                 @click="openPdfModal(s)">
                                <span class="hover:underline font-medium"
                                    :class="msg.role === 'user' ? 'text-accent-soft' : 'text-accent'">
                                    [{{ si + 1 }}] {{ s.title || 'Paper ' + s.paper_id }}
                                </span>
                                <span v-if="s.authors" class="opacity-70"> &mdash; {{ s.authors }}</span>
                                <span v-if="s.pages_referenced && s.pages_referenced.length">, {{ $t('papers.pagesAbbr') }} {{ s.pages_referenced.join(', ') }}</span>
                                <!-- Hover Preview -->
                                <div v-if="s.previews && s.previews.length"
                                     class="hidden group-hover/src:block absolute left-0 bottom-full mb-1 z-30 w-96 bg-white border border-gray-200 rounded-lg shadow-xl p-3 text-xs text-gray-700 max-h-48 overflow-y-auto">
                                    <div v-for="(pv, pi) in s.previews" :key="pi" class="mb-2 last:mb-0">
                                        <span class="font-semibold text-accent">{{ $t('papers.pagesAbbr') }} {{ pv.page }}:</span>
                                        <span class="ml-1">{{ pv.text }}{{ pv.text.length >= 300 ? '...' : '' }}</span>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Typing indicator -->
                <div v-if="loading" class="flex justify-start">
                    <div class="bg-gray-100 rounded-2xl rounded-bl-md px-4 py-3">
                        <div class="flex gap-1">
                            <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 0ms"></span>
                            <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 150ms"></span>
                            <span class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 300ms"></span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Source Badge Hover Tooltip (outside scrollable container) -->
            <div v-if="badgeTooltip.visible" class="source-tooltip" :style="badgeTooltip.style"
                 @mouseenter="badgeTooltip.pinned = true" @mouseleave="hideBadgeTooltip">
                <p class="font-semibold text-gray-900 mb-1.5 text-xs leading-snug">{{ badgeTooltip.title }}</p>
                <div v-for="(pv, pi) in badgeTooltip.previews" :key="pi" class="mb-2 last:mb-0">
                    <span class="font-semibold text-accent">{{ $t('papers.pagesAbbr') }} {{ pv.page }}:</span>
                    <span class="ml-1 text-gray-700">{{ pv.text }}{{ pv.text.length >= 300 ? '...' : '' }}</span>
                </div>
            </div>

            <!-- PDF Preview Modal -->
            <div v-if="pdfModal.show" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[10001]" @click.self="closePdfModal">
                <div class="pdf-preview-modal">
                    <div class="flex items-center gap-3 px-5 py-3.5 border-b border-gray-200 bg-gray-50 rounded-t-2xl flex-shrink-0">
                        <div class="flex-1 min-w-0">
                            <p class="text-sm font-semibold text-gray-900 truncate">{{ pdfModal.paperTitle }}</p>
                            <p class="text-xs text-gray-500 mt-0.5">{{ pdfModal.authors }} &middot; {{ $t('chat.page', { page: pdfModal.page }) }}</p>
                        </div>
                        <button @click="openInPaperDetail"
                                class="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-accent-ink bg-accent-soft border border-accent-soft rounded-lg hover:bg-accent-soft transition-colors">
                            <svg xmlns='http://www.w3.org/2000/svg' class='w-3.5 h-3.5' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6'/><polyline points='15 3 21 3 21 9'/><line x1='10' y1='14' x2='21' y2='3'/></svg>
                            {{ $t('chat.fullView') }}
                        </button>
                        <button @click="closePdfModal"
                                class="w-8 h-8 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-colors"
                                :title="$t('chat.closeEsc')">
                            <svg xmlns='http://www.w3.org/2000/svg' class='w-5 h-5' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><line x1='18' y1='6' x2='6' y2='18'/><line x1='6' y1='6' x2='18' y2='18'/></svg>
                        </button>
                    </div>
                    <div class="flex-1 overflow-hidden">
                        <iframe :src="pdfModal.url" class="w-full h-full border-none"></iframe>
                    </div>
                    <div v-if="pdfModal.highlightText" class="px-5 py-3 border-t border-gray-200 bg-accent-soft rounded-b-2xl flex-shrink-0 max-h-40 overflow-y-auto">
                        <p class="text-xs font-semibold text-accent-ink mb-1">&#128204; {{ $t('chat.referencedPassage') }}</p>
                        <p class="text-xs text-gray-700 leading-relaxed italic">"{{ pdfModal.highlightText.substring(0, 600) }}{{ pdfModal.highlightText.length > 600 ? '...' : '' }}"</p>
                    </div>
                </div>
            </div>

            <!-- Input -->
            <div class="flex-shrink-0">
                <div class="flex gap-2">
                    <input v-model="question" @keydown.enter="askQuestion"
                           :disabled="loading || !paperIds.length"
                           type="text" :placeholder="$t('chat.inputPlaceholder')"
                           class="flex-1 border border-gray-300 rounded-lg px-4 py-2.5 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none disabled:bg-gray-50 disabled:text-gray-400" />
                    <button @click="askQuestion"
                            :disabled="loading || !question.trim() || !paperIds.length"
                            class="inline-flex items-center gap-1.5 bg-accent text-white px-5 py-2.5 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
                        {{ $t('chat.ask') }}
                    </button>
                    <button v-if="messages.length" @click="clearChat"
                            class="inline-flex items-center gap-1.5 px-3 py-2.5 rounded-lg text-sm font-medium border border-gray-300 text-gray-600 bg-white hover:bg-gray-50 transition-colors"
                            :title="$t('chat.clear')">
                        <span v-html="icons.trash"></span>
                    </button>
                </div>
            </div>

            <!-- Paper Selector Modal -->
            <div v-if="showPaperSelector" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="showPaperSelector = false">
                <div class="bg-white rounded-xl shadow-xl max-w-2xl w-full mx-4 max-h-[80vh] flex flex-col">
                    <div class="flex items-center justify-between p-5 border-b border-gray-200">
                        <h3 class="text-lg font-semibold text-gray-900">{{ $t('chat.selectItems') }}</h3>
                        <button @click="showPaperSelector = false" class="text-gray-400 hover:text-gray-600 p-1">
                            <span v-html="icons.x" style="width:20px;height:20px;"></span>
                        </button>
                    </div>
                    <div class="p-4 border-b border-gray-200">
                        <input v-model="paperSearch" type="text" :placeholder="$t('chat.searchItems')"
                               class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                    </div>
                    <div class="flex-1 overflow-y-auto p-4">
                        <div v-for="p in filteredAvailablePapers" :key="p.id"
                             class="flex items-center gap-3 p-2 rounded-lg cursor-pointer hover:bg-gray-50 transition-colors"
                             :class="paperIds.includes(p.id) ? 'bg-accent-soft' : ''"
                             @click="togglePaper(p)">
                            <input type="checkbox" :checked="paperIds.includes(p.id)" @click.stop="togglePaper(p)"
                                   class="rounded border-gray-300 text-accent focus:ring-accent" />
                            <div class="flex-1 min-w-0">
                                <p class="text-sm font-medium text-gray-900 truncate">{{ p.title || p.filename }}</p>
                                <p class="text-xs text-gray-500">{{ p.authors || '' }}<span v-if="p.year"> &middot; {{ p.year }}</span></p>
                            </div>
                        </div>
                        <div v-if="filteredAvailablePapers.length === 0" class="text-center py-8 text-sm text-gray-400">
                            {{ $t('analysis.noItemsFound') }}
                        </div>
                    </div>
                    <div class="p-4 border-t border-gray-200 flex items-center justify-between bg-gray-50 rounded-b-xl">
                        <span class="text-sm text-gray-500">{{ $t('chat.selectedCount', { count: paperIds.length }) }}</span>
                        <button @click="confirmPaperSelection"
                                class="bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                            {{ $t('doi.accept') }}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `,
    data() {
        return {
            icons,
            question: '',
            messages: chatStore.messages,
            loading: false,
            paperIds: chatStore.paperIds,
            selectedPapers: chatStore.selectedPapers,
            allPapers: [],
            totalChunks: chatStore.totalChunks,
            showPaperSelector: false,
            paperSearch: '',
            chunking: false,
            chunkProgress: '',
            badgeTooltip: { visible: false, pinned: false, title: '', previews: [], style: {} },
            _badgeHideTimer: null,
            pdfModal: { show: false, paperId: null, paperTitle: '', authors: '', page: 1, url: '', highlightText: '' },
            // Add-on chat contexts (#190): add-on id -> { enabled, context }.
            addonChat: {},
        };
    },
    computed: {
        chatContextSlots() {
            return addonSlots('research-chat-context');
        },
        filteredAvailablePapers() {
            if (!this.paperSearch.trim()) return this.allPapers;
            const q = this.paperSearch.toLowerCase();
            return this.allPapers.filter(p =>
                (p.title || '').toLowerCase().includes(q) ||
                (p.authors || '').toLowerCase().includes(q)
            );
        },
    },
    mounted() {
        // Event delegation for source-badge hover
        const container = this.$refs.chatMessages;
        if (container) {
            this._onBadgeEnter = (e) => {
                const badge = e.target.closest('.source-badge');
                if (!badge) return;
                clearTimeout(this._badgeHideTimer);
                const srcIdx = parseInt(badge.getAttribute('data-source-idx'));
                const msgEl = badge.closest('[data-msg-idx]');
                if (!msgEl) return;
                const msgIdx = parseInt(msgEl.getAttribute('data-msg-idx'));
                const msg = this.messages[msgIdx];
                if (!msg || !msg.sources || !msg.sources[srcIdx]) return;
                const src = msg.sources[srcIdx];
                if (!src.previews || !src.previews.length) return;
                // Position tooltip
                const rect = badge.getBoundingClientRect();
                const tooltipW = 384; // w-96 = 24rem = 384px
                let left = rect.left + rect.width / 2 - tooltipW / 2;
                if (left < 8) left = 8;
                if (left + tooltipW > window.innerWidth - 8) left = window.innerWidth - tooltipW - 8;
                let top = rect.top - 8;
                const showAbove = rect.top > 220;
                this.badgeTooltip = {
                    visible: true,
                    pinned: false,
                    title: (src.title || t('chat.source')) + (src.authors ? ' — ' + src.authors : ''),
                    previews: src.previews,
                    style: showAbove
                        ? { position: 'fixed', left: left + 'px', bottom: (window.innerHeight - rect.top + 6) + 'px', width: tooltipW + 'px' }
                        : { position: 'fixed', left: left + 'px', top: (rect.bottom + 6) + 'px', width: tooltipW + 'px' },
                };
            };
            this._onBadgeLeave = (e) => {
                const badge = e.target.closest('.source-badge');
                if (!badge) return;
                this._badgeHideTimer = setTimeout(() => {
                    if (!this.badgeTooltip.pinned) this.badgeTooltip.visible = false;
                }, 200);
            };
            container.addEventListener('mouseenter', this._onBadgeEnter, true);
            container.addEventListener('mouseleave', this._onBadgeLeave, true);
            // Badge CLICK -> open PDF modal instead of navigating
            this._onBadgeClick = (e) => {
                const badge = e.target.closest('.source-badge');
                if (!badge) return;
                e.preventDefault();
                e.stopPropagation();
                const srcIdx = parseInt(badge.getAttribute('data-source-idx'));
                const msgEl = badge.closest('[data-msg-idx]');
                if (!msgEl) return;
                const msgIdx = parseInt(msgEl.getAttribute('data-msg-idx'));
                const msg = this.messages[msgIdx];
                if (!msg || !msg.sources || !msg.sources[srcIdx]) return;
                this.openPdfModal(msg.sources[srcIdx]);
            };
            container.addEventListener('click', this._onBadgeClick, true);
        }
    },
    unmounted() {
        const container = this.$refs.chatMessages;
        if (container) {
            container.removeEventListener('mouseenter', this._onBadgeEnter, true);
            container.removeEventListener('mouseleave', this._onBadgeLeave, true);
            container.removeEventListener('click', this._onBadgeClick, true);
        }
        if (this._escHandler) document.removeEventListener('keydown', this._escHandler);
    },
    async created() {
        // Paper-Liste laden
        try {
            this.allPapers = await api('/api/papers');
        } catch (e) { console.error(e); }


        // Paper-IDs aus Query-Parametern lesen
        const q = this.$route.query;
        if (q.paper_ids) {
            const ids = q.paper_ids.split(',').map(Number).filter(Boolean);
            // Only reset if different papers requested
            const currentIds = this.paperIds.slice().sort().join(',');
            const newIds = ids.slice().sort().join(',');
            if (newIds !== currentIds) {
                this.paperIds.length = 0;
                ids.forEach(id => this.paperIds.push(id));
                this.selectedPapers.length = 0;
                this.allPapers.filter(p => ids.includes(p.id)).forEach(p => this.selectedPapers.push(p));
                this.messages.length = 0;
                chatStore.totalChunks = 0;
                this.totalChunks = 0;
                await this.ensureChunked();
            }
        }
    },
    methods: {
        setAddonChat(addonId, patch) {
            const prev = this.addonChat[addonId] || { enabled: false, context: null };
            this.addonChat = Object.assign({}, this.addonChat, { [addonId]: Object.assign({}, prev, patch) });
        },
        // The blocks of every active Add-on whose toggle is on, in id order.
        addonChatBlocks() {
            const blocks = [];
            for (const s of this.chatContextSlots) {
                const c = this.addonChat[s.addon];
                if (c && c.enabled && typeof c.context === 'string' && c.context.trim()) blocks.push(c.context);
            }
            return blocks;
        },
        togglePaper(p) {
            const idx = this.paperIds.indexOf(p.id);
            if (idx >= 0) {
                this.paperIds.splice(idx, 1);
                this.selectedPapers = this.selectedPapers.filter(sp => sp.id !== p.id);
            } else {
                this.paperIds.push(p.id);
                this.selectedPapers.push(p);
            }
        },
        removePaper(id) {
            this.paperIds = this.paperIds.filter(pid => pid !== id);
            this.selectedPapers = this.selectedPapers.filter(p => p.id !== id);
        },
        async confirmPaperSelection() {
            this.showPaperSelector = false;
            await this.ensureChunked();
        },
        async ensureChunked() {
            if (!this.paperIds.length) return;
            this.chunking = true;
            this.chunkProgress = '';
            try {
                // Check chunk status
                const status = await api('/api/research-chat/chunk-status?paper_ids=' + this.paperIds.join(','));
                const unchunked = status.papers.filter(p => !p.chunked);
                this.totalChunks = status.papers.reduce((sum, p) => sum + p.chunk_count, 0);

                if (unchunked.length > 0) {
                    this.chunkProgress = t('chat.processingItems', { count: unchunked.length });
                    // Chunk missing papers
                    const result = await api('/api/research-chat/chunk-papers?paper_ids=' + unchunked.map(p => p.paper_id).join('&paper_ids='), {
                        method: 'POST',
                    });
                    this.totalChunks += result.total_chunks;
                }
            } catch (e) {
                console.error('Chunking error:', e);
            }
            this.chunking = false;
            chatStore.totalChunks = this.totalChunks;
        },
        async askQuestion() {
            if (!this.question.trim() || !this.paperIds.length || this.loading) return;

            const q = this.question.trim();
            this.messages.push({ role: 'user', content: q });
            this.question = '';
            this.loading = true;

            this.$nextTick(() => {
                const el = this.$refs.chatMessages;
                if (el) el.scrollTop = el.scrollHeight;
            });

            try {
                // Build history (last messages, without sources)
                const history = this.messages.slice(0, -1).map(m => ({
                    role: m.role,
                    content: m.content,
                })).slice(-6);

                let extra_context;
                const addonBlocks = this.addonChatBlocks();
                if (addonBlocks.length) extra_context = [...(extra_context || []), ...addonBlocks];

                const result = await api('/api/research-chat/ask', {
                    method: 'POST',
                    body: JSON.stringify({
                        question: q,
                        paper_ids: this.paperIds,
                        history: history,
                        ...(extra_context ? { extra_context } : {}),
                    }),
                });

                this.messages.push({
                    role: 'assistant',
                    content: translateDetail(result.answer),
                    sources: result.sources || [],
                });
            } catch (e) {
                this.messages.push({
                    role: 'assistant',
                    content: t('error.generic', { message: e.message || t('error.unknown') }),
                });
            }

            this.loading = false;
            chatStore.totalChunks = this.totalChunks;
            this.$nextTick(() => {
                const el = this.$refs.chatMessages;
                if (el) el.scrollTop = el.scrollHeight;
            });
        },
        clearChat() {
            this.messages.length = 0;
        },
        startNewChat() {
            this.messages.length = 0;
            this.paperIds.length = 0;
            this.selectedPapers.length = 0;
            chatStore.totalChunks = 0;
            this.totalChunks = 0;
            this.question = '';
        },
        hideBadgeTooltip() {
            this.badgeTooltip.pinned = false;
            this.badgeTooltip.visible = false;
        },
        openPdfModal(source) {
            if (!source) return;
            const page = source.pages_referenced && source.pages_referenced.length ? source.pages_referenced[0] : 1;
            this.pdfModal = {
                show: true,
                paperId: source.paper_id,
                paperTitle: source.title || 'Unbekannt',
                authors: source.authors || '',
                page: page,
                url: '/api/papers/' + source.paper_id + '/pdf#page=' + page,
                highlightText: source.previews ? source.previews.map(p => p.text).join(' [...] ') : '',
            };
            this.hideBadgeTooltip();
            this._escHandler = (e) => { if (e.key === 'Escape') this.closePdfModal(); };
            document.addEventListener('keydown', this._escHandler);
        },
        closePdfModal() {
            this.pdfModal.show = false;
            if (this._escHandler) {
                document.removeEventListener('keydown', this._escHandler);
                this._escHandler = null;
            }
        },
        openInPaperDetail() {
            const id = this.pdfModal.paperId;
            const page = this.pdfModal.page;
            this.closePdfModal();
            this.$router.push('/paper/' + id + '?page=' + page);
        },
        formatMessage(content, sources) {
            if (!content) return '';
            // Convert **bold** first
            let html = content.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
            // Convert line breaks BEFORE inserting HTML badges (to avoid <br> inside title attrs)
            html = html.replace(/\n/g, '<br>');
            // Convert [Quelle X] to clickable badges with hover tooltip
            html = html.replace(/\[Quelle (\d+)\]/g, (match, num) => {
                const idx = parseInt(num) - 1;
                const src = sources && sources[idx];
                const paperId = src ? src.paper_id : '';
                const title = src ? (src.title || '').replace(/&/g,'&amp;').replace(/"/g, '&quot;').replace(/</g,'&lt;').substring(0, 80) : '';
                const authors = src && src.authors ? ' — ' + src.authors.replace(/&/g,'&amp;').replace(/"/g, '&quot;').replace(/</g,'&lt;').substring(0, 60) : '';
                const pages = src && src.pages_referenced && src.pages_referenced.length ? ', S. ' + src.pages_referenced.join(', ') : '';
                const tooltip = title + authors + pages;
                const pageParam = src && src.pages_referenced && src.pages_referenced.length ? '?page=' + src.pages_referenced[0] : '';
                const href = paperId ? `#/paper/${paperId}${pageParam}` : '#';
                return `<a href="${href}" class="source-badge inline-flex items-center justify-center bg-accent-soft text-accent-ink text-xs font-medium px-1.5 py-0 rounded-full cursor-pointer hover:bg-accent-soft transition-colors no-underline" title="${tooltip}" data-source-idx="${idx}">[Q${num}]</a>`;
            });
            return html;
        },
    },
};


// =============================================================================
// ThesisPage Component
// =============================================================================

const ThesisPage = {
    template: `
        <div class="p-6 max-w-5xl mx-auto">
            <!-- Header -->
            <div class="flex items-center gap-3 mb-6">
                <div class="w-10 h-10 rounded-lg bg-accent-soft text-accent flex items-center justify-center">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>
                </div>
                <div>
                    <h2 class="text-xl font-bold text-gray-900">{{ $t('thesis.heading') }}</h2>
                    <p class="text-sm text-gray-500">{{ $t('thesis.subheading') }}</p>
                </div>
            </div>

            <!-- Upload Phase -->
            <div v-if="phase === 'upload'" class="bg-white rounded-xl shadow-sm border border-gray-200 p-8">
                <div class="border-2 border-dashed rounded-xl p-12 text-center transition-colors"
                     :class="dragging ? 'border-accent bg-accent-soft' : 'border-gray-300 hover:border-gray-400'"
                     @dragover.prevent="dragging = true"
                     @dragleave.prevent="dragging = false"
                     @drop.prevent="handleDrop">
                    <div class="w-16 h-16 mx-auto mb-4 bg-gray-100 text-gray-400 rounded-full flex items-center justify-center">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-8 h-8" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                    </div>
                    <p class="text-base text-gray-600 mb-2 font-medium">{{ $t('thesis.dropHint') }}</p>
                    <p class="text-sm text-gray-400 mb-5">{{ $t('thesis.orChooseFile') }}</p>
                    <label class="inline-flex items-center gap-2 bg-accent text-white px-6 py-3 rounded-lg text-sm font-medium hover:bg-accent-ink cursor-pointer transition-colors">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                        {{ $t('thesis.choosePdf') }}
                        <input type="file" accept=".pdf" @change="handleFileInput" class="hidden" />
                    </label>
                </div>
            </div>

            <!-- Loading Phase -->
            <div v-if="phase === 'loading'" class="bg-white rounded-xl shadow-sm border border-gray-200 p-12 text-center">
                <div class="spinner mx-auto mb-4" style="width:40px;height:40px;border-width:3px;"></div>
                <p class="text-base font-medium text-gray-900 mb-1">{{ loadingMessage }}</p>
                <p class="text-sm text-gray-500">{{ filename }}</p>
                <div class="w-72 mx-auto mt-5 bg-gray-200 rounded-full h-2.5">
                    <div class="bg-accent h-2.5 rounded-full transition-all duration-500" :style="{ width: progress + '%' }"></div>
                </div>
            </div>

            <!-- Results Phase -->
            <div v-if="phase === 'result'" class="space-y-4">
                <!-- Summary Cards -->
                <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-5">
                    <div class="flex items-center justify-between mb-4">
                        <h3 class="text-base font-semibold text-gray-900">{{ filename }}</h3>
                        <span class="text-xs text-gray-400">{{ $t('attach.chapterPages', { count: result.total_pages }) }}</span>
                    </div>
                    <div class="grid grid-cols-2 sm:grid-cols-5 gap-3">
                        <div class="bg-gray-50 rounded-lg p-3 text-center">
                            <div class="text-2xl font-bold text-gray-800">{{ result.summary.total }}</div>
                            <div class="text-[11px] text-gray-500">{{ $t('thesis.sourcesTotal') }}</div>
                        </div>
                        <div class="bg-accent-soft rounded-lg p-3 text-center">
                            <div class="text-2xl font-bold text-accent">{{ result.summary.found_online }}</div>
                            <div class="text-[11px] text-gray-500">{{ $t('thesis.foundOnline') }}</div>
                        </div>
                        <div class="bg-accent-soft rounded-lg p-3 text-center">
                            <div class="text-2xl font-bold text-accent">{{ result.summary.not_found_online }}</div>
                            <div class="text-[11px] text-gray-500">{{ $t('thesis.notOnline') }}</div>
                        </div>
                        <div class="bg-accent-soft rounded-lg p-3 text-center">
                            <div class="text-2xl font-bold text-accent">{{ result.summary.used_in_text }}</div>
                            <div class="text-[11px] text-gray-500">{{ $t('thesis.citedInText') }}</div>
                        </div>
                        <div class="bg-accent-soft rounded-lg p-3 text-center">
                            <div class="text-2xl font-bold text-accent">{{ result.summary.not_used_in_text }}</div>
                            <div class="text-[11px] text-gray-500">{{ $t('thesis.notInText') }}</div>
                        </div>
                    </div>
                </div>

                <!-- Tab Bar -->
                <div class="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                    <div class="flex border-b border-gray-200">
                        <button @click="tab='all'"
                            :class="tab==='all' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700'"
                            class="px-5 py-3 text-sm font-medium border-b-2 transition-colors">
                            Alle Quellen ({{ result.summary.total }})
                        </button>
                        <button @click="tab='not_online'"
                            :class="tab==='not_online' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700'"
                            class="px-5 py-3 text-sm font-medium border-b-2 transition-colors">
                            Nicht online ({{ result.summary.not_found_online }})
                        </button>
                        <button @click="tab='not_cited'"
                            :class="tab==='not_cited' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700'"
                            class="px-5 py-3 text-sm font-medium border-b-2 transition-colors">
                            Nicht im Text ({{ result.summary.not_used_in_text }})
                        </button>
                    </div>

                    <!-- All References Table -->
                    <div v-show="tab==='all'" class="p-5">
                        <div class="overflow-x-auto">
                            <table class="w-full text-sm">
                                <thead>
                                    <tr class="text-left text-xs text-gray-500 uppercase tracking-wider border-b">
                                        <th class="pb-2 pr-3 w-8">#</th>
                                        <th class="pb-2 pr-3">{{ $t('common.title') }}</th>
                                        <th class="pb-2 pr-3">{{ $t('common.authors') }}</th>
                                        <th class="pb-2 pr-3 text-center w-14">{{ $t('common.year') }}</th>
                                        <th class="pb-2 pr-3 text-center w-16">{{ $t('thesis.colOnline') }}</th>
                                        <th class="pb-2 text-center w-16">{{ $t('thesis.colInText') }}</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    <tr v-for="ref in result.references" :key="ref.index" class="border-b border-gray-100 hover:bg-gray-50">
                                        <td class="py-2 pr-3 text-xs text-gray-400">{{ ref.index }}</td>
                                        <td class="py-2 pr-3 text-gray-900 max-w-xs">
                                            <span class="line-clamp-2">{{ ref.title || 'Unbekannt' }}</span>
                                            <a v-if="ref.doi" :href="'https://doi.org/' + ref.doi" target="_blank" class="text-xs text-accent hover:underline block mt-0.5">DOI: {{ ref.doi }}</a>
                                        </td>
                                        <td class="py-2 pr-3 text-gray-500 max-w-[140px] truncate text-xs">{{ ref.authors }}</td>
                                        <td class="py-2 pr-3 text-center text-gray-500 text-xs">{{ ref.year || '-' }}</td>
                                        <td class="py-2 pr-3 text-center">
                                            <span v-if="ref.online_found" class="text-accent" :title="$t('thesis.foundOnline')">
                                                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                                            </span>
                                            <span v-else class="text-accent" :title="$t('thesis.notFoundOnline')">
                                                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                                            </span>
                                        </td>
                                        <td class="py-2 text-center">
                                            <span v-if="ref.used_in_text" class="text-accent" :title="$t('thesis.citedInText')">
                                                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                                            </span>
                                            <span v-else class="text-accent" :title="$t('thesis.notFoundInText')">
                                                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                                            </span>
                                        </td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <!-- Not Found Online -->
                    <div v-show="tab==='not_online'" class="p-5">
                        <div v-if="result.not_found_online.length === 0" class="text-center py-10 text-gray-400 text-sm">
                            {{ $t('thesis.allFoundOnline') }}
                        </div>
                        <div v-else class="space-y-2">
                            <p class="text-xs text-gray-500 mb-3">{{ $t('thesis.unverifiedHint') }}</p>
                            <div v-for="ref in result.not_found_online" :key="ref.index"
                                 class="flex items-start gap-3 bg-accent-soft rounded-lg p-3 border border-accent-soft">
                                <span class="text-xs text-accent font-mono mt-0.5 w-6 flex-shrink-0">[{{ ref.index }}]</span>
                                <div class="flex-1 min-w-0">
                                    <p class="text-sm text-gray-900 font-medium">{{ ref.title || $t('papers.untitled') }}</p>
                                    <p class="text-xs text-gray-500 mt-0.5">{{ ref.authors }}<span v-if="ref.year"> ({{ ref.year }})</span></p>
                                    <p v-if="ref.journal" class="text-xs text-gray-400">{{ ref.journal }}</p>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Not Used in Text -->
                    <div v-show="tab==='not_cited'" class="p-5">
                        <div v-if="result.not_used_in_text.length === 0" class="text-center py-10 text-gray-400 text-sm">
                            {{ $t('thesis.allCitedInText') }}
                        </div>
                        <div v-else class="space-y-2">
                            <p class="text-xs text-gray-500 mb-3">{{ $t('thesis.uncitedHint') }}</p>
                            <div v-for="ref in result.not_used_in_text" :key="ref.index"
                                 class="flex items-start gap-3 bg-accent-soft rounded-lg p-3 border border-accent-soft">
                                <span class="text-xs text-accent font-mono mt-0.5 w-6 flex-shrink-0">[{{ ref.index }}]</span>
                                <div class="flex-1 min-w-0">
                                    <p class="text-sm text-gray-900 font-medium">{{ ref.title || $t('papers.untitled') }}</p>
                                    <p class="text-xs text-gray-500 mt-0.5">{{ ref.authors }}<span v-if="ref.year"> ({{ ref.year }})</span></p>
                                    <p v-if="ref.journal" class="text-xs text-gray-400">{{ ref.journal }}</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Footer Actions -->
                <div class="flex items-center justify-between">
                    <button @click="phase = 'upload'; result = null" class="text-sm text-gray-500 hover:text-gray-700 flex items-center gap-1">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
                        {{ $t('thesis.newAnalysis') }}
                    </button>
                </div>
            </div>
        </div>
    `,
    data() {
        return {
            phase: 'upload',
            dragging: false,
            filename: '',
            loadingMessage: '',
            progress: 0,
            result: null,
            tab: 'all',
        };
    },
    methods: {
        handleDrop(e) {
            this.dragging = false;
            const file = e.dataTransfer.files[0];
            if (file && file.type === 'application/pdf') {
                this.startAnalysis(file);
            }
        },
        handleFileInput(e) {
            const file = e.target.files[0];
            if (file) this.startAnalysis(file);
        },
        async startAnalysis(file) {
            this.filename = file.name;
            this.phase = 'loading';
            this.progress = 10;
            this.loadingMessage = t('thesis.step.uploading');

            const steps = [
                { pct: 20, msg: t('thesis.step.extractingText') },
                { pct: 40, msg: t('thesis.step.findingBibliography') },
                { pct: 55, msg: t('thesis.step.extractingRefs') },
                { pct: 70, msg: t('thesis.step.checkingCitations') },
                { pct: 85, msg: t('thesis.step.checkingOnline') },
            ];
            let stepIdx = 0;
            const iv = setInterval(() => {
                if (stepIdx < steps.length) {
                    this.progress = steps[stepIdx].pct;
                    this.loadingMessage = steps[stepIdx].msg;
                    stepIdx++;
                }
            }, 3000);

            try {
                const formData = new FormData();
                formData.append('file', file);
                const resp = await fetch('/api/analysis/thesis', { method: 'POST', body: formData });
                clearInterval(iv);
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(translateDetail(err.detail, resp.status));
                }
                this.result = await resp.json();
                this.progress = 100;
                this.loadingMessage = 'Fertig!';
                setTimeout(() => { this.phase = 'result'; this.tab = 'all'; }, 400);
            } catch (err) {
                clearInterval(iv);
                alert(t('thesis.failed', { message: err.message }));
                this.phase = 'upload';
            }
        },
    },
};

// =============================================================================
// AnalysePage Component
// =============================================================================

// ── Wissensnetz: Farben der D3-Ebene ─────────────────────────────────────────
// SVG-Attribute nehmen keine CSS-Klasse, also liest der Graph die Tokens zur
// Zeichenzeit aus dem Dokument. Feste Hexwerte waren hier ein Dark-Mode-Loch:
// der Trennring der Fremdknoten ist die *Seitenfarbe* (hell #fafaf7 — im Dark
// Mode ein weisser Leuchtring um jeden Knoten), und die Auswahl zeichnete
// Schwarz auf Schwarz. Der Cache haelt den Wert pro Theme/Palette fest, damit
// nicht jeder Knoten ein getComputedStyle ausloest.
let _lbTokenCache = { key: null, vals: {} };
function lbToken(name, fallback) {
    const root = document.documentElement;
    const key = (root.getAttribute('data-theme') || '') + '|' + (root.getAttribute('data-palette') || '');
    if (_lbTokenCache.key !== key) _lbTokenCache = { key, vals: {} };
    if (!(name in _lbTokenCache.vals)) {
        const v = getComputedStyle(root).getPropertyValue(name).trim();
        _lbTokenCache.vals[name] = v || fallback;
    }
    return _lbTokenCache.vals[name];
}
// Knotenring: Auswahl > eigenes Paper > Brueckenknoten > Trennring gegen den Grund.
function netNodeStroke(d, bridgeNodes, highlighted) {
    if (highlighted) return lbToken('--lb-ink', '#1a1a1a');
    if (d.type === 'own') return lbToken('--lb-accent-ink', '#7a2808');
    if (bridgeNodes.has(d.id)) return lbToken('--lb-accent', '#c2410c');
    return lbToken('--lb-bg', '#fafaf7');
}
// Kantenfarbe: Auswahl > Bruecke > aus dem PDF gelesene Referenz > normale Kante.
function netEdgeStroke(i, bridgeEdgeSet, pdfRefEdgeSet, highlighted) {
    if (highlighted) return lbToken('--lb-ink', '#1a1a1a');
    if (bridgeEdgeSet.has(i)) return lbToken('--lb-accent', '#c2410c');
    if (pdfRefEdgeSet.has(i)) return lbToken('--lb-mute-2', '#a8a49c');
    return lbToken('--lb-hairline', '#e7e2d6');
}

const AnalysePage = {
    template: `
        <div class="lb-view lb-analyse">
            <!-- Bulk Extract Progress Modal -->
            <div v-if="bulkExtracting" class="lb-modal-overlay" style="align-items:center;justify-content:center">
                <div style="background:var(--lb-bg-elev);border:1px solid var(--lb-hairline);border-radius:12px;box-shadow:var(--lb-shadow-lg);padding:24px;max-width:640px;width:92vw;max-height:80vh;display:flex;flex-direction:column">
                    <div class="lb-section-label" style="padding:0;margin-bottom:6px">{{ $t('analysis.pipeline') }}</div>
                    <h3 style="font-family:var(--lb-font-serif);font-size:22px;font-weight:500;margin:0 0 16px;color:var(--lb-ink)">{{ $t('analysis.bulkRefExtraction') }}</h3>
                    <div style="margin-bottom:14px">
                        <div style="width:100%;background:var(--lb-bg-soft);border-radius:99px;height:6px;margin-bottom:8px;overflow:hidden">
                            <div style="background:var(--lb-accent);height:6px;border-radius:99px;transition:width .3s ease" :style="{ width: bulkProgress.percent + '%' }"></div>
                        </div>
                        <p style="font-size:13px;color:var(--lb-ink-2);margin:0">{{ bulkProgress.message || $t('common.starting') }}</p>
                        <p v-if="bulkProgress.current_paper && bulkProgress.total_papers" class="lb-mono" style="font-size:11px;color:var(--lb-mute);margin:4px 0 0">
                            {{ $t('analysis.itemProgress', { current: bulkProgress.current_paper, total: bulkProgress.total_papers }) }}
                        </p>
                    </div>
                    <div v-if="bulkPaperResults.length" style="flex:1;overflow-y:auto;border-top:1px solid var(--lb-hairline);padding-top:10px;display:flex;flex-direction:column;gap:2px">
                        <div v-for="r in bulkPaperResults" :key="r.paper_id" style="display:flex;align-items:center;gap:8px;font-size:12px;padding:3px 0">
                            <span v-if="r.status === 'ok'" style="color:var(--lb-accent);width:14px">&#10003;</span>
                            <span v-else style="color:var(--lb-mute);width:14px">&#x2013;</span>
                            <span class="lb-truncate" style="flex:1;color:var(--lb-ink-2)">{{ r.title }}</span>
                            <span v-if="r.status === 'ok'" class="lb-mono" style="color:var(--lb-mute);font-size:11px">{{ $t('analysis.refsRatio', { extracted: r.total_extracted, inLibrary: r.in_library }) }}</span>
                            <span v-else class="lb-mono" style="color:var(--lb-mute);font-size:11px">{{ r.status }}</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Page Head -->
            <header class="lb-page-head">
                <div class="lb-page-head-l">
                    <div class="lb-eyebrow">{{ $t('nav.analysis') }}</div>
                    <h1 class="lb-page-title">{{ $t('analysis.heading') }}</h1>
                    <div class="lb-page-meta">
                        <span v-if="stats">{{ $t('analysis.ownItemsCount', { count: stats.own_papers }) }}</span>
                        <span v-if="stats" class="lb-page-meta-sep">&middot;</span>
                        <span v-if="stats">{{ $t('analysis.referencesCount', { count: stats.total_references }) }}</span>
                        <span v-if="!stats">{{ $t('analysis.subheading') }}</span>
                    </div>
                </div>
                <div class="lb-page-head-r">
                    <div class="lb-segmented">
                        <button class="lb-seg" :class="{'is-on': mode==='categories'}" @click="setMode('categories')">{{ $t('sidebar.categories') }}</button>
                        <button class="lb-seg" :class="{'is-on': mode==='years'}" @click="setMode('years')">{{ $t('analysis.modeYears') }}</button>
                        <button class="lb-seg" :class="{'is-on': mode==='authors'}" @click="setMode('authors')">{{ $t('common.authors') }}</button>
                    </div>
                </div>
            </header>

            <!-- Toolbar -->
            <div class="lb-toolbar">
                <div class="lb-toolbar-l">
                    <div class="lb-sortgroup">
                        <span class="lb-sortgroup-l">{{ $t('analysis.stat.depth') }}</span>
                        <div style="display:flex;gap:2px;padding:3px 4px">
                            <button v-for="d in [1,2,3,4,5]" :key="d" @click="depth = d"
                                class="lb-seg" :class="{'is-on': depth === d}"
                                style="min-width:24px;justify-content:center;padding:4px 8px">{{ d }}</button>
                        </div>
                        <span class="lb-mono" style="font-size:10.5px;color:var(--lb-mute);padding-right:8px">{{ depthLabel }}</span>
                    </div>
                </div>
                <div class="lb-toolbar-r">
                    <button @click="bulkExtractRefs" :disabled="bulkExtracting" class="lb-btn lb-btn-ghost"
                        :title="$t('analysis.bulkRefTitle')">
                        <span v-if="bulkExtracting" class="spinner-sm"></span>
                        <svg v-else xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
                        {{ bulkExtracting ? $t('refs.extracting') : $t('analysis.pdfReferences') }}
                    </button>
                    <button @click="runAnalysis" :disabled="loading" class="lb-btn lb-btn-primary">
                        <span v-if="loading" class="spinner-sm"></span>
                        <span v-else v-html="icons.refresh"></span>
                        {{ loading ? $t('analysis.running') : $t('analysis.start') }}
                    </button>
                </div>
            </div>

            <!-- Error -->
            <div v-if="error" style="margin-bottom:18px;background:var(--lb-accent-soft);border:1px solid var(--lb-accent);color:var(--lb-accent-ink);border-radius:8px;padding:12px 14px;font-size:13px">
                {{ error }}
            </div>

            <!-- Stats -->
            <div v-if="stats" style="display:grid;grid-template-columns:repeat(7,1fr);gap:0;margin-bottom:24px;border-top:1px solid var(--lb-hairline);border-bottom:1px solid var(--lb-hairline)">
                <div v-for="(s, i) in statBlocks" :key="i" style="padding:18px 16px;border-right:1px solid var(--lb-hairline);text-align:left" :style="{borderRight: i===6 ? '0' : null}">
                    <div style="font-family:var(--lb-font-serif);font-style:italic;font-size:28px;line-height:1;color:var(--lb-ink);margin-bottom:6px">{{ s.value }}</div>
                    <div class="lb-section-label" style="padding:0">{{ s.label }}</div>
                </div>
            </div>

            <!-- Tabs + Content -->
            <div v-if="networkData">
                <div style="display:flex;gap:24px;border-bottom:1px solid var(--lb-hairline);margin-bottom:20px;overflow-x:auto">
                    <button v-for="t in tabDefs" :key="t.id" @click="activeTab=t.id"
                        :style="{color: activeTab===t.id ? 'var(--lb-ink)' : 'var(--lb-mute)', borderBottomColor: activeTab===t.id ? 'var(--lb-accent)' : 'transparent'}"
                        style="appearance:none;background:transparent;border:0;border-bottom:2px solid transparent;padding:10px 2px;font-family:var(--lb-font-mono);font-size:11px;letter-spacing:0.14em;text-transform:uppercase;cursor:pointer;display:inline-flex;align-items:center;gap:8px;white-space:nowrap;transition:color .12s ease,border-color .12s ease">
                        {{ t.label }}
                        <span v-if="t.count != null" class="lb-mono" style="font-size:10px;color:var(--lb-mute-2);letter-spacing:0">{{ t.count }}</span>
                    </button>
                </div>

                <!-- Network Graph -->
                <div v-show="activeTab==='network'">
                    <!-- Graph Toolbar -->
                    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap">
                        <!-- Search -->
                        <div style="position:relative;width:260px">
                            <input v-model="graphSearchQuery" @focus="graphSearchOpen = true" @input="graphSearchOpen = true" @blur="setTimeout(() => graphSearchOpen = false, 200)"
                                :placeholder="$t('analysis.searchNavigate')" class="lb-input" style="padding-left:32px">
                            <svg style="position:absolute;left:10px;top:50%;transform:translateY(-50%);color:var(--lb-mute)" width="14" height="14" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                            <div v-if="graphSearchOpen && graphSearchResults.length" style="position:absolute;top:100%;left:0;right:0;margin-top:4px;background:var(--lb-bg-elev);border:1px solid var(--lb-hairline);border-radius:8px;box-shadow:var(--lb-shadow-lg);max-height:240px;overflow-y:auto;z-index:30">
                                <button v-for="n in graphSearchResults" :key="n.id" @mousedown.prevent="navigateToNode(n)"
                                    style="width:100%;text-align:left;padding:7px 12px;font-size:12px;border:0;background:transparent;border-bottom:1px solid var(--lb-hairline);display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--lb-ink-2)">
                                    <span style="width:8px;height:8px;border-radius:50%;flex-shrink:0" :style="{background: graphNodeColor(n.type)}"></span>
                                    <span class="lb-truncate" style="flex:1">{{ n.title || n.id }}</span>
                                    <span v-if="n.year" class="lb-mono" style="font-size:11px;color:var(--lb-mute);flex-shrink:0">{{ n.year }}</span>
                                </button>
                            </div>
                        </div>
                        <span class="lb-field-label" style="margin:0 4px 0 8px">{{ $t('filters.category') }}</span>
                        <select v-model="graphPanelCategory" @change="applyCategoryFilter" class="lb-select" style="border:1px solid var(--lb-hairline);border-radius:7px;background:var(--lb-bg-elev);min-width:160px">
                            <option value="">{{ $t('filters.all') }}</option>
                            <option v-for="c in graphPanelCategoryList" :key="c.id" :value="c.id">{{ c.name }}</option>
                        </select>
                        <div style="position:relative;margin-left:auto">
                            <button @click="graphPanelOpen = !graphPanelOpen" class="lb-btn">
                                <span style="width:7px;height:7px;border-radius:2px;background:var(--lb-accent)"></span>
                                Eigene Paper ({{ graphPanelFilteredPapers.length }})
                                <svg :style="{transform: graphPanelOpen ? 'rotate(180deg)' : 'none', transition:'transform .12s ease'}" width="11" height="11" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
                            </button>
                            <div v-if="graphPanelOpen" style="position:absolute;top:100%;right:0;margin-top:4px;background:var(--lb-bg-elev);border:1px solid var(--lb-hairline);border-radius:8px;box-shadow:var(--lb-shadow-lg);overflow:hidden;z-index:30;width:320px">
                                <div style="padding:8px;border-bottom:1px solid var(--lb-hairline)">
                                    <input v-model="graphPanelSearch" :placeholder="$t('analysis.filterPlaceholder')" class="lb-input" style="padding:6px 10px;font-size:12px">
                                </div>
                                <div style="max-height:280px;overflow-y:auto">
                                    <button v-for="p in graphPanelFilteredPapers" :key="p.id" @click="navigateToNode(p)"
                                        :style="{background: highlightedNodeId === p.id ? 'var(--lb-accent-soft)' : 'transparent'}"
                                        style="width:100%;text-align:left;padding:7px 12px;font-size:12px;border:0;border-bottom:1px solid var(--lb-hairline);display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--lb-ink-2)">
                                        <span style="width:7px;height:7px;border-radius:50%;flex-shrink:0;background:var(--lb-ink)"></span>
                                        <span class="lb-truncate" style="flex:1">{{ p.title || p.id }}</span>
                                        <span v-if="p.year" class="lb-mono" style="font-size:11px;color:var(--lb-mute);flex-shrink:0">{{ p.year }}</span>
                                    </button>
                                    <div v-if="!graphPanelFilteredPapers.length" style="padding:18px;text-align:center;font-size:12px;color:var(--lb-mute)">{{ $t('analysis.noItemsFound') }}</div>
                                </div>
                            </div>
                        </div>
                    </div>
                    <!-- Net Shell: Canvas + Aside -->
                    <div class="lb-net-shell">
                        <div class="lb-net-canvas">
                            <div ref="graphContainer" style="position:absolute;inset:0" @click.self="clearHighlight"></div>

                            <!-- Hover Card -->
                            <div v-if="hoverNode" class="lb-hover-card">
                                <div class="lb-hover-year">{{ hoverNode.year || '—' }}</div>
                                <div class="lb-hover-title">{{ hoverNode.title || hoverNode.id }}</div>
                                <div class="lb-hover-meta" v-if="hoverNode.authors">{{ hoverNode.authors }}</div>
                                <div class="lb-hover-foot">
                                    <span v-if="hoverNode.cited_by_count != null">{{ hoverNode.cited_by_count.toLocaleString($locale()) }} {{ $t('papers.citAbbr') }}</span>
                                    <span v-if="hoverNode.cited_by_count != null" class="lb-meta-dot">·</span>
                                    <span>{{ nodeTypeLabel(hoverNode.type) }}</span>
                                </div>
                            </div>

                            <!-- Selected Node Detail -->
                            <div v-if="selectedNode" style="position:absolute;top:18px;right:18px;background:var(--lb-bg-elev);border:1px solid var(--lb-hairline);border-radius:10px;box-shadow:var(--lb-shadow-lg);padding:18px;width:320px;z-index:10;max-height:calc(100% - 36px);overflow-y:auto">
                                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px;gap:10px">
                                    <div style="flex:1;min-width:0">
                                        <div class="lb-mono" style="font-size:10.5px;color:var(--lb-accent);letter-spacing:0.04em;margin-bottom:4px">{{ selectedNode.year || '—' }} · {{ nodeTypeLabel(selectedNode.type) }}</div>
                                        <h4 style="font-family:var(--lb-font-serif);font-size:15px;line-height:1.3;font-weight:500;color:var(--lb-ink);margin:0">{{ selectedNode.title }}</h4>
                                    </div>
                                    <button @click="selectedNode=null; clearHighlight(); nodeAbstract=null" class="lb-modal-close" style="width:24px;height:24px"><span v-html="icons.x"></span></button>
                                </div>
                                <p v-if="selectedNode.authors" style="font-size:12px;color:var(--lb-ink-3);font-style:italic;margin:0 0 8px">{{ selectedNode.authors }}</p>
                                <div class="lb-mono" style="display:flex;gap:10px;font-size:11px;color:var(--lb-mute);margin-bottom:8px">
                                    <span v-if="selectedNode.cited_by_count">{{ selectedNode.cited_by_count.toLocaleString($locale()) }} {{ $t('papers.citAbbr') }}</span>
                                    <span v-if="selectedNode.referenced_by_count">{{ $t('analysis.refTimes', { count: selectedNode.referenced_by_count }) }}</span>
                                </div>
                                <div v-if="selectedNode.doi" style="margin-bottom:10px">
                                    <a :href="'https://doi.org/' + selectedNode.doi" target="_blank" class="lb-mono" style="font-size:11px;color:var(--lb-accent);text-decoration:none">DOI: {{ selectedNode.doi }}</a>
                                </div>
                                <div v-if="selectedNode.paper_id" style="margin-bottom:10px">
                                    <router-link :to="'/paper/' + selectedNode.paper_id" class="lb-btn lb-btn-primary" style="padding:5px 10px;font-size:11.5px">{{ $t('analysis.openDetails') }} →</router-link>
                                </div>
                                <div style="border-top:1px solid var(--lb-hairline);padding-top:10px;margin-top:6px">
                                    <div v-if="nodeAbstractLoading" style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--lb-mute)">
                                        <span class="spinner-sm"></span> {{ $t('analysis.abstractLoading') }}
                                    </div>
                                    <div v-else-if="nodeAbstract" style="font-size:12.5px;line-height:1.55;color:var(--lb-ink-2);max-height:160px;overflow-y:auto">
                                        <span class="lb-section-label" style="padding:0;display:inline">Abstract</span><br>{{ nodeAbstract }}
                                    </div>
                                    <div v-else-if="nodeAbstractError" style="font-size:11.5px;color:var(--lb-mute);font-style:italic">{{ nodeAbstractError }}</div>
                                </div>
                                <button @click="askAboutSelectedNode()" class="lb-btn lb-btn-w" style="margin-top:10px;justify-content:center">
                                    <svg width="13" height="13" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"/></svg>
                                    {{ $t('analysis.askInChat') }}
                                </button>
                            </div>
                        </div>

                        <aside class="lb-net-aside">
                            <div class="lb-aside-block">
                                <h4 class="lb-aside-h">{{ $t('analysis.legend') }}</h4>
                                <ul class="lb-legend">
                                    <li><span class="lb-legend-mark lb-legend-paper"></span><span>{{ $t('analysis.legendOwnItem') }}</span></li>
                                    <li><span class="lb-legend-mark" style="background:var(--lb-accent);width:12px;height:12px;border-radius:50%"></span><span>{{ $t('analysis.legendShared') }}</span></li>
                                    <li><span class="lb-legend-mark lb-legend-cat"></span><span>{{ $t('analysis.legendSize') }}</span></li>
                                    <li><span class="lb-legend-mark lb-legend-edge"></span><span>{{ $t('analysis.legendCites') }}</span></li>
                                    <li><span class="lb-legend-mark lb-legend-edge-co"></span><span>{{ $t('analysis.legendPdfRef') }}</span></li>
                                </ul>
                            </div>

                            <div class="lb-aside-block">
                                <h4 class="lb-aside-h">{{ $t('analysis.filter') }}</h4>
                                <div style="display:flex;flex-direction:column;gap:14px;margin-top:6px">
                                    <div>
                                        <div style="display:flex;justify-content:space-between;font-size:11.5px;color:var(--lb-ink-3);margin-bottom:4px"><span>{{ $t('analysis.spacing') }}</span><span class="lb-mono" style="color:var(--lb-ink-2)">{{ graphDistance }}</span></div>
                                        <input type="range" v-model.number="graphDistance" min="5" max="120" step="1" style="width:100%;accent-color:var(--lb-accent)">
                                    </div>
                                    <div>
                                        <div style="display:flex;justify-content:space-between;font-size:11.5px;color:var(--lb-ink-3);margin-bottom:4px"><span :title="$t('analysis.refDepthTitle')">{{ $t('analysis.refDepth') }}</span><span class="lb-mono" style="color:var(--lb-accent)">{{ minRefs }}</span></div>
                                        <input type="range" v-model.number="minRefs" min="1" max="3" step="1" style="width:100%;accent-color:var(--lb-accent)">
                                    </div>
                                    <div>
                                        <div style="display:flex;justify-content:space-between;font-size:11.5px;color:var(--lb-ink-3);margin-bottom:4px"><span :title="$t('analysis.minCitationsTitle')">{{ $t('analysis.minCitations') }}</span><span class="lb-mono" style="color:var(--lb-ink-2)">{{ minCitations || $t('analysis.off') }}</span></div>
                                        <input type="range" v-model.number="minCitations" min="0" max="500" step="10" style="width:100%;accent-color:var(--lb-accent)">
                                    </div>
                                    <div>
                                        <div style="display:flex;justify-content:space-between;font-size:11.5px;color:var(--lb-ink-3);margin-bottom:4px"><span :title="$t('analysis.topNTitle')">{{ $t('analysis.topNRefs') }}</span><span class="lb-mono" style="color:var(--lb-ink-2)">{{ topNRefs || 'Alle' }}</span></div>
                                        <input type="range" v-model.number="topNRefs" min="0" max="30" step="1" style="width:100%;accent-color:var(--lb-accent)">
                                    </div>
                                </div>
                            </div>

                            <div class="lb-aside-block" style="margin-top:auto">
                                <p class="lb-mono" style="font-size:10px;color:var(--lb-mute);letter-spacing:0.06em;margin:0">{{ $t('analysis.panZoomHint') }}</p>
                            </div>
                        </aside>
                    </div>
                </div>

                <!-- Paper Chat (below network) -->
                <div v-show="activeTab==='network' && networkData" style="border-top:1px solid var(--lb-hairline);margin-top:24px;padding-top:20px">
                    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
                        <div>
                            <div class="lb-section-label" style="padding:0;margin-bottom:4px">{{ $t('analysis.itemChat') }}</div>
                            <div v-if="chatContextPaper" style="font-family:var(--lb-font-serif);font-style:italic;font-size:15px;color:var(--lb-ink-2)" class="lb-truncate">{{ chatContextPaper.title }}</div>
                        </div>
                        <div style="display:flex;align-items:center;gap:10px">
                            <button v-if="chatContextPaper" @click="chatContextPaper=null; clearChat()" class="lb-btn-text" :title="$t('analysis.clearItemContext')">{{ $t('analysis.switchItem') }}</button>
                            <button @click="chatOpen = !chatOpen" class="lb-btn-text">{{ chatOpen ? 'Einklappen' : 'Aufklappen' }}</button>
                        </div>
                    </div>

                    <div v-show="chatOpen">
                        <div v-if="!chatContextPaper" class="lb-empty" style="padding:32px 24px;border:1px solid var(--lb-hairline);border-radius:10px;background:var(--lb-bg-elev)">
                            <div class="lb-empty-mark">"</div>
                            <p style="font-size:13.5px;color:var(--lb-ink-3);margin:0 0 4px">{{ $t('analysis.chatHintBefore') }} <span style="color:var(--lb-accent);font-weight:500">{{ $t('analysis.chatHintButton') }}</span>.</p>
                            <p style="font-size:12px;color:var(--lb-mute);margin:0">{{ $t('analysis.chatHintBoth') }}</p>
                        </div>

                        <div v-else>
                            <div ref="chatMessages" style="background:var(--lb-bg-elev);border:1px solid var(--lb-hairline);border-radius:10px;margin-bottom:10px;overflow-y:auto;max-height:400px;min-height:140px">
                                <div v-if="chatMessages.length === 0 && !chatStreaming" style="display:flex;align-items:center;justify-content:center;height:140px;color:var(--lb-mute);font-size:12.5px;font-style:italic">
                                    {{ $t('analysis.askAbout', { title: chatContextPaper.title }) }}
                                </div>
                                <div v-for="(msg, i) in chatMessages" :key="i" style="padding:14px 18px;border-bottom:1px solid var(--lb-hairline)">
                                    <div class="lb-section-label" style="padding:0;margin-bottom:6px" :style="{color: msg.role === 'user' ? 'var(--lb-accent)' : 'var(--lb-mute)'}">{{ msg.role === 'user' ? 'Du' : 'Assistent' }}</div>
                                    <div :style="{fontFamily: msg.role === 'user' ? 'var(--lb-font-sans)' : 'var(--lb-font-serif)', fontSize: msg.role === 'user' ? '13.5px' : '15px'}" class="lb-md" style="line-height:1.6;color:var(--lb-ink-2)" v-html="renderMarkdown(msg.content)"></div>
                                </div>
                                <div v-if="chatStreaming" style="padding:14px 18px">
                                    <div class="lb-section-label" style="padding:0;margin-bottom:6px;color:var(--lb-mute)">{{ $t('analysis.assistant') }}</div>
                                    <div style="font-family:var(--lb-font-serif);font-size:15px;line-height:1.6;color:var(--lb-ink-2)" class="lb-md" v-html="renderMarkdown(chatStreamContent || $t('analysis.thinking'))"></div>
                                </div>
                            </div>

                            <div style="display:flex;gap:8px">
                                <input v-model="chatInput" @keydown.enter="sendChatMessage" :disabled="chatStreaming"
                                    :placeholder="$t('analysis.askAboutItem')" class="lb-input" style="flex:1">
                                <button @click="sendChatMessage" :disabled="chatStreaming || !chatInput.trim()" class="lb-btn lb-btn-primary">
                                    <span v-if="chatStreaming" class="spinner-sm"></span>
                                    <svg v-else width="14" height="14" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6"/></svg>
                                    {{ $t('chat.send') }}
                                </button>
                                <button v-if="chatMessages.length" @click="clearChat" class="lb-icon-btn" :title="$t('chat.clear')">
                                    <svg width="14" height="14" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                                </button>
                            </div>

                            <div v-if="chatMessages.length === 0 && !chatStreaming" style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px">
                                <button v-for="q in quickQuestions" :key="q" @click="chatInput = q; $nextTick(() => sendChatMessage())" class="lb-chip">{{ q }}</button>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Own Paper Stats -->
                <div v-show="activeTab==='ownstats'">
                    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px">
                        <p style="font-size:13px;color:var(--lb-ink-3);margin:0">{{ $t('analysis.ownTableHint') }}</p>
                        <button @click="updateCitations" :disabled="updatingCitations" class="lb-btn lb-btn-ghost">
                            <span v-if="updatingCitations" class="spinner-sm"></span>
                            <span v-else v-html="icons.refresh"></span>
                            {{ $t('analysis.refreshCitations') }}
                        </button>
                    </div>
                    <div v-if="!networkData.own_paper_stats || networkData.own_paper_stats.length === 0" class="lb-empty">
                        <div class="lb-empty-mark">∅</div>
                        <p class="lb-empty-text">{{ $t('analysis.noData') }}</p>
                    </div>
                    <div v-else style="overflow-x:auto">
                        <table class="lb-table">
                            <thead><tr>
                                <th class="is-sortable" @click="sortOwn('title')">{{ $t('common.title') }} ↕</th>
                                <th>{{ $t('common.authors') }}</th>
                                <th class="is-sortable" style="text-align:center" @click="sortOwn('year')">{{ $t('common.year') }} ↕</th>
                                <th class="is-sortable" style="text-align:right" @click="sortOwn('cited_by_count')">{{ $t('common.citations') }} ↕</th>
                                <th class="is-sortable" style="text-align:right" @click="sortOwn('reference_count')">{{ $t('analysis.colOaRefs') }} ↕</th>
                                <th class="is-sortable" style="text-align:right" @click="sortOwn('pdf_reference_count')">{{ $t('analysis.stat.pdfRefs') }} ↕</th>
                                <th style="text-align:center">OA</th>
                                <th>DOI</th>
                            </tr></thead>
                            <tbody>
                                <tr v-for="p in sortedOwnPapers" :key="p.paper_id">
                                    <td class="lb-td-title"><router-link :to="'/paper/' + p.paper_id">{{ p.title }}</router-link></td>
                                    <td class="lb-td-authors lb-truncate">{{ p.authors }}</td>
                                    <td class="lb-td-num" style="text-align:center">{{ p.year || '—' }}</td>
                                    <td class="lb-td-num"><span class="lb-num-pill is-accent">{{ (p.cited_by_count || 0).toLocaleString() }}</span></td>
                                    <td class="lb-td-num">{{ p.reference_count || 0 }}</td>
                                    <td class="lb-td-num"><span v-if="p.pdf_reference_count" class="lb-num-pill">{{ p.pdf_reference_count }}</span><span v-else style="color:var(--lb-mute-2)">—</span></td>
                                    <td class="lb-td-c"><span v-if="p.in_openalex" style="color:var(--lb-accent)" v-html="icons.check"></span><span v-else style="color:var(--lb-mute-2)">—</span></td>
                                    <td><a v-if="p.doi" :href="'https://doi.org/' + p.doi" target="_blank" class="lb-mono" style="font-size:11px;color:var(--lb-accent);text-decoration:none">{{ p.doi }}</a></td>
                                </tr>
                            </tbody>
                        </table>
                        <div style="margin-top:18px;padding-top:16px;border-top:1px solid var(--lb-hairline);display:flex;gap:32px;font-size:13px;color:var(--lb-ink-3)">
                            <div>{{ $t('analysis.totalCitations') }}: <span class="lb-mono" style="color:var(--lb-ink);font-size:14px">{{ totalOwnCitations.toLocaleString() }}</span></div>
                            <div>{{ $t('analysis.average') }}: <span class="lb-mono" style="color:var(--lb-ink);font-size:14px">{{ avgOwnCitations }}</span></div>
                            <div>{{ $t('analysis.max') }}: <span class="lb-mono" style="color:var(--lb-ink);font-size:14px">{{ maxOwnCitations.toLocaleString() }}</span></div>
                        </div>
                    </div>
                </div>

                <!-- Referenced Papers Table -->
                <div v-show="activeTab==='references'">
                    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px">
                        <p style="font-size:13px;color:var(--lb-ink-3);margin:0">{{ $t('analysis.refTableHint') }}</p>
                        <div style="display:flex;align-items:center;gap:8px">
                            <span class="lb-field-label">{{ $t('analysis.filter') }}</span>
                            <select v-model="refFilter" class="lb-select" style="border:1px solid var(--lb-hairline);border-radius:7px;background:var(--lb-bg-elev)">
                                <option value="all">{{ $t('filters.all') }}</option>
                                <option value="not_in_lib">{{ $t('analysis.notInLibrary') }}</option>
                                <option value="in_lib">{{ $t('refs.inLibrary') }}</option>
                            </select>
                        </div>
                    </div>
                    <div v-if="!networkData.referenced_papers || networkData.referenced_papers.length === 0" class="lb-empty">
                        <div class="lb-empty-mark">∅</div>
                        <p class="lb-empty-text">{{ $t('analysis.noReferencedItems') }}</p>
                    </div>
                    <div v-else style="overflow-x:auto">
                        <table class="lb-table">
                            <thead><tr>
                                <th style="width:32px">#</th>
                                <th class="is-sortable" @click="sortRef('title')">{{ $t('common.title') }} ↕</th>
                                <th>{{ $t('common.authors') }}</th>
                                <th class="is-sortable" style="text-align:center" @click="sortRef('year')">{{ $t('common.year') }} ↕</th>
                                <th class="is-sortable" style="text-align:right" @click="sortRef('cited_by_count')">{{ $t('common.citations') }} ↕</th>
                                <th class="is-sortable" style="text-align:right" @click="sortRef('referenced_by_own')">{{ $t('analysis.colByOwn') }} ↕</th>
                                <th style="text-align:center">{{ $t('analysis.colInLibrary') }}</th>
                                <th style="text-align:center">{{ $t('chat.source') }}</th>
                                <th>DOI</th>
                            </tr></thead>
                            <tbody>
                                <tr v-for="(ref, idx) in sortedReferencedPapers" :key="ref.openalex_id || idx">
                                    <td class="lb-td-num" style="text-align:left;color:var(--lb-mute-2)">{{ idx + 1 }}</td>
                                    <td class="lb-td-title">{{ ref.title }}</td>
                                    <td class="lb-td-authors lb-truncate">{{ ref.authors }}</td>
                                    <td class="lb-td-num" style="text-align:center">{{ ref.year || '—' }}</td>
                                    <td class="lb-td-num"><span class="lb-num-pill" :class="ref.cited_by_count > 100 ? 'is-strong' : ref.cited_by_count > 20 ? 'is-accent' : ''">{{ (ref.cited_by_count || 0).toLocaleString() }}</span></td>
                                    <td class="lb-td-num"><span v-if="ref.referenced_by_own > 1" class="lb-num-pill is-accent">{{ ref.referenced_by_own }}×</span><span v-else style="color:var(--lb-mute-2)">1×</span></td>
                                    <td class="lb-td-c"><span v-if="ref.in_library" style="color:var(--lb-accent)" v-html="icons.check"></span><span v-else style="color:var(--lb-mute-2)">—</span></td>
                                    <td class="lb-td-c"><span class="lb-num-pill" :class="ref.source === 'pdf' ? 'is-accent' : ''">{{ ref.source === 'pdf' ? 'PDF' : 'OA' }}</span></td>
                                    <td><a v-if="ref.doi" :href="'https://doi.org/' + ref.doi" target="_blank" class="lb-mono" style="font-size:11px;color:var(--lb-accent);text-decoration:none">{{ ref.doi }}</a><a v-else-if="ref.title" :href="scholarUrl(ref)" target="_blank" style="font-size:11px;color:var(--lb-mute);text-decoration:none" :title="$t('refs.scholarTitle')">Scholar &#8599;</a><span v-else style="color:var(--lb-mute-2)">—</span></td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Missing Sources Table -->
                <div v-show="activeTab==='missing'">
                    <div style="margin-bottom:18px">
                        <p style="font-size:13px;color:var(--lb-ink-3);margin:0">{{ $t('analysis.missingTableHint') }}</p>
                    </div>
                    <div v-if="filteredMissing.length === 0" class="lb-empty">
                        <div class="lb-empty-mark">∅</div>
                        <p class="lb-empty-text">{{ $t('analysis.noMissingSources') }}</p>
                    </div>
                    <div v-else style="overflow-x:auto">
                        <table class="lb-table">
                            <thead><tr>
                                <th>{{ $t('common.title') }}</th>
                                <th>{{ $t('common.authors') }}</th>
                                <th style="text-align:center">{{ $t('common.year') }}</th>
                                <th class="is-sortable" style="text-align:right" @click="sortMissing('referenced_by_count')">{{ $t('analysis.refByOwn') }} ↕</th>
                                <th class="is-sortable" style="text-align:right" @click="sortMissing('cited_by_count')">{{ $t('common.citations') }} ↕</th>
                                <th>DOI</th>
                            </tr></thead>
                            <tbody>
                                <tr v-for="src in sortedMissing" :key="src.id">
                                    <td class="lb-td-title">{{ src.title }}</td>
                                    <td class="lb-td-authors lb-truncate">{{ src.authors }}</td>
                                    <td class="lb-td-num" style="text-align:center">{{ src.year || '—' }}</td>
                                    <td class="lb-td-num"><span class="lb-num-pill is-accent">{{ src.referenced_by_count }}×</span></td>
                                    <td class="lb-td-num">{{ (src.cited_by_count || 0).toLocaleString() }}</td>
                                    <td><a v-if="src.doi" :href="'https://doi.org/' + src.doi" target="_blank" class="lb-mono" style="font-size:11px;color:var(--lb-accent);text-decoration:none">{{ src.doi }}</a><a v-else-if="src.title" :href="scholarUrl(src)" target="_blank" style="font-size:11px;color:var(--lb-mute);text-decoration:none" :title="$t('refs.scholarTitle')">Scholar &#8599;</a><span v-else style="color:var(--lb-mute-2)">—</span></td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Empty state -->
            <div v-if="!networkData && !loading" class="lb-empty" style="border:1px solid var(--lb-hairline);border-radius:14px;background:var(--lb-bg-elev);padding:64px 32px">
                <div class="lb-empty-mark">◌</div>
                <h3 class="lb-empty-title">{{ $t('analysis.emptyTitle') }}</h3>
                <p class="lb-empty-text">
                    {{ $t('analysis.emptyText') }}
                </p>
                <div style="margin:24px auto 16px;display:inline-flex;align-items:center;gap:10px;font-size:12.5px;color:var(--lb-ink-3)">
                    <span class="lb-field-label" style="margin:0">{{ $t('analysis.stat.depth') }}</span>
                    <div class="lb-segmented">
                        <button v-for="d in [1,2,3,4,5]" :key="d" @click="depth = d" class="lb-seg" :class="{'is-on': depth === d}" style="min-width:30px;justify-content:center">{{ d }}</button>
                    </div>
                    <span class="lb-mono" style="font-size:11px;color:var(--lb-mute)">{{ depthLabel }}</span>
                </div>
                <div>
                    <button @click="runAnalysis" class="lb-btn lb-btn-primary" style="padding:10px 22px;font-size:13px">
                    {{ $t('analysis.start') }}
                </button>
            </div>
        </div>
    `,
    data() {
        return {
            loading: false,
            updatingCitations: false,
            networkData: analysisStore.networkData,
            graphSearchQuery: '',
            graphSearchOpen: false,
            highlightedNodeId: null,
            graphPanelOpen: false,
            graphPanelCategory: '',
            graphPanelSearch: '',
            graphNodes: [],
            graphDistance: 25,
            stats: analysisStore.stats,
            activeTab: analysisStore.activeTab,
            selectedNode: analysisStore.selectedNode,
            depth: analysisStore.depth,
            // Sort states
            sortKey: analysisStore.sortKey,
            sortDir: analysisStore.sortDir,
            ownSortKey: analysisStore.ownSortKey,
            ownSortDir: analysisStore.ownSortDir,
            refSortKey: analysisStore.refSortKey,
            refSortDir: analysisStore.refSortDir,
            refFilter: analysisStore.refFilter,
            minRefs: analysisStore.minRefs,
            minCitations: analysisStore.minCitations,
            topNRefs: analysisStore.topNRefs,
            error: null,
            icons,
            bulkExtracting: false,
            bulkExtractResult: analysisStore.bulkExtractResult,
            bulkProgress: { percent: 0, message: '', current_paper: 0, total_papers: 0 },
            bulkPaperResults: [],
            // Chat
            chatOpen: true,
            chatMessages: analysisStore.chatMessages,
            chatInput: '',
            chatStreaming: false,
            chatStreamContent: '',
            chatContextPaper: analysisStore.chatContextPaper,
            // Node detail
            nodeAbstract: null,
            nodeAbstractLoading: false,
            nodeAbstractError: null,
            // Editorial UI state
            mode: 'categories', // categories | years | authors
            hoverNode: null,
        };
    },
    mounted() {
        // Restore graph if data exists from previous visit
        if (this.networkData && this.activeTab === 'network') {
            this.$nextTick(() => this.renderGraph());
        }
    },
    computed: {
        // Uebersetzter Text gehoert nach computed, nicht nach data: `data()`
        // laeuft einmal, ein Sprachwechsel danach erreicht es nie wieder.
        quickQuestions() {
            return [
                t('analysis.q.about'),
                t('analysis.q.findings'),
                t('analysis.q.methods'),
                t('analysis.q.mostCited'),
            ];
        },
        statBlocks() {
            const s = this.stats || {};
            return [
                { label: t('analysis.stat.ownItems'),   value: s.own_papers ?? '—' },
                { label: t('analysis.stat.inOpenalex'), value: s.papers_found_in_openalex ?? '—' },
                { label: t('analysis.stat.references'), value: s.total_references ?? '—' },
                { label: t('analysis.stat.pdfRefs'),    value: s.pdf_reference_edges ?? 0 },
                { label: t('analysis.stat.shared'),     value: s.shared_references ?? '—' },
                { label: t('analysis.stat.missing2'),   value: this.filteredMissing.length },
                { label: t('analysis.stat.depth'),      value: s.depth ?? this.depth ?? 1 },
            ];
        },
        tabDefs() {
            const nd = this.networkData || {};
            return [
                { id: 'network',    label: t('analysis.tab.network'),    count: null },
                { id: 'ownstats',   label: t('analysis.tab.ownItems'),   count: (nd.own_paper_stats || []).length || null },
                { id: 'references', label: t('analysis.tab.references'), count: (nd.referenced_papers || []).length || null },
                { id: 'missing',    label: t('analysis.tab.missing'),    count: this.filteredMissing.length || null },
            ];
        },
        depthLabel() {
            return {
                1: t('analysis.depth.1'), 2: t('analysis.depth.2'), 3: t('analysis.depth.3'),
                4: t('analysis.depth.4'), 5: t('analysis.depth.5'),
            }[this.depth] || '';
        },
        graphSearchResults() {
            if (!this.graphSearchQuery || !this.graphNodes.length) return [];
            const q = this.graphSearchQuery.toLowerCase();
            return this.graphNodes.filter(n => (n.title || '').toLowerCase().includes(q) || (n.authors || '').toLowerCase().includes(q)).slice(0, 20);
        },
        graphPanelCategoryList() {
            if (!this.graphNodes.length) return [];
            const cats = new Map();
            this.graphNodes.filter(n => n.type === 'own' && n.categories).forEach(n => {
                (n.categories || []).forEach(c => { if (!cats.has(c.id)) cats.set(c.id, c); });
            });
            return [...cats.values()].sort((a, b) => a.name.localeCompare(b.name));
        },
        graphPanelFilteredPapers() {
            if (!this.graphNodes.length) return [];
            let list = this.graphNodes.filter(n => n.type === 'own');
            if (this.graphPanelCategory) {
                const cid = parseInt(this.graphPanelCategory);
                list = list.filter(n => (n.categories || []).some(c => c.id === cid));
            }
            if (this.graphPanelSearch) {
                const q = this.graphPanelSearch.toLowerCase();
                list = list.filter(n => (n.title || '').toLowerCase().includes(q) || (n.authors || '').toLowerCase().includes(q));
            }
            return list.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
        },
        totalOwnCitations() {
            if (!this.networkData?.own_paper_stats) return 0;
            return this.networkData.own_paper_stats.reduce((s, p) => s + (p.cited_by_count || 0), 0);
        },
        avgOwnCitations() {
            if (!this.networkData?.own_paper_stats?.length) return '0';
            return (this.totalOwnCitations / this.networkData.own_paper_stats.length).toFixed(1);
        },
        maxOwnCitations() {
            if (!this.networkData?.own_paper_stats) return 0;
            return Math.max(0, ...this.networkData.own_paper_stats.map(p => p.cited_by_count || 0));
        },
        sortedOwnPapers() {
            if (!this.networkData?.own_paper_stats) return [];
            const s = [...this.networkData.own_paper_stats];
            const key = this.ownSortKey;
            const dir = this.ownSortDir;
            s.sort((a, b) => {
                const va = a[key] ?? '';
                const vb = b[key] ?? '';
                if (typeof va === 'string') return va.localeCompare(vb) * (dir > 0 ? 1 : -1);
                return ((vb || 0) - (va || 0)) * (dir > 0 ? -1 : 1);
            });
            return s;
        },
        sortedReferencedPapers() {
            if (!this.networkData?.referenced_papers) return [];
            let list = [...this.networkData.referenced_papers];
            if (this.refFilter === 'not_in_lib') list = list.filter(r => !r.in_library);
            else if (this.refFilter === 'in_lib') list = list.filter(r => r.in_library);
            const key = this.refSortKey;
            const dir = this.refSortDir;
            list.sort((a, b) => {
                const va = a[key] ?? '';
                const vb = b[key] ?? '';
                if (typeof va === 'string') return va.localeCompare(vb) * (dir > 0 ? 1 : -1);
                return ((vb || 0) - (va || 0)) * (dir > 0 ? -1 : 1);
            });
            return list;
        },
        filteredMissing() {
            if (!this.networkData) return [];
            return this.networkData.missing_sources.filter(s => (s.referenced_by_count || 0) >= 2);
        },
        sortedMissing() {
            const s = [...this.filteredMissing];
            s.sort((a, b) => {
                const va = a[this.sortKey] || 0;
                const vb = b[this.sortKey] || 0;
                return (vb - va) * (this.sortDir > 0 ? -1 : 1);
            });
            return s;
        },
    },
    methods: {
        setMode(m) {
            if (this.mode === m) return;
            this.mode = m;
            // Re-render graph to apply new node fill scheme
            if (this.networkData && this.activeTab === 'network') {
                this.$nextTick(() => this.renderGraph());
            }
        },
        async runAnalysis() {
            this.loading = true;
            this.error = null;
            this.selectedNode = null;
            try {
                const data = await api('/api/analysis/build?depth=' + this.depth, { method: 'POST' });
                this.networkData = data;
                this.stats = data.stats;
                this.$nextTick(() => this.renderGraph());
            } catch (e) {
                this.error = t('analysis.failed', { message: e.message });
            }
            this.loading = false;
        },
        async updateCitations() {
            this.updatingCitations = true;
            try {
                const result = await api('/api/analysis/update-citations', { method: 'POST' });
                alert(result.updated + ' Paper aktualisiert.');
                // Re-run analysis to get fresh data
                await this.runAnalysis();
            } catch (e) {
                this.error = 'Aktualisierung fehlgeschlagen: ' + e.message;
            }
            this.updatingCitations = false;
        },
        async bulkExtractRefs() {
            this.bulkExtracting = true;
            this.bulkExtractResult = null;
            this.bulkProgress = { percent: 0, message: 'Starte...', current_paper: 0, total_papers: 0 };
            this.bulkPaperResults = [];
            try {
                const response = await fetch('/api/papers/bulk-extract-references', { method: 'POST' });
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop();
                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;
                        try {
                            const evt = JSON.parse(line.slice(6));
                            if (evt.type === 'progress') {
                                this.bulkProgress = {
                                    percent: evt.percent || 0,
                                    message: evt.message ? translateDetail(evt.message) : '',
                                    current_paper: evt.current_paper || 0,
                                    total_papers: evt.total_papers || 0,
                                };
                            } else if (evt.type === 'paper_result') {
                                this.bulkPaperResults.push(evt);
                            } else if (evt.type === 'complete') {
                                this.bulkExtractResult = evt;
                            }
                        } catch (parseErr) { /* skip */ }
                    }
                }
                // Show final summary
                if (this.bulkExtractResult) {
                    const r = this.bulkExtractResult;
                    alert(t('analysis.refsExtractedHeading') + '\n' +
                        r.processed + ' Paper verarbeitet\n' +
                        t('analysis.refsFound', { count: r.total_references }) + '\n' +
                        t('analysis.refsInLibrary', { count: r.total_in_library }));
                }
            } catch (e) {
                alert('Bulk-Extraktion fehlgeschlagen: ' + e.message);
            }
            this.bulkExtracting = false;
        },
        sortMissing(key) {
            if (this.sortKey === key) this.sortDir *= -1;
            else { this.sortKey = key; this.sortDir = -1; }
        },
        sortOwn(key) {
            if (this.ownSortKey === key) this.ownSortDir *= -1;
            else { this.ownSortKey = key; this.ownSortDir = -1; }
        },
        sortRef(key) {
            if (this.refSortKey === key) this.refSortDir *= -1;
            else { this.refSortKey = key; this.refSortDir = -1; }
        },
        scholarUrl(ref) {
            return scholarSearchUrl(ref);
        },
        nodeTypeBadgeClass(type) {
            const m = {
                own: 'bg-accent-soft text-accent-ink',
                missing: 'bg-accent-soft text-accent-ink',
                own_ref: 'bg-accent-soft text-accent-ink',
                external: 'bg-gray-100 text-gray-700',
                depth2: 'bg-accent-soft text-accent-ink',
                depth3: 'bg-accent-soft text-accent-ink',
                depth4: 'bg-slate-100 text-slate-600',
                depth5: 'bg-gray-50 text-gray-500',
            };
            return m[type] || 'bg-gray-100 text-gray-700';
        },
        nodeTypeLabel(type) {
            const m = {
                own: 'Eigenes Paper',
                missing: t('analysis.nodeMissing'),
                own_ref: t('refs.inLibrary'),
                external: t('analysis.nodeExternal'),
                depth2: 'Tiefe 2',
                depth3: 'Tiefe 3',
                depth4: 'Tiefe 4',
                depth5: 'Tiefe 5',
            };
            return m[type] || type;
        },
        renderGraph() {
            const container = this.$refs.graphContainer;
            if (!container || !this.networkData) return;
            container.innerHTML = '';

            const width = container.clientWidth;
            const height = container.clientHeight || 600;
            const allNodes = this.networkData.nodes;
            const allEdges = this.networkData.edges;

            // Build node ID set
            const nodeMap = new Map();
            allNodes.forEach(n => nodeMap.set(n.id, n));

            // Only keep edges where both endpoints exist
            const validEdges = allEdges.filter(e => nodeMap.has(e.source) && nodeMap.has(e.target));

            // Find connected node IDs
            const connectedIds = new Set();
            validEdges.forEach(e => {
                connectedIds.add(e.source);
                connectedIds.add(e.target);
            });
            // Always include own papers
            allNodes.forEach(n => { if (n.type === 'own') connectedIds.add(n.id); });

            const nodes = allNodes.filter(n => connectedIds.has(n.id)).map(n => ({...n}));
            const edges = validEdges.map(e => ({source: e.source, target: e.target, edge_type: e.edge_type}));

            if (nodes.length === 0) {
                container.innerHTML = '<div class="flex items-center justify-center h-full text-gray-400 text-sm">'
                    + t('analysis.noNetworkData') + '</div>';
                return;
            }

            // Editorial color scheme — read live tokens so dark-mode works
            const cssVar = lbToken;
            const editorialInk      = cssVar('--lb-ink', '#1a1a1a');
            const editorialInk3     = cssVar('--lb-ink-3', '#6b6760');
            const editorialMute     = cssVar('--lb-mute', '#8e8a82');
            const editorialMute2    = cssVar('--lb-mute-2', '#a8a49c');
            const editorialHairline = cssVar('--lb-hairline-2', '#d8d1be');
            const editorialAccent   = cssVar('--lb-accent', '#c2410c');
            const editorialAccentSoft = cssVar('--lb-accent-soft', '#fce9da');
            const colorMap = {
                own:      editorialInk,           // own paper = ink (per spec)
                missing:  editorialAccent,        // bridge / shared source = accent
                own_ref:  editorialAccentSoft,    // in library
                external: editorialInk3,
                depth2:   editorialMute,
                depth3:   editorialMute2,
                depth4:   editorialHairline,
                depth5:   editorialHairline,
            };
            // For Mode "years" we interpolate ink-3 → accent
            const yearVals = nodes.map(n => n.year).filter(y => y);
            const yearScale = (yearVals.length >= 2)
                ? d3.scaleLinear().domain([Math.min(...yearVals), Math.max(...yearVals)]).range([0, 1])
                : () => 0.5;
            const yearInterp = d3.interpolateRgb(editorialAccentSoft, editorialAccent);

            // --- Find bridge nodes: non-own nodes that sit on paths between own papers ---
            const ownNodeIds = new Set(nodes.filter(n => n.type === 'own').map(n => n.id));
            // Build adjacency: for each node, which nodes are connected (undirected)
            const adj = new Map();
            edges.forEach(e => {
                const s = typeof e.source === 'object' ? e.source.id : e.source;
                const t = typeof e.target === 'object' ? e.target.id : e.target;
                if (!adj.has(s)) adj.set(s, new Set());
                if (!adj.has(t)) adj.set(t, new Set());
                adj.get(s).add(t);
                adj.get(t).add(s);
            });
            // BFS-based bridge detection: find non-own nodes reachable from >=2 own papers within minR hops
            const minR = this.minRefs || 1;
            const reachability = new Map(); // nodeId -> Set of ownPaperIds that can reach it
            const bfsParent = new Map();    // nodeId -> Map(ownPaperId -> parentNodeId) for path tracing
            for (const ownId of ownNodeIds) {
                // BFS from this own paper up to minR hops
                let frontier = [ownId];
                const visited = new Set([ownId]);
                for (let depth = 0; depth < minR && frontier.length > 0; depth++) {
                    const nextFrontier = [];
                    for (const cur of frontier) {
                        const neighbors = adj.get(cur);
                        if (!neighbors) continue;
                        for (const nb of neighbors) {
                            if (visited.has(nb)) continue;
                            visited.add(nb);
                            if (!reachability.has(nb)) reachability.set(nb, new Set());
                            reachability.get(nb).add(ownId);
                            if (!bfsParent.has(nb)) bfsParent.set(nb, new Map());
                            bfsParent.get(nb).set(ownId, cur);
                            // Don't expand through other own papers
                            if (!ownNodeIds.has(nb)) nextFrontier.push(nb);
                        }
                    }
                    frontier = nextFrontier;
                }
            }
            const bridgeNodes = new Set();
            for (const [nid, owners] of reachability) {
                if (!ownNodeIds.has(nid) && owners.size >= 2) bridgeNodes.add(nid);
            }

            // Build edge index for fast lookup
            const edgeIndex = new Map();
            edges.forEach((e, i) => {
                const s = typeof e.source === 'object' ? e.source.id : e.source;
                const t = typeof e.target === 'object' ? e.target.id : e.target;
                edgeIndex.set(s + '|' + t, i);
                edgeIndex.set(t + '|' + s, i);
            });

            // Trace BFS paths from bridge nodes back to own papers to find bridge edges
            const bridgeEdgeSet = new Set();
            for (const bNode of bridgeNodes) {
                const owners = reachability.get(bNode);
                if (!owners) continue;
                for (const ownId of owners) {
                    let cur = bNode;
                    while (cur && cur !== ownId) {
                        const parent = bfsParent.get(cur)?.get(ownId);
                        if (parent == null) break;
                        const ei = edgeIndex.get(cur + '|' + parent);
                        if (ei != null) bridgeEdgeSet.add(ei);
                        cur = parent;
                    }
                }
            }
            // Also mark own<->own edges
            edges.forEach((e, i) => {
                const s = typeof e.source === 'object' ? e.source.id : e.source;
                const t = typeof e.target === 'object' ? e.target.id : e.target;
                if (ownNodeIds.has(s) && ownNodeIds.has(t)) bridgeEdgeSet.add(i);
            });

            const maxCited = d3.max(nodes, d => d.cited_by_count) || 1;
            const sizeScale = d3.scaleSqrt().domain([0, maxCited]).range([5, 22]);

            const svg = d3.select(container)
                .append('svg')
                .attr('width', width)
                .attr('height', height)
                .attr('viewBox', [0, 0, width, height]);

            const g = svg.append('g');

            // Zoom - track if user has manually zoomed
            let userHasZoomed = false;
            const zoomBehavior = d3.zoom()
                .scaleExtent([0.05, 5])
                .on('zoom', (event) => {
                    g.attr('transform', event.transform);
                    // Mark as user-zoomed only for direct user gestures
                    if (event.sourceEvent) userHasZoomed = true;
                });
            svg.call(zoomBehavior);

            // Arrow markers — editorial palette
            const defs = svg.append('defs');
            const mkMarker = (id, fill) => {
                defs.append('marker')
                    .attr('id', id)
                    .attr('viewBox', '0 -5 10 10')
                    .attr('refX', 20).attr('refY', 0)
                    .attr('markerWidth', 6).attr('markerHeight', 6)
                    .attr('orient', 'auto')
                    .append('path').attr('d', 'M0,-5L10,0L0,5').attr('fill', fill);
            };
            mkMarker('arrowhead', editorialHairline);
            mkMarker('arrowhead-bridge', editorialAccent);
            mkMarker('arrowhead-pdfref', editorialMute2);

            // Track PDF-ref edges
            const pdfRefEdgeSet = new Set();
            edges.forEach((e, i) => {
                if (e.edge_type === 'pdf_ref') pdfRefEdgeSet.add(i);
            });

            // Simulation
            // Place nodes initially near center to avoid drift
            nodes.forEach(n => {
                if (n.x == null) n.x = width / 2 + (Math.random() - 0.5) * 100;
                if (n.y == null) n.y = height / 2 + (Math.random() - 0.5) * 100;
            });

            const dist = this.graphDistance || 25;
            const simulation = d3.forceSimulation(nodes)
                .force('link', d3.forceLink(edges).id(d => d.id).distance(dist).strength(0.8))
                .force('charge', d3.forceManyBody().strength(-(dist * 1.2)).distanceMax(200))
                .force('center', d3.forceCenter(width / 2, height / 2).strength(0.05))
                .force('collision', d3.forceCollide().radius(d => sizeScale(d.cited_by_count || 0) + 1));

            // Links - normal below, bridge edges and pdf-ref edges on top
            const link = g.append('g')
                .selectAll('line')
                .data(edges)
                .join('line')
                .attr('stroke', (d, i) => netEdgeStroke(i, bridgeEdgeSet, pdfRefEdgeSet, false))
                .attr('stroke-width', (d, i) => bridgeEdgeSet.has(i) ? 2.5 : pdfRefEdgeSet.has(i) ? 1.5 : 1)
                .attr('stroke-opacity', (d, i) => bridgeEdgeSet.has(i) ? 0.7 : pdfRefEdgeSet.has(i) ? 0.5 : 0.3)
                .attr('marker-end', (d, i) => pdfRefEdgeSet.has(i) ? 'url(#arrowhead-pdfref)' : (bridgeEdgeSet.has(i) ? 'url(#arrowhead-bridge)' : 'url(#arrowhead)'));

            // Nodes
            const vm = this;
            const node = g.append('g')
                .selectAll('circle')
                .data(nodes)
                .join('circle')
                .attr('r', d => sizeScale(d.cited_by_count || 0))
                .attr('fill', d => {
                    // Mode "years": interpolate paper-nodes by publication year
                    if (vm.mode === 'years' && d.year) return yearInterp(yearScale(d.year));
                    // Color bridge nodes (reachable from >=2 own papers within depth) as accent
                    if (bridgeNodes.has(d.id)) return colorMap.missing;
                    return colorMap[d.type] || editorialMute2;
                })
                .attr('stroke', d => netNodeStroke(d, bridgeNodes, false))
                .attr('stroke-width', d => d.type === 'own' ? 3 : (bridgeNodes.has(d.id) ? 2 : 1))
                .style('cursor', 'pointer')
                .on('mouseenter', (event, d) => { vm.hoverNode = d; })
                .on('mouseleave', () => { vm.hoverNode = null; })
                .on('click', (event, d) => {
                    event.stopPropagation();
                    // Editorial spec: click on own paper-node navigates to PaperDetail slide-in
                    if (d.paper_id) {
                        vm.$router.push('/paper/' + d.paper_id);
                        return;
                    }
                    vm.selectedNode = d;
                    vm.highlightedNodeId = d.id;
                    vm._applyHighlight(d.id, node, link, label, citLabel, nodes, edges, adj, colorMap, bridgeNodes, ownNodeIds, bridgeEdgeSet, pdfRefEdgeSet, sizeScale);
                })
                .call(d3.drag()
                    .on('start', (event, d) => {
                        if (!event.active) simulation.alphaTarget(0.05).restart();
                        d.fx = d.x; d.fy = d.y;
                    })
                    .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
                    .on('end', (event, d) => {
                        if (!event.active) simulation.alphaTarget(0);
                        // Keep node pinned where user dropped it
                        d.fx = event.x; d.fy = event.y;
                    })
                );

            node.append('title').text(d => d.title || d.id);

            // Labels for own papers
            const label = g.append('g')
                .selectAll('text')
                .data(nodes.filter(n => n.type === 'own'))
                .join('text')
                .text(d => {
                    const t = d.title || '';
                    return t.length > 30 ? t.substring(0, 28) + '...' : t;
                })
                .attr('font-size', '9px')
                .attr('fill', lbToken('--lb-ink-2', '#3a3833'))
                .attr('font-weight', '500')
                .attr('dx', d => sizeScale(d.cited_by_count || 0) + 4)
                .attr('dy', 3)
                .style('pointer-events', 'none');

            // Citation count labels on bridge nodes (connecting nodes)
            const citLabel = g.append('g')
                .selectAll('text')
                .data(nodes.filter(n => bridgeNodes.has(n.id) && n.cited_by_count > 0))
                .join('text')
                .text(d => d.cited_by_count >= 1000 ? (d.cited_by_count / 1000).toFixed(1) + 'k' : d.cited_by_count)
                .attr('font-size', '8px')
                .attr('fill', lbToken('--lb-accent-ink', '#7a2808'))
                .attr('font-weight', '600')
                .attr('text-anchor', 'middle')
                .attr('dy', -2)
                .attr('dx', 0)
                .style('pointer-events', 'none');

            simulation.on('tick', () => {
                link
                    .attr('x1', d => d.source.x)
                    .attr('y1', d => d.source.y)
                    .attr('x2', d => d.target.x)
                    .attr('y2', d => d.target.y);
                node
                    .attr('cx', d => d.x)
                    .attr('cy', d => d.y);
                label
                    .attr('x', d => d.x)
                    .attr('y', d => d.y);
                citLabel
                    .attr('x', d => d.x)
                    .attr('y', d => d.y - sizeScale(d.cited_by_count || 0) - 2);
            });

            // Auto-fit: only on initial load, skip if user already zoomed
            simulation.on('end', () => {
                if (userHasZoomed) return;
                const pad = 40;
                let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
                nodes.forEach(n => {
                    const r = sizeScale(n.cited_by_count || 0);
                    if (n.x - r < minX) minX = n.x - r;
                    if (n.y - r < minY) minY = n.y - r;
                    if (n.x + r > maxX) maxX = n.x + r;
                    if (n.y + r > maxY) maxY = n.y + r;
                });
                const bw = maxX - minX + pad * 2;
                const bh = maxY - minY + pad * 2;
                const scale = Math.min(width / bw, height / bh, 1.5);
                const cx = (minX + maxX) / 2;
                const cy = (minY + maxY) / 2;
                const transform = d3.zoomIdentity
                    .translate(width / 2, height / 2)
                    .scale(scale)
                    .translate(-cx, -cy);
                svg.transition().duration(750).call(zoomBehavior.transform, transform);
            });

            this._simulation = simulation;
            this.graphNodes = nodes;
            this._graphNodes = nodes;
            this._graphEdges = edges;
            this._graphAdj = adj;
            this._graphSvg = svg;
            this._graphZoom = zoomBehavior;
            this._graphD3 = { node, link, label, citLabel, colorMap, bridgeNodes, ownNodeIds, bridgeEdgeSet, pdfRefEdgeSet, sizeScale };

            // Apply visibility filters on initial render
            this._applyVisibilityFilters();
        },
        graphNodeColor(type) {
            const m = {
                own: lbToken('--lb-ink', '#1a1a1a'),
                missing: lbToken('--lb-accent', '#c2410c'),
                own_ref: lbToken('--lb-accent-soft', '#fce9da'),
                external: lbToken('--lb-ink-3', '#6b6760'),
                depth2: lbToken('--lb-mute', '#8e8a82'),
                depth3: lbToken('--lb-mute-2', '#a8a49c'),
                depth4: lbToken('--lb-hairline-2', '#d8d1be'),
                depth5: lbToken('--lb-hairline', '#e7e2d6'),
            };
            return m[type] || lbToken('--lb-mute-2', '#a8a49c');
        },
        navigateToNode(n) {
            this.graphSearchOpen = false;
            this.graphSearchQuery = '';
            this.selectedNode = n;
            this.highlightedNodeId = n.id;
            // Apply highlight
            if (this._graphD3) {
                const { node, link, label, citLabel, colorMap, bridgeNodes, ownNodeIds, bridgeEdgeSet, pdfRefEdgeSet, sizeScale } = this._graphD3;
                this._applyHighlight(n.id, node, link, label, citLabel, this.graphNodes, this._graphEdges, this._graphAdj, colorMap, bridgeNodes, ownNodeIds, bridgeEdgeSet, pdfRefEdgeSet, sizeScale);
            }
            // Pan to node
            if (this._graphSvg && this._graphZoom && n.x != null) {
                const container = this.$refs.graphContainer;
                const width = container.clientWidth;
                const height = container.clientHeight || 600;
                const scale = 1.5;
                const transform = d3.zoomIdentity.translate(width / 2, height / 2).scale(scale).translate(-n.x, -n.y);
                this._graphSvg.transition().duration(600).call(this._graphZoom.transform, transform);
            }
        },
        clearHighlight() {
            this.highlightedNodeId = null;
            if (this._graphD3) {
                const { node, link, label, citLabel, colorMap, bridgeNodes, ownNodeIds, bridgeEdgeSet, pdfRefEdgeSet, sizeScale } = this._graphD3;
                this._applyHighlight(null, node, link, label, citLabel, this.graphNodes, this._graphEdges, this._graphAdj, colorMap, bridgeNodes, ownNodeIds, bridgeEdgeSet, pdfRefEdgeSet, sizeScale);
            }
        },
        _applyHighlight(nodeId, node, link, label, citLabel, nodes, edges, adj, colorMap, bridgeNodes, ownNodeIds, bridgeEdgeSet, pdfRefEdgeSet, sizeScale) {
            if (!nodeId) {
                // Reset all to defaults
                node.attr('opacity', 1)
                    .attr('fill', d => colorMap[d.type] || lbToken('--lb-mute-2', '#a8a49c'))
                    .attr('stroke', d => netNodeStroke(d, bridgeNodes, false))
                    .attr('stroke-width', d => d.type === 'own' ? 3 : (bridgeNodes.has(d.id) ? 2 : 1));
                link.attr('stroke-opacity', (d, i) => bridgeEdgeSet.has(i) ? 0.7 : pdfRefEdgeSet.has(i) ? 0.5 : 0.3)
                    .attr('stroke-width', (d, i) => bridgeEdgeSet.has(i) ? 2.5 : pdfRefEdgeSet.has(i) ? 1.5 : 1)
                    .attr('stroke', (d, i) => netEdgeStroke(i, bridgeEdgeSet, pdfRefEdgeSet, false));
                label.attr('opacity', 1);
                citLabel.attr('opacity', 1);
                return;
            }
            const neighbors = adj.get(nodeId) || new Set();
            // Dim all, highlight selected + neighbors
            node.attr('opacity', d => d.id === nodeId || neighbors.has(d.id) ? 1 : 0.12)
                .attr('stroke', d => netNodeStroke(d, bridgeNodes, d.id === nodeId))
                .attr('stroke-width', d => d.id === nodeId ? 4 : (d.type === 'own' ? 3 : (bridgeNodes.has(d.id) ? 2 : 1)));
            link.attr('stroke-opacity', (d) => {
                const s = typeof d.source === 'object' ? d.source.id : d.source;
                const t = typeof d.target === 'object' ? d.target.id : d.target;
                return (s === nodeId || t === nodeId) ? 0.9 : 0.04;
            }).attr('stroke-width', (d) => {
                const s = typeof d.source === 'object' ? d.source.id : d.source;
                const t = typeof d.target === 'object' ? d.target.id : d.target;
                return (s === nodeId || t === nodeId) ? 3 : 0.5;
            }).attr('stroke', (d, i) => {
                const s = typeof d.source === 'object' ? d.source.id : d.source;
                const t = typeof d.target === 'object' ? d.target.id : d.target;
                return netEdgeStroke(i, bridgeEdgeSet, pdfRefEdgeSet, s === nodeId || t === nodeId);
            });
            label.attr('opacity', d => d.id === nodeId || neighbors.has(d.id) ? 1 : 0.1);
            citLabel.attr('opacity', d => d.id === nodeId || neighbors.has(d.id) ? 1 : 0.1);
        },
        applyCategoryFilter() {
            if (!this._graphD3) return;
            const { node, link, label, citLabel, colorMap, bridgeNodes, ownNodeIds, bridgeEdgeSet, pdfRefEdgeSet } = this._graphD3;
            const catId = this.graphPanelCategory ? parseInt(this.graphPanelCategory) : null;
            if (!catId) {
                // Reset - show everything
                node.attr('opacity', 1)
                    .attr('fill', d => colorMap[d.type] || lbToken('--lb-mute-2', '#a8a49c'))
                    .attr('stroke', d => netNodeStroke(d, bridgeNodes, false))
                    .attr('stroke-width', d => d.type === 'own' ? 3 : (bridgeNodes.has(d.id) ? 2 : 1));
                link.attr('stroke-opacity', (d, i) => bridgeEdgeSet.has(i) ? 0.7 : pdfRefEdgeSet.has(i) ? 0.5 : 0.3)
                    .attr('stroke-width', (d, i) => bridgeEdgeSet.has(i) ? 2.5 : pdfRefEdgeSet.has(i) ? 1.5 : 1)
                    .attr('stroke', (d, i) => netEdgeStroke(i, bridgeEdgeSet, pdfRefEdgeSet, false));
                label.attr('opacity', 1);
                citLabel.attr('opacity', 1);
                return;
            }
            // Find own paper IDs matching this category
            const matchIds = new Set();
            this.graphNodes.forEach(n => {
                if (n.type === 'own' && (n.categories || []).some(c => c.id === catId)) {
                    matchIds.add(n.id);
                }
            });
            // Also include their direct neighbors
            const relevantIds = new Set(matchIds);
            const adj = this._graphAdj;
            matchIds.forEach(mid => {
                const neighbors = adj.get(mid);
                if (neighbors) neighbors.forEach(nb => relevantIds.add(nb));
            });
            // Dim non-relevant nodes
            node.attr('opacity', d => relevantIds.has(d.id) ? 1 : 0.08)
                .attr('fill', d => colorMap[d.type] || lbToken('--lb-mute-2', '#a8a49c'))
                .attr('stroke', d => netNodeStroke(d, bridgeNodes, matchIds.has(d.id)))
                .attr('stroke-width', d => matchIds.has(d.id) ? 4 : (d.type === 'own' ? 3 : (bridgeNodes.has(d.id) ? 2 : 1)));
            link.attr('stroke-opacity', d => {
                const s = typeof d.source === 'object' ? d.source.id : d.source;
                const t = typeof d.target === 'object' ? d.target.id : d.target;
                return (matchIds.has(s) || matchIds.has(t)) ? 0.7 : 0.03;
            }).attr('stroke-width', d => {
                const s = typeof d.source === 'object' ? d.source.id : d.source;
                const t = typeof d.target === 'object' ? d.target.id : d.target;
                return (matchIds.has(s) || matchIds.has(t)) ? 2.5 : 0.5;
            }).attr('stroke', (d, i) => {
                const s = typeof d.source === 'object' ? d.source.id : d.source;
                const t = typeof d.target === 'object' ? d.target.id : d.target;
                return netEdgeStroke(i, bridgeEdgeSet, pdfRefEdgeSet, matchIds.has(s) || matchIds.has(t));
            });
            label.attr('opacity', d => relevantIds.has(d.id) ? 1 : 0.05);
            citLabel.attr('opacity', d => relevantIds.has(d.id) ? 1 : 0.05);
        },
        async fetchNodeAbstract(node) {
            this.nodeAbstract = null;
            this.nodeAbstractError = null;
            if (!node) return;

            // If it's an own paper, check if we already have abstract in DB
            if (node.paper_id) {
                this.nodeAbstractLoading = true;
                try {
                    const data = await api('/api/papers/' + node.paper_id);
                    if (data.abstract) {
                        this.nodeAbstract = data.abstract;
                        this.nodeAbstractLoading = false;
                        return;
                    }
                } catch (e) { /* continue to OpenAlex */ }
            }

            // Try OpenAlex if we have DOI or openalex_id
            const doi = node.doi;
            const oaId = node.openalex_id || node.id;
            if (!doi && !oaId) {
                this.nodeAbstractLoading = false;
                this.nodeAbstractError = t('analysis.abstractNoDoi');
                return;
            }

            this.nodeAbstractLoading = true;
            try {
                const data = await api('/api/analysis/node-abstract?doi=' + encodeURIComponent(doi || '') + '&openalex_id=' + encodeURIComponent(oaId || ''));
                if (data.abstract) {
                    this.nodeAbstract = data.abstract;
                } else {
                    this.nodeAbstractError = t('analysis.abstractNotInOpenalex');
                }
            } catch (e) {
                this.nodeAbstractError = t('analysis.abstractLoadFailed');
            } finally {
                this.nodeAbstractLoading = false;
            }
        },
        askAboutSelectedNode() {
            if (!this.selectedNode) return;
            // Set up chat context with this paper
            this.chatContextPaper = {
                title: this.selectedNode.title,
                authors: this.selectedNode.authors,
                year: this.selectedNode.year,
                doi: this.selectedNode.doi,
                abstract: this.nodeAbstract || '',
                paper_id: this.selectedNode.paper_id || null,
                openalex_id: this.selectedNode.openalex_id || this.selectedNode.id || null,
            };
            this.chatMessages = [];
            this.chatStreamContent = '';
            this.chatOpen = true;
            // Scroll to chat
            this.$nextTick(() => {
                const chatEl = this.$refs.chatMessages;
                if (chatEl) chatEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            });
        },
        clearChat() {
            this.chatMessages = [];
            this.chatStreamContent = '';
        },
        renderMarkdown(text) {
            return renderMarkdownSafe(text);
        },
        async sendChatMessage() {
            const question = this.chatInput.trim();
            if (!question || this.chatStreaming || !this.chatContextPaper) return;

            this.chatInput = '';
            this.chatMessages.push({ role: 'user', content: question });
            this.chatStreaming = true;
            this.chatStreamContent = '';

            this.$nextTick(() => {
                if (this.$refs.chatMessages) {
                    this.$refs.chatMessages.scrollTop = this.$refs.chatMessages.scrollHeight;
                }
            });

            try {
                const history = this.chatMessages.filter(m => m.role !== 'system').slice(0, -1);

                const body = {
                    question,
                    history,
                };
                // Own paper (in DB) — pass paper_id
                if (this.chatContextPaper.paper_id) {
                    body.paper_ids = [this.chatContextPaper.paper_id];
                } else {
                    // External paper — pass metadata
                    body.paper_ids = [];
                    body.external_papers = [{
                        title: this.chatContextPaper.title,
                        authors: this.chatContextPaper.authors,
                        year: this.chatContextPaper.year,
                        doi: this.chatContextPaper.doi,
                        abstract: this.chatContextPaper.abstract,
                    }];
                }

                const resp = await fetch('/api/analysis/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
                });

                if (!resp.ok) {
                    const err = await resp.json();
                    this.chatMessages.push({ role: 'assistant', content: translateDetail(err.error, resp.status) });
                    this.chatStreaming = false;
                    return;
                }

                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';

                    for (const line of lines) {
                        const trimmed = line.trim();
                        if (!trimmed.startsWith('data: ')) continue;
                        const data = trimmed.slice(6);
                        if (data === '[DONE]') break;
                        try {
                            const parsed = JSON.parse(data);
                            if (parsed.error) {
                                this.chatStreamContent += '\n\nFehler: ' + parsed.error;
                            } else if (parsed.content) {
                                this.chatStreamContent += parsed.content;
                            }
                        } catch (e) { /* ignore parse errors */ }
                    }

                    this.$nextTick(() => {
                        if (this.$refs.chatMessages) {
                            this.$refs.chatMessages.scrollTop = this.$refs.chatMessages.scrollHeight;
                        }
                    });
                }

                if (this.chatStreamContent) {
                    this.chatMessages.push({ role: 'assistant', content: translateDetail(this.chatStreamContent) });
                }
            } catch (e) {
                this.chatMessages.push({ role: 'assistant', content: t('error.generic', { message: e.message }) });
            } finally {
                this.chatStreaming = false;
                this.chatStreamContent = '';
                this.$nextTick(() => {
                    if (this.$refs.chatMessages) {
                        this.$refs.chatMessages.scrollTop = this.$refs.chatMessages.scrollHeight;
                    }
                });
            }
        },
        _updateGraphDistance(dist) {
            if (!this._simulation) return;
            const linkForce = this._simulation.force('link');
            const chargeForce = this._simulation.force('charge');
            if (linkForce) linkForce.distance(dist);
            // Scale charge proportionally: base -30 at distance 25
            if (chargeForce) chargeForce.strength(-(dist * 1.2));
            this._simulation.alpha(0.3).restart();
        },
        _updateMinRefs(minR) {
            if (!this._graphD3) return;
            const { node, link, colorMap, ownNodeIds, sizeScale } = this._graphD3;
            const adj = this._graphAdj;
            const edges = this._graphEdges;

            // BFS-based bridge detection: find non-own nodes reachable from >=2 own papers within minR hops
            const reachability = new Map();
            const bfsParent = new Map();
            for (const ownId of ownNodeIds) {
                let frontier = [ownId];
                const visited = new Set([ownId]);
                for (let depth = 0; depth < minR && frontier.length > 0; depth++) {
                    const nextFrontier = [];
                    for (const cur of frontier) {
                        const neighbors = adj.get(cur);
                        if (!neighbors) continue;
                        for (const nb of neighbors) {
                            if (visited.has(nb)) continue;
                            visited.add(nb);
                            if (!reachability.has(nb)) reachability.set(nb, new Set());
                            reachability.get(nb).add(ownId);
                            if (!bfsParent.has(nb)) bfsParent.set(nb, new Map());
                            bfsParent.get(nb).set(ownId, cur);
                            if (!ownNodeIds.has(nb)) nextFrontier.push(nb);
                        }
                    }
                    frontier = nextFrontier;
                }
            }
            const bridgeNodes = new Set();
            for (const [nid, owners] of reachability) {
                if (!ownNodeIds.has(nid) && owners.size >= 2) bridgeNodes.add(nid);
            }

            // Build edge index
            const edgeIndex = new Map();
            edges.forEach((e, i) => {
                const s = typeof e.source === 'object' ? e.source.id : e.source;
                const t = typeof e.target === 'object' ? e.target.id : e.target;
                edgeIndex.set(s + '|' + t, i);
                edgeIndex.set(t + '|' + s, i);
            });

            // Trace BFS paths for bridge edges
            const bridgeEdgeSet = new Set();
            for (const bNode of bridgeNodes) {
                const owners = reachability.get(bNode);
                if (!owners) continue;
                for (const ownId of owners) {
                    let cur = bNode;
                    while (cur && cur !== ownId) {
                        const parent = bfsParent.get(cur)?.get(ownId);
                        if (parent == null) break;
                        const ei = edgeIndex.get(cur + '|' + parent);
                        if (ei != null) bridgeEdgeSet.add(ei);
                        cur = parent;
                    }
                }
            }
            edges.forEach((e, i) => {
                const s = typeof e.source === 'object' ? e.source.id : e.source;
                const t = typeof e.target === 'object' ? e.target.id : e.target;
                if (ownNodeIds.has(s) && ownNodeIds.has(t)) bridgeEdgeSet.add(i);
            });

            // Track PDF-ref edges
            const pdfRefEdgeSet = new Set();
            edges.forEach((e, i) => {
                if (e.edge_type === 'pdf_ref') pdfRefEdgeSet.add(i);
            });

            // Update node colors and strokes
            node.attr('fill', d => {
                if (bridgeNodes.has(d.id)) return colorMap.missing;
                return colorMap[d.type] || lbToken('--lb-mute-2', '#a8a49c');
            })
            .attr('stroke', d => netNodeStroke(d, bridgeNodes, false))
            .attr('stroke-width', d => d.type === 'own' ? 3 : (bridgeNodes.has(d.id) ? 2 : 1));

            // Update edge colors
            link.attr('stroke', (d, i) => netEdgeStroke(i, bridgeEdgeSet, pdfRefEdgeSet, false))
                .attr('stroke-width', (d, i) => bridgeEdgeSet.has(i) ? 2.5 : pdfRefEdgeSet.has(i) ? 1.5 : 1)
                .attr('stroke-opacity', (d, i) => bridgeEdgeSet.has(i) ? 0.7 : pdfRefEdgeSet.has(i) ? 0.5 : 0.3)
                .attr('marker-end', (d, i) => pdfRefEdgeSet.has(i) ? 'url(#arrowhead-pdfref)' : (bridgeEdgeSet.has(i) ? 'url(#arrowhead-bridge)' : 'url(#arrowhead)'));

            // Update stored reference
            this._graphD3.bridgeNodes = bridgeNodes;
            this._graphD3.bridgeEdgeSet = bridgeEdgeSet;
            this._graphD3.pdfRefEdgeSet = pdfRefEdgeSet;

            // Re-apply visibility filters after bridge detection update
            this._applyVisibilityFilters();
        },
        _applyVisibilityFilters() {
            if (!this._graphD3 || !this._graphNodes) return;
            const { node, link, label, citLabel, ownNodeIds, bridgeNodes } = this._graphD3;
            const adj = this._graphAdj;
            const edges = this._graphEdges;
            const minCit = this.minCitations || 0;
            const topN = this.topNRefs || 0;

            // Build nodeId -> node lookup
            const nodeById = new Map();
            this._graphNodes.forEach(n => nodeById.set(n.id, n));

            // Determine top-N allowed set per own paper
            let topNAllowed = null;
            if (topN > 0) {
                topNAllowed = new Set();
                for (const ownId of ownNodeIds) {
                    const neighbors = adj.get(ownId);
                    if (!neighbors) continue;
                    const refs = [];
                    for (const nb of neighbors) {
                        if (ownNodeIds.has(nb)) continue;
                        const n = nodeById.get(nb);
                        if (n) refs.push(n);
                    }
                    refs.sort((a, b) => (b.cited_by_count || 0) - (a.cited_by_count || 0));
                    for (let i = 0; i < Math.min(topN, refs.length); i++) {
                        topNAllowed.add(refs[i].id);
                    }
                }
            }

            // Determine visibility per node
            const visible = new Map();
            this._graphNodes.forEach(n => {
                // Own papers: always visible
                if (ownNodeIds.has(n.id)) { visible.set(n.id, true); return; }
                // Bridge nodes (shared refs): always visible
                if (bridgeNodes && bridgeNodes.has(n.id)) { visible.set(n.id, true); return; }
                // Filter 1: min citations
                if (minCit > 0 && (n.cited_by_count || 0) < minCit) { visible.set(n.id, false); return; }
                // Filter 2: top-N per paper
                if (topNAllowed && !topNAllowed.has(n.id)) { visible.set(n.id, false); return; }
                visible.set(n.id, true);
            });

            // Apply to D3 selections
            node.style('display', d => visible.get(d.id) === false ? 'none' : null);

            link.style('display', (d, i) => {
                const s = typeof d.source === 'object' ? d.source.id : d.source;
                const t = typeof d.target === 'object' ? d.target.id : d.target;
                return (visible.get(s) === false || visible.get(t) === false) ? 'none' : null;
            });

            // Labels only for own papers (always visible)
            // citLabel for bridge nodes — show/hide based on visibility
            if (citLabel) {
                citLabel.style('display', d => visible.get(d.id) === false ? 'none' : null);
            }
        },
    },
    watch: {
        activeTab(val) {
            if (val === 'network' && this.networkData) {
                this.$nextTick(() => {
                    if (this.$refs.graphContainer && !this.$refs.graphContainer.querySelector('svg')) {
                        this.renderGraph();
                    }
                });
            }
        },
        graphDistance(val) {
            this._updateGraphDistance(val);
        },
        minRefs(val) {
            this._updateMinRefs(val);
            this._applyVisibilityFilters();
        },
        minCitations() {
            this._applyVisibilityFilters();
        },
        topNRefs() {
            this._applyVisibilityFilters();
        },
        selectedNode(node) {
            if (node) {
                this.fetchNodeAbstract(node);
            } else {
                this.nodeAbstract = null;
                this.nodeAbstractError = null;
            }
        },
    },
    beforeUnmount() {
        if (this._simulation) this._simulation.stop();
        // Persist state to global store
        analysisStore.networkData = this.networkData;
        analysisStore.stats = this.stats;
        analysisStore.selectedNode = this.selectedNode;
        analysisStore.depth = this.depth;
        analysisStore.activeTab = this.activeTab;
        analysisStore.sortKey = this.sortKey;
        analysisStore.sortDir = this.sortDir;
        analysisStore.ownSortKey = this.ownSortKey;
        analysisStore.ownSortDir = this.ownSortDir;
        analysisStore.refSortKey = this.refSortKey;
        analysisStore.refSortDir = this.refSortDir;
        analysisStore.refFilter = this.refFilter;
        analysisStore.minRefs = this.minRefs;
        analysisStore.minCitations = this.minCitations;
        analysisStore.topNRefs = this.topNRefs;
        analysisStore.bulkExtractResult = this.bulkExtractResult;
        analysisStore.chatMessages = this.chatMessages;
        analysisStore.chatContextPaper = this.chatContextPaper;
    },
};


// =============================================================================
// Add-on routes
// =============================================================================

// The one add/remove routine behind the Add-on routes. "Am I on a removed
// route?" compares route names, not paths — a sub-route with params
// (/<id>/runs/:id) never equals the concrete path. Before the router is
// mounted it only adds: the
// initial navigation resolves against the full table by itself, and a
// replace() now would swallow the deep link the page was loaded with.
function syncRouteRecords(records, removers) {
    // A path whose component changed (an Add-on updated at runtime) is
    // swapped: removed here, added again below.
    const active = new Map(records.map((r) => [r.path, r.component]));
    const curName = router.currentRoute.value ? router.currentRoute.value.name : null;
    let currentRemoved = false;
    for (const path of Object.keys(removers)) {
        if (active.get(path) !== removers[path].component) {
            const entry = removers[path];
            entry.remove();
            delete removers[path];
            if (curName && curName === entry.name && !active.has(path)) currentRemoved = true;
        }
    }
    let added = false;
    for (const r of records) {
        if (!removers[r.path]) {
            removers[r.path] = { remove: router.addRoute(r), name: r.name, component: r.component };
            added = true;
        }
    }
    if (!_routerStarted) return;
    if (currentRemoved) {
        router.push('/');
    } else if (added) {
        // We may just have registered the route the user deep-linked/refreshed
        // onto; re-resolve the current location so it renders instead of
        // staying blank. Only after the initial navigation: before it,
        // currentRoute is the unmatched start location "/" and a replace
        // would overwrite the deep link.
        router.isReady().then(() => {
            if (router.currentRoute.value.matched.length === 0) {
                router.replace(router.currentRoute.value.fullPath);
            }
        }).catch(() => {});
    }
}


// =============================================================================
// Add-on frontends (#187, ADR-0021)
// -----------------------------------------------------------------------------
// The server names what each active Add-on ships (`window.LB_ADDONS` in the
// page, `GET /api/plugins/frontend` at runtime): assets, stylesheet, script,
// locale files, `default_language`, nav route. The boot loads them — assets,
// then stylesheet, then script, locales alongside — within ten seconds per
// Add-on, and only then mounts the router, so a reload on an Add-on route
// renders that page straight away. Add-ons load independently: one that
// throws, times out or breaks its namespace ends in `error`, the rest run.
//
// The script calls `LocalBib.registerPlugin({id, views, subroutes, slots,
// locales})` while it runs. The core enforces the namespace: view keys and
// slot keys `<id>.…`, routes `/<id>` or `/<id>/…`, locale keys `<id>.…` — one
// violation rejects the whole registration (console.error names the key).
// Switching an Add-on off drops routes, nav entry, slots and locale namespace
// but keeps the registration, so switching it on again needs no second script.
//
// Slots (#190) — Add-on Contract surface, same names as `plugin_api.SLOTS`.
// A registration fills a slot with `slots: {'<slot>': {key: '<id>.…',
// component}}`; any other slot name rejects the registration. The core renders
// a slot's component only while its Add-on is active, with these props/emits:
//
//   item-list-filter       Item list filter bar (PaperList).
//     props  { selection }  the current selection, or null
//     emits  update:selection  { value, label, citeKeys: string[] } | null
//     The core keeps one selection per Add-on, sends its `citeKeys` as the
//     existing `cite_keys` parameter of GET /api/papers (intersected when
//     several sources contribute; an empty list means "no Items"), counts
//     each non-null selection in the filter badge, clears them on reset and
//     drops an Add-on's selection when it is switched off.
//   item-detail-aside      Item detail, metadata column (PaperDetail).
//     props  { itemId, citeKey }
//     Keyed on the Item id: navigating to another Item remounts it.
//   research-chat-context  Research Chat, above the conversation.
//     props  { enabled }
//     emits  update:enabled  boolean — the component renders its own toggle
//                            (label from its locale files)
//            update:context  string | null — the text block to contribute
//     While enabled, a non-empty block is appended to the existing
//     `extra_context` list of POST /api/research-chat/ask; disabled, nothing.
//   settings               The Add-on's section in Settings → Add-ons.
//     props  { addonId, fields, values, save }
//       fields/values as GET /api/plugins/{id}/settings answers them (a
//       secret is `{has_key, key_hint}`); `save(values)` PUTs (`null` =
//       unchanged) and resolves to the new values.
//     Without a component the core renders the Manifest's declared fields
//     generically (string, secret, path, bool). Field label: the field's
//     declared `label` key, else `<id>.settings.<key>`, else the key itself.
// Slot texts come from the Add-on's own locale files through `t()`.
// =============================================================================

const ADDON_SLOTS = Object.freeze(['item-list-filter', 'item-detail-aside', 'research-chat-context', 'settings']);
const ADDON_EVENT = 'lb-plugins-changed';
const ADDON_BUDGET_MS = 10000;
const _addonRouteRemovers = {};   // route path -> { remove, name }
let _routerStarted = false;

function emitAddonChange(id, active) {
    window.dispatchEvent(new CustomEvent(ADDON_EVENT, { detail: { id, active } }));
}

function addonIsActive(id) {
    return _addons[id] ? _addons[id].state === 'active' : false;
}

function inNamespace(value, id) {
    return typeof value === 'string' && value.startsWith(id + '.');
}

function routeInNamespace(path, id) {
    return typeof path === 'string' && (path === '/' + id || path.startsWith('/' + id + '/'));
}

function catalogProblem(catalog, id, where) {
    if (!catalog || typeof catalog !== 'object') return where + ' is not an object of key -> text';
    const bad = Object.keys(catalog).find((k) => !inNamespace(k, id));
    return bad ? `locale key "${bad}" (${where}) is outside "${id}."` : null;
}

// What is wrong with a registration, or null. `entry` is the server's load
// list for the same id (its nav route must resolve to a registered view).
function registrationProblem(reg, entry) {
    const id = entry.id;
    const views = reg.views || {};
    if (typeof views !== 'object') return 'views must be an object';
    for (const key of Object.keys(views)) {
        if (!inNamespace(key, id)) return `view key "${key}" is outside "${id}."`;
        if (!views[key] || typeof views[key] !== 'object') return `view "${key}" is not a component`;
    }
    const subroutes = reg.subroutes || [];
    if (!Array.isArray(subroutes)) return 'subroutes must be a list';
    for (const s of subroutes) {
        if (!s || !routeInNamespace(s.path, id)) return `route "${s && s.path}" is outside "/${id}/"`;
        if (!views[s.view]) return `route "${s.path}" names unknown view "${s.view}"`;
    }
    const nav = entry.nav;
    if (nav && !routeInNamespace(nav.route, id)) return `nav route "${nav.route}" is outside "/${id}/"`;
    if (nav && !views[nav.view]) return `nav view "${nav.view}" is not registered`;
    const slots = reg.slots || {};
    if (typeof slots !== 'object') return 'slots must be an object';
    for (const [slot, def] of Object.entries(slots)) {
        if (!ADDON_SLOTS.includes(slot)) return `slot "${slot}" is not one of ${ADDON_SLOTS.join(', ')}`;
        if (!def || !inNamespace(def.key, id)) return `slot "${slot}" key "${def && def.key}" is outside "${id}."`;
        if (!def.component || typeof def.component !== 'object') return `slot "${slot}" has no component`;
    }
    for (const [lang, catalog] of Object.entries(reg.locales || {})) {
        const problem = catalogProblem(catalog, id, 'locales.' + lang);
        if (problem) return problem;
    }
    return null;
}

// Called by an Add-on script while it runs. Returns true when accepted.
function registerPlugin(reg) {
    const id = reg && typeof reg.id === 'string' ? reg.id : '';
    const a = _addons[id];
    if (!a || a.state !== 'loading') {
        console.error(`[LocalBib] registerPlugin: "${id}" is not an Add-on being loaded — ignored`);
        return false;
    }
    if (a.registration || a.rejected) {
        console.error(`[LocalBib] Add-on "${id}": registerPlugin called twice — ignored`);
        return false;
    }
    const problem = registrationProblem(reg, a.entry);
    if (problem) {
        a.rejected = problem;
        console.error(`[LocalBib] Add-on "${id}": registration rejected — ${problem}`);
        return false;
    }
    const views = {};
    for (const [key, comp] of Object.entries(reg.views || {})) views[key] = markRaw(comp);
    const slots = {};
    for (const [slot, def] of Object.entries(reg.slots || {})) {
        slots[slot] = { key: def.key, component: markRaw(def.component) };
    }
    a.registration = {
        views,
        subroutes: (reg.subroutes || []).map((s) => ({ path: s.path, view: s.view })),
        slots,
        locales: reg.locales || {},
    };
    return true;
}

// Script loading is one seam: the browser injects a <script>, the vitest
// setup (tests/js/setup.js) evaluates the fetched text instead — jsdom runs
// no external scripts.
function injectScript(url) {
    if (typeof window.LB_SCRIPT_LOADER === 'function') return window.LB_SCRIPT_LOADER(url);
    return new Promise((resolve, reject) => {
        const el = document.createElement('script');
        el.src = url;
        el.async = false;
        el.onload = () => resolve();
        el.onerror = () => reject(new Error('could not load ' + url));
        document.head.appendChild(el);
    });
}

function injectStylesheet(url) {
    if ([...document.querySelectorAll('link[data-addon-href]')].some((l) => l.getAttribute('data-addon-href') === url)) return;
    const el = document.createElement('link');
    el.rel = 'stylesheet';
    el.href = url;
    el.setAttribute('data-addon-href', url);
    document.head.appendChild(el);
}

async function loadAddonFiles(a) {
    const entry = a.entry;
    const locales = Promise.all(Object.entries(entry.locales || {}).map(async ([lang, url]) => {
        const catalog = await api(url);
        const problem = catalogProblem(catalog, entry.id, url);
        if (problem) throw new Error(problem);
        return [lang, catalog];
    }));
    locales.catch(() => {});  // awaited below; keeps an early rejection from going unhandled
    for (const url of entry.assets || []) {
        if (/\.css(\?|$)/.test(url)) injectStylesheet(url);
        else if (/\.js(\?|$)/.test(url)) await injectScript(url);
    }
    if (entry.stylesheet) injectStylesheet(entry.stylesheet);
    // A throw at the script's top level surfaces as a window error, not as a
    // failed load; catch it for this script's URL.
    let thrown = null;
    const onError = (ev) => {
        if (ev && ev.filename && ev.filename.indexOf(`/plugins/${entry.id}/`) !== -1) thrown = ev.message;
    };
    window.addEventListener('error', onError);
    try {
        await injectScript(entry.script);
    } finally {
        window.removeEventListener('error', onError);
    }
    if (thrown) throw new Error('script error: ' + thrown);
    if (!a.registration) throw new Error(a.rejected || 'the script did not call LocalBib.registerPlugin');
    const catalogs = {};
    for (const [lang, catalog] of await locales) catalogs[lang] = catalog;
    // Inline catalogs from the registration win over the files, key by key.
    for (const [lang, catalog] of Object.entries(a.registration.locales)) {
        catalogs[lang] = Object.assign({}, catalogs[lang] || {}, catalog);
    }
    a.catalogs = catalogs;
}

function withBudget(promise, ms) {
    let timer;
    const timeout = new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`not ready within ${ms / 1000} s`)), ms);
    });
    return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

// Loads one Add-on (or re-activates a retained registration). Never throws:
// a failure is this Add-on's `error` state and a console.error.
async function loadAddon(entry) {
    const known = _addons[entry.id];
    if (known && known.registration && known.entry.version === entry.version) {
        known.entry = entry;
        known.state = 'active';
        return;
    }
    const a = { entry, state: 'loading', error: null, rejected: null, registration: null, catalogs: {} };
    _addons[entry.id] = a;
    try {
        await withBudget(loadAddonFiles(a), ADDON_BUDGET_MS);
        a.state = 'active';
    } catch (e) {
        a.state = 'error';
        a.error = String((e && e.message) || e);
        a.registration = null;
        console.error(`[LocalBib] Add-on "${entry.id}" failed to load: ${a.error}`);
    }
}

function addonRouteRecords() {
    const records = [];
    const seen = new Set();
    for (const [id, a] of Object.entries(_addons)) {
        if (a.state !== 'active' || !a.registration) continue;
        const { views, subroutes } = a.registration;
        const nav = a.entry.nav;
        const wanted = nav ? [{ path: nav.route, view: nav.view }, ...subroutes] : subroutes;
        wanted.forEach((r, i) => {
            if (seen.has(r.path)) return;
            seen.add(r.path);
            records.push({ path: r.path, component: views[r.view], name: `addon-${id}-${i}` });
        });
    }
    return records;
}

function addonStates() {
    return Object.fromEntries(Object.entries(_addons).map(([id, a]) => [id, a.state]));
}

// After any change of Add-on state: routes, re-render, events.
function applyAddons(before) {
    syncRouteRecords(addonRouteRecords(), _addonRouteRemovers);
    addonTick.value++;
    for (const [id, a] of Object.entries(_addons)) {
        const now = a.state === 'active';
        if ((before[id] === 'active') !== now) emitAddonChange(id, now);
    }
}

// Brings the loaded frontends in line with a load list: loads what is new,
// re-activates what was switched back on, deactivates what is gone. A
// deactivated Add-on keeps its registration (its script stays in the DOM).
async function reconcileAddons(list) {
    const before = addonStates();
    const wanted = new Set(list.map((e) => e.id));
    for (const [id, a] of Object.entries(_addons)) {
        if (wanted.has(id)) continue;
        if (a.registration) a.state = 'inactive';
        else delete _addons[id];
    }
    await Promise.all(list.map(loadAddon));
    applyAddons(before);
}

// After the server changed which Add-ons run (switch, install, Zustimmung):
// the same loader runs against its list and the sidebar refreshes — a new
// Add-on's nav entry appears without a reload.
async function refreshAddonFrontends() {
    let list = [];
    try {
        list = (await api('/api/plugins/frontend')).addons || [];
    } catch (e) {
        console.error('Add-on list could not be loaded:', e);
    }
    await reconcileAddons(list);
    window.dispatchEvent(new CustomEvent('refresh-sidebar'));
}

// Runtime switch (the Marketplace card): the server (de)activates, then the
// frontends follow.
async function setAddonEnabled(id, on) {
    const result = await api(`/api/plugins/${encodeURIComponent(id)}/${on ? 'enable' : 'disable'}`, { method: 'POST' });
    await refreshAddonFrontends();
    return result;
}

// The registered slot components of every active Add-on for one slot name,
// in id order: [{ addon, key, component }]. Reactive through `addonTick`.
function addonSlots(name) {
    addonTick.value;
    const out = [];
    for (const id of Object.keys(_addons).sort()) {
        const a = _addons[id];
        const def = a.state === 'active' && a.registration ? a.registration.slots[name] : null;
        if (def) out.push({ addon: id, key: def.key, component: def.component });
    }
    return out;
}

// A sidebar nav item is shown when a view stands behind it: the active
// Add-on registered the view its nav item names.
function navItemVisible(item) {
    addonTick.value;
    const a = _addons[item.id];
    return !!(a && a.state === 'active' && a.registration && a.registration.views[item.view]);
}

// An Add-on nav label is a key of its own namespace; anything else is text.
function navItemLabel(item) {
    return _addons[item.id] && inNamespace(item.label, item.id) ? t(item.label) : item.label;
}

window.LocalBib = Object.freeze({
    Vue,
    registerPlugin,
    isActive: addonIsActive,
    state: (id) => (_addons[id] ? _addons[id].state : 'inactive'),
    slots: addonSlots,
    t: (key, vars) => t(key, vars),
    tn: (key, n, vars) => tn(key, n, vars),
    api: (url, options) => api(url, options),
    events: Object.freeze({ changed: ADDON_EVENT }),
});


// =============================================================================
// Marketplace (#189, ADR-0021)
// -----------------------------------------------------------------------------
// Reads GET /api/marketplace: index entries merged with installed state and
// Dev-Suchpfad Add-ons (services/marketplace.py decides, this view only
// renders). Install (#191): from the index with byte progress (SSE), from a
// file, or a folder as Dev-Suchpfad — each ends in the Zustimmungsdialog
// unless every Berechtigung is agreed to already; agreeing switches the
// Add-on on and its frontend loads live. Lifecycle (#193): the card's switch,
// update (a running Add-on switches on the next start; new Berechtigungen ask
// for the difference first), rollback, removal, the states and the restart
// button; the Add-on's settings live in its slide-over.
// =============================================================================

const MARKETPLACE_FILTERS = ['all', 'installed', 'updates', 'dev'];

// The seven Berechtigungen (CONTEXT.md) with the locale key of their one-
// sentence explanation. Order matches the spec's own list.
const MARKETPLACE_PERMISSION_KEYS = {
    'library.read': 'marketplace.permission.libraryRead',
    'library.write': 'marketplace.permission.libraryWrite',
    'llm': 'marketplace.permission.llm',
    'settings.core': 'marketplace.permission.settingsCore',
    'network': 'marketplace.permission.network',
    'files': 'marketplace.permission.files',
    'storage': 'marketplace.permission.storage',
};

// Whether LocalBib enforces `key`, as the card's index rows say (the pending
// version's own rows first). Unknown counts as declared only — never overclaim.
function permissionEnforced(addon, key) {
    const pending = addon.pending_update ? addon.pending_update.version : '';
    const version = (addon.versions || []).find((v) => v.version === pending);
    const rows = [...((version && version.permissions) || []), ...(addon.permissions || []), ...(addon.installed_permissions || [])];
    const row = rows.find((p) => p.key === key);
    return !!(row && row.enforced);
}

// Whether a downloaded update waits for its Zustimmung: a new Berechtigung,
// or a file the user has not confirmed (even one that declares none).
function pendingNeedsConsent(addon) {
    const p = addon.pending_update;
    return !!(p && ((p.missing && p.missing.length) || p.confirm));
}

function formatAddonSize(bytes) {
    if (bytes === null || bytes === undefined) return '';
    const units = ['B', 'KB', 'MB', 'GB'];
    let n = bytes;
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return (i === 0 ? String(n) : n.toFixed(1)) + ' ' + units[i];
}

// A Python build tag ("cp314-win_amd64") to the version a non-developer
// reads ("3.14") -- the major digit is always a single "3", the rest is the
// minor. Falls back to the raw tag when it does not look like a cp tag.
function pythonTagVersion(tag) {
    const m = /^cp(\d)(\d+)/.exec(tag || '');
    return m ? `${m[1]}.${m[2]}` : (tag || '');
}

// Why the core offers no version of an Add-on (services/marketplace.py's
// `incompatible_detail`, #199) as one sentence a non-developer can act on.
// Empty for an unknown/absent reason -- the caller then falls back to the
// bare "Incompatible" state pill, never a broken translation key.
function incompatibleReasonSentence(reason) {
    if (!reason || !reason.code) return '';
    if (reason.code === 'python') {
        const needed = (reason.needed && reason.needed[0]) || '';
        return t('marketplace.incompatibleReason.python', {
            needed: pythonTagVersion(needed), have: pythonTagVersion(reason.have),
        });
    }
    if (reason.code === 'min_core') {
        return t('marketplace.incompatibleReason.minCore', { needed: reason.needed || '', have: reason.have || '' });
    }
    if (reason.code === 'api_version') {
        return t('marketplace.incompatibleReason.apiVersion');
    }
    return '';
}

// What an installed Add-on's card and slide-over say about its Zustand (#193):
// a failed load (the boot marker's hint on its own), incompatible — with the
// fitting update if the index has one —, a downloaded update waiting for the
// restart or for consent, a removal waiting for the restart.
const AddonLifecycleNotes = {
    name: 'AddonLifecycleNotes',
    props: { addon: { type: Object, required: true } },
    template: `
        <div v-if="notes.length" class="flex flex-col gap-1 text-xs">
            <p v-for="n in notes" :key="n.id" class="px-2 py-1 rounded border border-hairline" :class="n.cls"
               :data-testid="'marketplace-note-' + n.id + '-' + addon.id">{{ n.text }}</p>
        </div>
    `,
    computed: {
        notes() {
            const a = this.addon;
            const out = [];
            const red = 'bg-red-50 text-red-700';
            const amber = 'bg-amber-50 text-amber-700';
            const reason = (e) => (e && e.code ? t(e.code, { id: a.id }) : '');
            if (a.pending_removal) out.push({ id: 'removal', cls: amber, text: t('marketplace.pending.removal') });
            if (a.dev_shadow_disabled) out.push({ id: 'devShadow', cls: amber, text: t('marketplace.devShadowDisabled') });
            if (a.addon_state === 'error' && a.error && a.error.code === 'error.plugins.crashedDuringLoad') {
                out.push({ id: 'bootmarker', cls: red, text: t('marketplace.bootMarker') });
            } else if (a.addon_state === 'error') {
                const detail = [reason(a.error), a.error && a.error.message].filter(Boolean).join(' — ');
                out.push({ id: 'error', cls: red, text: t('marketplace.errorState', { message: detail }) });
            }
            if (a.addon_state === 'incompatible') {
                out.push({ id: 'incompatible', cls: red, text: t('marketplace.incompatibleState', { reason: reason(a.error) }) });
                if (a.update_available && a.offered_version) {
                    out.push({ id: 'offer', cls: amber, text: t('marketplace.incompatibleUpdate', { version: a.offered_version }) });
                }
            }
            // Not (yet) installed and nothing fits (#199): say why, not just
            // "Incompatible" -- e.g. which Python build the artifact needs.
            const reasonSentence = incompatibleReasonSentence(a.incompatible_reason);
            if (reasonSentence) out.push({ id: 'incompatibleReason', cls: red, text: reasonSentence });
            if (a.pending_update) {
                const key = pendingNeedsConsent(a) ? 'marketplace.pending.consent' : 'marketplace.pending.update';
                out.push({ id: 'pending', cls: amber, text: t(key, { version: a.pending_update.version }) });
            }
            return out;
        },
    },
};

const MarketplaceCard = {
    name: 'MarketplaceCard',
    components: { AddonLifecycleNotes },
    props: {
        addon: { type: Object, required: true },
        busy: { type: Boolean, default: false },
    },
    emits: ['open', 'action', 'toggle'],
    template: `
        <div role="button" tabindex="0" class="text-left border border-hairline rounded-lg p-4 flex flex-col gap-2 hover:shadow-md transition-shadow bg-bg-elev cursor-pointer"
             :data-testid="'marketplace-card-' + addon.id" @click="$emit('open', addon)" @keydown.enter="$emit('open', addon)">
            <div class="flex items-start gap-3">
                <img v-if="addon.icon" :src="iconUrl" alt="" class="w-10 h-10 rounded object-cover flex-shrink-0"
                     @error="iconFailed = true" v-show="!iconFailed" />
                <span v-if="!addon.icon || iconFailed" class="w-10 h-10 rounded flex items-center justify-center bg-bg-soft text-mute flex-shrink-0" v-html="icons.puzzle"></span>
                <div class="min-w-0 flex-1">
                    <div class="font-semibold text-ink truncate">{{ addon.name }}</div>
                    <div class="text-xs text-ink-3 truncate">{{ addon.tagline }}</div>
                </div>
                <!-- The switch (#193): on/off without a reload -->
                <button v-if="switchable" type="button" role="switch" :aria-checked="addon.enabled ? 'true' : 'false'"
                        :aria-label="$t('marketplace.switch.label', { name: addon.name })" :disabled="busy"
                        class="lb-switch flex-shrink-0 relative inline-flex items-center h-5 w-9 rounded-full transition-colors"
                        :class="addon.enabled ? 'bg-accent' : 'bg-hairline'"
                        :data-testid="'marketplace-toggle-' + addon.id" @click.stop="$emit('toggle', addon)">
                    <span class="inline-block w-4 h-4 bg-white rounded-full shadow transform transition-transform"
                          :class="addon.enabled ? 'translate-x-4' : 'translate-x-0.5'"></span>
                </button>
            </div>
            <addon-lifecycle-notes :addon="addon" />

            <div class="flex flex-wrap gap-1.5 items-center text-xs">
                <span v-if="addon.trust" class="lb-chip" :data-testid="'marketplace-trust-' + addon.id">
                    {{ $t(addon.trust === 'official' ? 'marketplace.trust.official' : 'marketplace.trust.thirdParty') }}
                </span>
                <span v-if="addon.languages.length" class="lb-chip">{{ addon.languages.join(', ') }}</span>
                <span v-if="addon.size != null" class="lb-chip">{{ sizeText }}</span>
                <span v-if="addon.requires_source" class="lb-chip" :title="$t('marketplace.requiresSource')">{{ $t('marketplace.badge.requiresSource') }}</span>
            </div>
            <div class="mt-auto pt-2 flex items-center justify-between gap-2">
                <span class="text-xs font-medium" :class="stateClass" :data-testid="'marketplace-state-' + addon.id">
                    {{ $t(stateKey) }}
                </span>
                <button v-if="action" type="button" class="lb-btn lb-btn-primary text-xs" :disabled="busy"
                        :data-testid="'marketplace-button-' + addon.id" @click.stop="$emit('action', addon, action)">
                    {{ $t(buttonKey) }}
                </button>
                <span v-else class="px-3 py-1.5 rounded text-xs font-medium border cursor-default select-none"
                      :class="buttonClass" :data-testid="'marketplace-button-' + addon.id">
                    {{ $t(buttonKey) }}
                </span>
            </div>
        </div>
    `,
    data() {
        return { icons, iconFailed: false };
    },
    computed: {
        iconUrl() {
            return '/api/marketplace/assets/' + this.addon.icon.split('/').map(encodeURIComponent).join('/');
        },
        sizeText() {
            return formatAddonSize(this.addon.size);
        },
        stateKey() {
            const map = {
                not_installed: 'marketplace.state.notInstalled',
                installed: 'marketplace.state.installed',
                update_available: 'marketplace.state.updateAvailable',
                dev: 'marketplace.state.dev',
                incompatible: 'marketplace.state.incompatible',
                installed_incompatible: 'marketplace.state.installedIncompatible',
            };
            return map[this.addon.state] || 'marketplace.state.notInstalled';
        },
        stateClass() {
            if (this.addon.state === 'update_available') return 'text-amber-600';
            if (this.addon.state === 'incompatible' || this.addon.state === 'installed_incompatible') return 'text-red-600';
            if (this.addon.state === 'installed') return 'text-emerald-600';
            return 'text-ink-3';
        },
        // An installed Add-on that may run here has a switch; a queued
        // removal or an incompatible Bundle has none.
        switchable() {
            const a = this.addon;
            return !!(a.installed && a.addon_state && !a.pending_removal && a.addon_state !== 'incompatible');
        },
        // What the primary button does (#191, #193): install a compatible,
        // not yet installed Add-on; reopen the Zustimmung of one that still
        // waits for it (or of a downloaded update); update to the offered
        // version. Everything else only shows the Zustand.
        action() {
            const a = this.addon;
            if (a.pending_removal) return null;
            if (a.addon_state === 'consent_pending') return 'consent';
            // A file or folder that was never confirmed stays off until it is.
            if (!a.enabled && a.needs_confirmation && (a.origin === 'file' || a.origin === 'dev')) return 'consent';
            if (pendingNeedsConsent(a)) return 'updateConsent';
            if (a.update_available && a.in_index && !a.requires_source) return 'update';
            if (a.state === 'not_installed' && !a.requires_source && a.in_index) return 'install';
            return null;
        },
        buttonKey() {
            if (this.action === 'consent') return 'marketplace.button.consent';
            if (this.action === 'updateConsent') return 'marketplace.button.updateConsent';
            const map = {
                not_installed: 'marketplace.button.install',
                installed: 'marketplace.button.installed',
                update_available: 'marketplace.button.update',
                dev: 'marketplace.button.dev',
                incompatible: 'marketplace.button.incompatible',
                installed_incompatible: 'marketplace.button.incompatible',
            };
            return map[this.addon.state] || 'marketplace.button.install';
        },
        buttonClass() {
            if (this.addon.state === 'update_available') return 'border-amber-200 text-amber-700 bg-amber-50';
            if (this.addon.state === 'incompatible' || this.addon.state === 'installed_incompatible') return 'border-red-100 text-red-600 bg-red-50';
            if (this.addon.state === 'installed') return 'border-hairline text-emerald-700 bg-emerald-50';
            return 'border-hairline text-ink-3 bg-bg-soft';
        },
    },
};

// The Zustimmungsdialog (#191, CONTEXT.md "Zustimmung"). `request` is the
// server's consent request: {id, name, version, trust, origin, sha256,
// permissions: [{key, enforced}], missing}. Each Berechtigung is one sentence
// with its durchgesetzt/erklaert label; the one sentence about the app's
// rights stands first for a Drittanbieter and last for an Offiziell Add-on.
// A file install repeats the warning and shows the computed checksum.
const AddonConsentDialog = {
    name: 'AddonConsentDialog',
    props: {
        request: { type: Object, required: true },
        busy: { type: Boolean, default: false },
        error: { type: String, default: '' },
    },
    emits: ['accept', 'decline'],
    template: `
        <div class="lb-modal-overlay" data-testid="consent-dialog">
            <div class="lb-modal" style="max-width:520px" role="dialog" aria-modal="true">
                <div class="lb-modal-head">
                    <h3 class="text-lg font-semibold" data-testid="consent-title">{{ request.update
                        ? $t('marketplace.consent.updateTitle', { name: request.name, version: request.version })
                        : $t('marketplace.consent.title', { name: request.name }) }}</h3>
                </div>
                <div class="lb-modal-main" data-testid="consent-body">
                    <div class="flex flex-wrap gap-1.5 items-center text-xs mb-3">
                        <span class="lb-chip" data-testid="consent-trust">{{ $t(official ? 'marketplace.trust.official' : 'marketplace.trust.thirdParty') }}</span>
                        <span v-if="request.version" class="lb-chip">{{ request.version }}</span>
                        <span v-if="request.origin === 'dev'" class="lb-chip">{{ $t('marketplace.state.dev') }}</span>
                    </div>
                    <div v-if="request.origin === 'file'" class="mb-3 text-xs px-3 py-2 rounded bg-amber-50 text-amber-700 border border-hairline"
                         data-testid="consent-file-warning">{{ $t('marketplace.file.warning') }}</div>
                    <p v-if="request.sha256" class="mb-3 text-xs text-ink-3" data-testid="consent-sha256">
                        {{ $t('marketplace.file.checksum') }} <code class="break-all">{{ request.sha256 }}</code>
                    </p>
                    <p v-if="!official" class="mb-3 text-sm font-medium text-ink" data-testid="consent-rights">{{ $t('marketplace.permissions.allRights') }}</p>
                    <p class="text-sm text-ink-3 mb-2">{{ $t(request.update ? 'marketplace.consent.updateIntro'
                        : (request.permissions.length ? 'marketplace.consent.intro' : 'marketplace.consent.none')) }}</p>
                    <ul class="text-sm space-y-1.5 mb-3" data-testid="consent-permissions">
                        <li v-for="p in request.permissions" :key="p.key" class="flex items-start justify-between gap-2"
                            :data-testid="'consent-permission-' + p.key">
                            <span>{{ $t(permissionLabel(p.key)) }}</span>
                            <span class="lb-chip flex-shrink-0">{{ $t(p.enforced ? 'marketplace.permissions.enforced' : 'marketplace.permissions.declared') }}</span>
                        </li>
                    </ul>
                    <p v-if="official" class="mb-3 text-xs text-ink-3" data-testid="consent-rights">{{ $t('marketplace.permissions.allRights') }}</p>
                    <p v-if="error" class="mb-3 text-xs text-red-600" data-testid="consent-error">{{ error }}</p>
                    <div class="flex justify-end gap-2 pt-2">
                        <button type="button" class="lb-btn lb-btn-ghost" :disabled="busy" data-testid="consent-decline" @click="$emit('decline')">
                            {{ $t('marketplace.consent.decline') }}
                        </button>
                        <button type="button" class="lb-btn lb-btn-primary" :disabled="busy" data-testid="consent-accept" @click="$emit('accept')">
                            {{ $t(request.staged ? 'marketplace.consent.updateAccept' : 'marketplace.consent.accept') }}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `,
    computed: {
        official() {
            return this.request.trust === 'official';
        },
    },
    methods: {
        permissionLabel(key) {
            return MARKETPLACE_PERMISSION_KEYS[key] || key;
        },
    },
};

const MarketplacePage = {
    name: 'MarketplacePage',
    components: { MarketplaceCard, AddonConsentDialog, AddonLifecycleNotes, AddonSettingsSection },
    template: `
        <div class="p-6 max-w-6xl mx-auto">
            <div class="flex items-baseline justify-between gap-2 mb-1 flex-wrap">
                <h2 class="text-xl font-semibold text-ink">{{ $t('marketplace.title') }}</h2>
                <div class="flex gap-2">
                    <button type="button" class="lb-btn lb-btn-ghost text-xs" data-testid="marketplace-from-file" @click="openPanel('file')">
                        {{ $t('marketplace.file.open') }}
                    </button>
                    <button type="button" class="lb-btn lb-btn-ghost text-xs" data-testid="marketplace-from-folder" @click="openPanel('folder')">
                        {{ $t('marketplace.folder.open') }}
                    </button>
                </div>
            </div>
            <p class="text-sm text-ink-3 mb-4">{{ $t('marketplace.subtitle') }}</p>

            <div v-if="offline && hasIndex" class="mb-4 text-xs px-3 py-2 rounded bg-amber-50 text-amber-700 border border-hairline"
                 data-testid="marketplace-offline-notice">
                {{ $t('marketplace.offlineNotice', { date: indexDateText }) }}
            </div>

            <div v-if="progress" class="mb-4 px-3 py-2 rounded border border-hairline bg-bg-soft text-sm" data-testid="marketplace-progress">
                <div class="flex items-center justify-between gap-2">
                    <span>{{ progressText }}</span>
                    <button v-if="progress.error" type="button" class="lb-btn lb-btn-text text-xs" @click="progress = null">{{ $t('common.close') }}</button>
                </div>
                <div v-if="!progress.error" class="h-1.5 mt-2 rounded bg-hairline overflow-hidden">
                    <div class="h-full bg-accent transition-all" :style="{ width: progressPercent + '%' }"></div>
                </div>
                <p v-if="progress.error" class="text-xs text-red-600 mt-1" data-testid="marketplace-progress-error">{{ progress.error }}</p>
            </div>

            <!-- After an update, a rollback or a removal (#193): the restart -->
            <div v-if="restart" class="mb-4 px-3 py-2 rounded border border-hairline bg-sky-50 text-sky-800 text-sm flex flex-wrap items-center justify-between gap-2"
                 data-testid="marketplace-restart-banner">
                <span>{{ $t(restart === 'running' ? 'marketplace.restart.running' : 'marketplace.restart.needed') }}</span>
                <button v-if="restart === 'needed'" type="button" class="lb-btn lb-btn-primary text-xs" data-testid="marketplace-restart"
                        @click="restartNow">{{ $t('marketplace.restart.now') }}</button>
                <p v-if="restart === 'unavailable'" class="w-full text-xs" data-testid="marketplace-restart-hint">{{ $t('marketplace.restart.unavailable') }}</p>
                <p v-if="restartError" class="w-full text-xs text-red-600">{{ restartError }}</p>
            </div>

            <!-- Install from a file / load from a folder (#191) -->
            <div v-if="panel === 'file'" class="mb-4 p-3 rounded border border-hairline bg-bg-elev text-sm" data-testid="marketplace-file-panel">
                <p class="mb-2 text-xs px-3 py-2 rounded bg-amber-50 text-amber-700 border border-hairline" data-testid="marketplace-file-warning">
                    {{ $t('marketplace.file.warning') }}
                </p>
                <div class="flex flex-wrap items-center gap-2">
                    <input type="file" accept=".zip" data-testid="marketplace-file-input" @change="onFilePicked" />
                    <button type="button" class="lb-btn lb-btn-primary text-xs" :disabled="!pickedFile || panelBusy"
                            data-testid="marketplace-file-install" @click="installFromFile">{{ $t('marketplace.button.install') }}</button>
                    <button type="button" class="lb-btn lb-btn-text text-xs" @click="panel = null">{{ $t('common.cancel') }}</button>
                </div>
                <p v-if="panelError" class="text-xs text-red-600 mt-2" data-testid="marketplace-panel-error">{{ panelError }}</p>
            </div>
            <div v-if="panel === 'folder'" class="mb-4 p-3 rounded border border-hairline bg-bg-elev text-sm" data-testid="marketplace-folder-panel">
                <p class="mb-2 text-xs text-ink-3">{{ $t('marketplace.folder.hint') }}</p>
                <div class="flex flex-wrap items-center gap-2">
                    <input type="text" class="lb-input flex-1 min-w-0" v-model="folderPath" :placeholder="$t('marketplace.folder.placeholder')"
                           data-testid="marketplace-folder-input" @keydown.enter="loadFromFolder" />
                    <button type="button" class="lb-btn lb-btn-primary text-xs" :disabled="!folderPath.trim() || panelBusy"
                            data-testid="marketplace-folder-load" @click="loadFromFolder">{{ $t('marketplace.folder.load') }}</button>
                    <button type="button" class="lb-btn lb-btn-text text-xs" @click="panel = null">{{ $t('common.cancel') }}</button>
                </div>
                <p v-if="panelError" class="text-xs text-red-600 mt-2" data-testid="marketplace-panel-error">{{ panelError }}</p>
            </div>

            <div class="flex flex-wrap gap-2 mb-5" data-testid="marketplace-filters">
                <button v-for="f in filters" :key="f" type="button"
                        class="lb-chip" :class="{ 'is-on': filter === f }"
                        :data-testid="'marketplace-filter-' + f"
                        @click="filter = f">
                    {{ $t('marketplace.filter.' + f) }}
                    <span class="lb-chip-n">{{ counts[f] }}</span>
                </button>
            </div>

            <div v-if="loading" class="text-sm text-ink-3 py-10 text-center">{{ $t('marketplace.loading') }}</div>
            <div v-else-if="!hasIndex && !addons.length" class="text-sm text-ink-3 py-16 text-center" data-testid="marketplace-no-index">
                {{ $t('marketplace.noIndexYet') }}
            </div>
            <div v-else-if="!filteredAddons.length" class="text-sm text-ink-3 py-16 text-center" data-testid="marketplace-empty">
                {{ $t('marketplace.empty.' + filter) }}
            </div>
            <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4" data-testid="marketplace-grid">
                <marketplace-card v-for="a in filteredAddons" :key="a.id" :addon="a" :busy="!!(progress && !progress.error) || switching === a.id"
                                  @open="openDetail" @action="onCardAction" @toggle="toggle" />
            </div>

            <addon-consent-dialog v-if="consent" :request="consent" :busy="consentBusy" :error="consentError"
                                  @accept="acceptConsent" @decline="declineConsent" />

            <!-- Slide-over -->
            <div v-if="selected" class="lb-modal-overlay" data-testid="marketplace-slideover" @click.self="closeDetail">
                <div class="lb-modal" style="max-width:520px;margin-left:auto">
                    <div class="lb-modal-head">
                        <h3 class="text-lg font-semibold" data-testid="marketplace-slideover-name">{{ selected.name }}</h3>
                        <div class="lb-modal-actions">
                            <button class="lb-modal-close" @click="closeDetail" :title="$t('common.close')" data-testid="marketplace-slideover-close">
                                <span v-html="icons.x"></span>
                            </button>
                        </div>
                    </div>
                    <div class="lb-modal-main">
                        <p class="text-sm text-ink-3 mb-3">{{ selected.tagline }}</p>

                        <div class="flex flex-wrap gap-1.5 items-center text-xs mb-4">
                            <span v-if="selected.trust" class="lb-chip">{{ $t(selected.trust === 'official' ? 'marketplace.trust.official' : 'marketplace.trust.thirdParty') }}</span>
                            <span v-if="selected.languages.length" class="lb-chip">{{ selected.languages.join(', ') }}</span>
                            <span v-if="selected.author" class="lb-chip">{{ $t('marketplace.author') }}: {{ selected.author }}</span>
                        </div>

                        <div v-if="selected.requires_source" class="mb-4 text-xs px-3 py-2 rounded bg-sky-50 text-sky-700 border border-hairline"
                             data-testid="marketplace-requires-source">
                            {{ $t('marketplace.requiresSource') }}
                        </div>
                        <div v-if="selected.installed_not_in_index" class="mb-4 text-xs px-3 py-2 rounded bg-red-50 text-red-700 border border-hairline">
                            {{ $t('marketplace.installedNotInIndex') }}
                        </div>
                        <div class="mb-4" data-testid="marketplace-slideover-state"><addon-lifecycle-notes :addon="selected" /></div>

                        <!-- Lifecycle (#193): update, rollback, remove -->
                        <div v-if="selected.installed" class="mb-5 flex flex-wrap items-center gap-2" data-testid="marketplace-lifecycle">
                            <span v-if="selected.installed_version" class="lb-chip">{{ $t('marketplace.installedVersion', { version: selected.installed_version }) }}</span>
                            <button v-if="selected.update_available && selected.in_index && !selected.pending_removal" type="button"
                                    class="lb-btn lb-btn-primary text-xs" data-testid="marketplace-slideover-update"
                                    @click="install(selected)">{{ $t('marketplace.button.update') }}</button>
                            <button v-if="selected.rollback_available && selected.source === 'bundle' && !selected.pending_removal" type="button"
                                    class="lb-btn lb-btn-ghost text-xs" data-testid="marketplace-rollback" :disabled="lifecycleBusy"
                                    @click="rollback(selected)">{{ $t('marketplace.rollback', { version: selected.previous_version }) }}</button>
                            <p v-if="selected.dev_origin === 'env'" class="w-full text-xs text-ink-3" data-testid="marketplace-dev-env-note">
                                {{ $t('marketplace.devPathFromEnv') }}
                            </p>
                            <template v-else-if="!selected.pending_removal">
                                <button v-if="!confirmRemove" type="button" class="lb-btn lb-btn-ghost text-xs text-red-600"
                                        data-testid="marketplace-remove" @click="confirmRemove = true">{{ $t('marketplace.remove') }}</button>
                                <span v-else class="flex flex-wrap items-center gap-2 text-xs">
                                    <span>{{ $t('marketplace.remove.confirm', { name: selected.name }) }}</span>
                                    <button type="button" class="lb-btn lb-btn-primary text-xs" data-testid="marketplace-remove-confirm"
                                            :disabled="lifecycleBusy" @click="remove(selected)">{{ $t('marketplace.remove.yes') }}</button>
                                    <button type="button" class="lb-btn lb-btn-text text-xs" @click="confirmRemove = false">{{ $t('common.cancel') }}</button>
                                </span>
                            </template>
                            <p v-if="lifecycleError" class="w-full text-xs text-red-600" data-testid="marketplace-lifecycle-error">{{ lifecycleError }}</p>
                        </div>

                        <!-- The Add-on's own settings (moved here from Settings, #193) -->
                        <div v-if="selected.installed && !selected.pending_removal" class="mb-5" data-testid="marketplace-settings">
                            <addon-settings-section :key="selected.id + ':' + selected.installed_version" :addon-id="selected.id" />
                        </div>

                        <div v-if="selected.description" class="mb-5">
                            <h4 class="text-xs font-semibold uppercase text-mute mb-1">{{ $t('marketplace.description') }}</h4>
                            <div class="lb-md text-sm" v-html="renderMarkdown(selected.description)"></div>
                        </div>

                        <div v-if="selected.screenshots.length" class="mb-5">
                            <h4 class="text-xs font-semibold uppercase text-mute mb-2">{{ $t('marketplace.screenshots') }}</h4>
                            <div class="flex gap-2 overflow-x-auto">
                                <img v-for="(s, i) in selected.screenshots" :key="i" :src="assetUrl(s)"
                                     class="h-32 rounded border border-hairline object-cover" alt="" />
                            </div>
                        </div>

                        <div class="mb-5">
                            <h4 class="text-xs font-semibold uppercase text-mute mb-2">{{ $t('marketplace.permissions') }}</h4>
                            <p class="text-xs text-ink-3 mb-2">{{ $t('marketplace.permissions.allRights') }}</p>
                            <ul class="text-sm space-y-1">
                                <li v-for="p in selected.permissions" :key="p.key" class="flex items-start justify-between gap-2">
                                    <span>{{ $t(permissionLabel(p.key)) }}</span>
                                    <span class="lb-chip flex-shrink-0" :data-testid="'marketplace-permission-' + p.key">
                                        {{ $t(p.enforced ? 'marketplace.permissions.enforced' : 'marketplace.permissions.declared') }}
                                    </span>
                                </li>
                            </ul>
                        </div>

                        <div v-if="selected.versions.length" class="mb-2">
                            <h4 class="text-xs font-semibold uppercase text-mute mb-2">{{ $t('marketplace.versions') }}</h4>
                            <ul class="text-sm space-y-2">
                                <li v-for="v in selected.versions" :key="v.version" class="border-b border-hairline pb-2 last:border-0">
                                    <div class="flex items-center justify-between">
                                        <span class="font-medium">{{ v.version }}</span>
                                        <span class="text-xs text-mute">{{ v.released }}</span>
                                    </div>
                                    <p v-if="v.changelog" class="text-xs text-ink-3 mt-0.5">{{ v.changelog }}</p>
                                </li>
                            </ul>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `,
    data() {
        return {
            icons,
            loading: true,
            addons: [],
            offline: false,
            hasIndex: false,
            indexDate: null,
            filter: 'all',
            filters: MARKETPLACE_FILTERS,
            selectedId: null,     // the slide-over follows the card across reloads
            progress: null,       // {id, name, step, received, total, error}
            restart: null,        // null | 'needed' | 'running' | 'unavailable' (#193)
            restartError: '',
            switching: null,      // id whose switch is on its way
            lifecycleBusy: false,
            lifecycleError: '',
            confirmRemove: false,
            consent: null,        // the consent request the dialog shows
            consentBusy: false,
            consentError: '',
            panel: null,          // 'file' | 'folder'
            panelBusy: false,
            panelError: '',
            pickedFile: null,
            folderPath: '',
        };
    },
    computed: {
        selected() {
            return this.selectedId ? this.addons.find((a) => a.id === this.selectedId) || null : null;
        },
        progressPercent() {
            const p = this.progress;
            if (!p) return 0;
            if (p.step !== 'download') return 100;
            return p.total ? Math.min(100, Math.round((p.received / p.total) * 100)) : 0;
        },
        progressText() {
            const p = this.progress;
            if (!p) return '';
            if (p.error) return this.$t('marketplace.install.failed', { name: p.name });
            if (p.step === 'download') {
                return this.$t('marketplace.install.downloading', {
                    name: p.name,
                    received: formatAddonSize(p.received || 0),
                    total: p.total ? formatAddonSize(p.total) : '?',
                });
            }
            return this.$t('marketplace.install.step.' + p.step, { name: p.name });
        },
        indexDateText() {
            if (!this.indexDate) return '';
            try {
                return new Date(this.indexDate).toLocaleString(this.$locale());
            } catch (e) { return this.indexDate; }
        },
        counts() {
            return {
                all: this.addons.length,
                installed: this.addons.filter((a) => a.installed).length,
                updates: this.addons.filter((a) => a.state === 'update_available').length,
                dev: this.addons.filter((a) => a.source === 'dev').length,
            };
        },
        filteredAddons() {
            if (this.filter === 'installed') return this.addons.filter((a) => a.installed);
            if (this.filter === 'updates') return this.addons.filter((a) => a.state === 'update_available');
            if (this.filter === 'dev') return this.addons.filter((a) => a.source === 'dev');
            return this.addons;
        },
    },
    methods: {
        async load() {
            // Only the first load shows the placeholder; a refresh after a
            // switch or an update keeps the grid in place.
            if (!this.addons.length) this.loading = true;
            try {
                const data = await api('/api/marketplace');
                this.addons = data.addons || [];
                this.offline = !!data.offline;
                this.hasIndex = !!data.has_index;
                this.indexDate = data.index_date || null;
            } catch (e) {
                this.addons = [];
                this.hasIndex = false;
            } finally {
                this.loading = false;
            }
        },
        openDetail(addon) {
            this.selectedId = addon.id;
            this.confirmRemove = false;
            this.lifecycleError = '';
        },
        onCardAction(addon, action) {
            if (action === 'install' || action === 'update') this.install(addon);
            else if (action === 'consent') this.reviewConsent(addon);
            else if (action === 'updateConsent') this.reviewUpdateConsent(addon);
        },
        // The card's switch (#193). Switching on an Add-on whose Berechtigungen
        // are not all agreed to opens the Zustimmung first.
        async toggle(addon) {
            if (!addon.enabled && ((addon.missing_consent && addon.missing_consent.length) || addon.needs_confirmation)) {
                this.reviewConsent(addon);
                return;
            }
            this.switching = addon.id;
            try {
                await setAddonEnabled(addon.id, !addon.enabled);
            } catch (e) {
                this.lifecycleError = e.message;
            } finally {
                this.switching = null;
            }
            await this.load();
        },
        async rollback(addon) {
            await this.lifecycleCall(addon, 'rollback');
        },
        async remove(addon) {
            await this.lifecycleCall(addon, 'remove');
            this.confirmRemove = false;
        },
        // Rollback and removal switch the server's state at once (a removal
        // also switches the Add-on off, so its frontend goes now); what needs
        // the restart says so and the banner offers it.
        async lifecycleCall(addon, what) {
            this.lifecycleBusy = true;
            this.lifecycleError = '';
            try {
                const result = await api(`/api/plugins/${encodeURIComponent(addon.id)}/${what}`, { method: 'POST' });
                if (result.restart_required) this.restart = 'needed';
                await refreshAddonFrontends();
            } catch (e) {
                this.lifecycleError = e.message;
            } finally {
                this.lifecycleBusy = false;
            }
            await this.load();
        },
        // "Jetzt neu starten": the frozen app restarts itself and this page
        // reloads once the new process answers; from source the server says
        // it cannot, and the hint says what to do instead.
        async restartNow() {
            this.restartError = '';
            try {
                const resp = await fetch('/api/app/restart', { method: 'POST' });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    const code = err.detail && err.detail.code;
                    if (code === 'error.restart_unavailable') {
                        this.restart = 'unavailable';
                        return;
                    }
                    throw new Error(translateDetail(err.detail, resp.status));
                }
                this.restart = 'running';
                setTimeout(() => this.waitForRestart(Date.now()), 1500);
            } catch (e) {
                this.restartError = e.message;
            }
        },
        async waitForRestart(started) {
            try {
                const resp = await fetch('/api/marketplace');
                if (resp.ok) {
                    window.location.reload();
                    return;
                }
            } catch (e) { /* not up yet */ }
            if (Date.now() - started < 60000) setTimeout(() => this.waitForRestart(started), 1000);
        },
        // Download with byte progress (SSE), then the Zustimmung — or, when
        // every Berechtigung is already agreed to, straight to live.
        async install(addon) {
            this.progress = { id: addon.id, name: addon.name, step: 'download', received: 0, total: addon.size, error: null };
            let done = null;
            try {
                const resp = await fetch(`/api/marketplace/install/${encodeURIComponent(addon.id)}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ version: addon.offered_version || '' }),
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(translateDetail(err.detail, resp.status));
                }
                await readSseStream(resp, (ev) => {
                    if (ev.type === 'progress') {
                        this.progress.step = ev.step;
                        if (ev.received !== undefined) this.progress.received = ev.received;
                        if (ev.total) this.progress.total = ev.total;
                    } else if (ev.type === 'complete') {
                        done = ev;
                    } else if (ev.type === 'error') {
                        this.progress.error = this.$t(ev.code || 'error.marketplace.installFailed', ev.params || {});
                    }
                });
                if (!done && !this.progress.error) this.progress.error = this.$t('error.marketplace.installFailed', { message: '' });
            } catch (e) {
                this.progress.error = e.message;
            }
            if (done) {
                this.progress = null;
                await this.afterInstall(done);
            }
            await this.load();
        },
        // After an install or an update: the Zustimmung if one is needed (for
        // an update only the difference), the restart banner if the new
        // version waits for the next start, else the frontends follow live.
        async afterInstall(result) {
            const update = result.update;
            if (update && update.staged) {
                if (update.missing.length || update.confirm) {
                    this.consentError = '';
                    this.consent = result.consent;
                } else {
                    this.restart = 'needed';
                }
                return;
            }
            // A file or folder always asks once, even with no Berechtigung.
            if ((result.addon && result.addon.state === 'consent_pending') || (result.consent && result.consent.confirm)) {
                this.consentError = '';
                this.consent = result.consent;
            } else {
                await refreshAddonFrontends();
            }
        },
        // A downloaded update that still waits for consent reopens its dialog:
        // only the Berechtigungen it adds.
        reviewUpdateConsent(addon) {
            const indexed = addon.in_index && addon.source !== 'dev';
            const missing = addon.pending_update.missing;
            const fromFile = addon.pending_update.origin === 'file';
            this.consentError = '';
            this.consent = {
                id: addon.id,
                name: addon.name,
                version: addon.pending_update.version,
                trust: indexed && !fromFile ? addon.trust : 'third-party',
                origin: addon.pending_update.origin || 'index',
                sha256: addon.pending_update.sha256 || '',
                update: true,
                staged: true,
                permissions: missing.map((key) => ({ key, enforced: permissionEnforced(addon, key) })),
                missing,
            };
        },
        // A card whose Add-on still waits for its Zustimmung reopens the dialog.
        reviewConsent(addon) {
            const origin = addon.origin || (addon.source === 'dev' ? 'dev' : 'index');
            const indexed = addon.in_index && origin === 'index';
            this.consentError = '';
            this.consent = {
                id: addon.id,
                name: addon.name,
                version: addon.installed_version,
                trust: indexed ? addon.trust : 'third-party',
                origin,
                sha256: addon.sha256 || '',
                permissions: addon.installed_permissions || [],
                missing: [],
            };
        },
        async acceptConsent() {
            const c = this.consent;
            this.consentBusy = true;
            this.consentError = '';
            try {
                const body = JSON.stringify({ permissions: c.permissions.map((p) => p.key) });
                if (c.staged) {
                    // A downloaded update of a running Add-on: agreeing lets it
                    // become active on the next start; the old one runs on.
                    await api(`/api/plugins/${encodeURIComponent(c.id)}/update/consent`, { method: 'POST', body });
                    this.restart = 'needed';
                } else {
                    await api(`/api/plugins/${encodeURIComponent(c.id)}/consent`, { method: 'POST', body });
                    await setAddonEnabled(c.id, true);
                }
                this.consent = null;
            } catch (e) {
                this.consentError = e.message;
            } finally {
                this.consentBusy = false;
            }
            await this.load();
        },
        // Declining keeps the Bundle installed and inactive — its Zustimmung
        // stays open and the card offers the dialog again.
        declineConsent() {
            this.consent = null;
            this.load();
        },
        openPanel(which) {
            this.panel = this.panel === which ? null : which;
            this.panelError = '';
            this.pickedFile = null;
        },
        onFilePicked(ev) {
            this.pickedFile = (ev.target.files && ev.target.files[0]) || null;
        },
        async installFromFile() {
            if (!this.pickedFile) return;
            this.panelBusy = true;
            this.panelError = '';
            try {
                const form = new FormData();
                form.append('file', this.pickedFile);
                const resp = await fetch('/api/marketplace/install-file', { method: 'POST', body: form });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(translateDetail(err.detail, resp.status));
                }
                const result = await resp.json();
                this.panel = null;
                await this.afterInstall(result);
            } catch (e) {
                this.panelError = e.message;
            } finally {
                this.panelBusy = false;
            }
            await this.load();
        },
        async loadFromFolder() {
            const path = this.folderPath.trim();
            if (!path) return;
            this.panelBusy = true;
            this.panelError = '';
            try {
                const result = await api('/api/marketplace/dev-path', { method: 'POST', body: JSON.stringify({ path }) });
                this.panel = null;
                this.folderPath = '';
                this.filter = 'dev';
                await this.afterInstall(result);
            } catch (e) {
                this.panelError = e.message;
            } finally {
                this.panelBusy = false;
            }
            await this.load();
        },
        closeDetail() {
            this.selectedId = null;
        },
        assetUrl(relPath) {
            return '/api/marketplace/assets/' + relPath.split('/').map(encodeURIComponent).join('/');
        },
        permissionLabel(key) {
            return MARKETPLACE_PERMISSION_KEYS[key] || key;
        },
        renderMarkdown(text) {
            return renderMarkdownSafe(text);
        },
    },
    mounted() {
        this.load();
    },
};


// =============================================================================
// Router
// =============================================================================

const routes = [
    { path: '/', component: PaperList, name: 'home' },
    { path: '/category/:id', component: PaperList, name: 'category' },
    { path: '/paper/:id', component: PaperDetail, name: 'paper' },
    { path: '/import', component: ImportPage, name: 'import' },
    { path: '/migrate', component: MigratePage, name: 'migrate' },
    { path: '/analyse', component: AnalysePage, name: 'analyse' },
    { path: '/marketplace', component: MarketplacePage, name: 'marketplace' },
    { path: '/settings', component: SettingsPage, name: 'settings' },
    { path: '/thesis', component: ThesisPage, name: 'thesis' },
    { path: '/research-chat', component: ResearchChatPage, name: 'research-chat' },
    { path: '/category-planner', component: CategoryPlanner, name: 'category-planner' },
];

const router = createRouter({
    history: createWebHashHistory(),
    routes,
});


// =============================================================================
// Mount App
// =============================================================================

const app = createApp(App);
// `$t`/`$tn` in jedem Template, ohne sie durch 100 Komponenten zu reichen.
// Reaktiv wird es von selbst: `t()` liest `uiLang.value`, also haengt jeder
// Render, der `$t` aufruft, am Ref — setUiLang() faerbt die ganze Oberflaeche
// neu, ohne Neustart und ohne Remount.
app.config.globalProperties.$t = t;
app.config.globalProperties.$tn = tn;
app.config.globalProperties.$locale = uiLocale;
app.config.globalProperties.$lang = () => uiLang.value;

// Boot (#187): Add-on frontends first, then the router, then the mount — so
// the first route resolves against the Add-on routes too. Without Add-ons no
// await is ever reached and the app mounts synchronously, as before.
async function lbBoot() {
    const list = Array.isArray(window.LB_ADDONS) ? window.LB_ADDONS : [];
    if (list.length) {
        try {
            await reconcileAddons(list);
        } catch (e) {
            console.error('[LocalBib] Add-on boot failed:', e);
        }
    }
    _routerStarted = true;
    app.use(router);
    app.mount('#app');
}
lbBoot();
