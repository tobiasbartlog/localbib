// =============================================================================
// Literatur-Manager Web UI - Vue 3 SPA
// =============================================================================

const { createApp, ref, reactive, computed, watch, onMounted, nextTick } = Vue;
const { createRouter, createWebHashHistory } = VueRouter;

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
        const err = await resp.json().catch(() => ({ detail: 'Unbekannter Fehler' }));
        throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
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
                    <span @click.stop="$emit('edit', node)" title="Bearbeiten"
                          style="cursor:pointer" v-html="icons.edit"></span>
                    <span @click.stop="$emit('delete', node)" title="Löschen"
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
    { id: 'openrouter', label: 'OpenRouter (viele Modelle)' },
    { id: 'groq', label: 'Groq' },
    { id: 'custom', label: 'Custom (OpenAI-kompatibel, z. B. Ollama)' },
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
                        :title="collapsed ? 'Seitenleiste ausklappen' : 'Seitenleiste einklappen'">
                    <svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="m11 17-5-5 5-5"/><path d="m18 17-5-5 5-5"/></svg>
                </button>
            </div>

            <!-- Navigation -->
            <nav class="lb-nav">
                <router-link to="/" class="lb-nav-item" exact-active-class="is-active"
                             :title="collapsed ? 'Alle Paper' : null">
                    <span class="lb-nav-icon" v-html="icons.papers"></span>
                    <span class="lb-nav-label">Alle Paper</span>
                </router-link>
                <router-link to="/import" class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? 'Import' : null">
                    <span class="lb-nav-icon" v-html="icons.upload"></span>
                    <span class="lb-nav-label">Import</span>
                    <span v-if="stats.pending_imports" class="lb-nav-badge">{{ stats.pending_imports }}</span>
                </router-link>
                <router-link to="/analyse" class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? 'Analyse' : null">
                    <span class="lb-nav-icon" v-html="icons.network"></span>
                    <span class="lb-nav-label">Analyse</span>
                </router-link>
                <router-link to="/research-chat" class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? 'Research Chat' : null">
                    <span class="lb-nav-icon"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></span>
                    <span class="lb-nav-label">Research Chat</span>
                </router-link>
                <router-link v-if="thesisMode" to="/thesis" class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? 'Thesis-Analyse' : null">
                    <span class="lb-nav-icon"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg></span>
                    <span class="lb-nav-label">Thesis-Analyse</span>
                </router-link>
                <!-- Plugin-NavItems (Phase 0b): vom Server via /api/plugins/nav geliefert -->
                <router-link v-for="item in pluginNavItems" :key="item.id" :to="item.route"
                             class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? item.label : null">
                    <span class="lb-nav-icon" v-html="item.icon"></span>
                    <span class="lb-nav-label">{{ item.label }}</span>
                </router-link>
                <router-link to="/settings" class="lb-nav-item" active-class="is-active"
                             :title="collapsed ? 'Einstellungen' : null">
                    <span class="lb-nav-icon" v-html="icons.settings"></span>
                    <span class="lb-nav-label">Einstellungen</span>
                </router-link>
            </nav>

            <!-- Kategorien -->
            <div class="lb-section">
                <span class="lb-section-label">Kategorien</span>
                <span style="display:inline-flex; gap:4px">
                    <button class="lb-section-add" @click="showAddCategoryModal = true" title="Neue Kategorie">+</button>
                    <button class="lb-section-add" @click="$router.push({ name: 'category-planner' })"
                            title="Kategorien-Planner öffnen" style="font-size:11px">⌥</button>
                </span>
            </div>
            <div class="lb-list lb-cats">
                <div v-if="loading" style="display:flex; justify-content:center; padding:16px">
                    <div class="spinner"></div>
                </div>
                <div v-else-if="tree.length === 0" style="padding:6px 10px; font-size:12.5px; color:var(--lb-mute)">
                    Keine Kategorien vorhanden
                </div>
                <category-node v-for="root in tree" :key="root.id" :node="root"
                               :edit-mode="categoryEditMode"
                               @edit="startEditCategory"
                               @delete="deleteCategory" />
            </div>

            <!-- Footer -->
            <div class="lb-foot">
                <div class="lb-foot-stats">
                    <span>{{ stats.paper_count || 0 }} Paper</span>
                    <span>{{ stats.category_count || 0 }} Kategorien</span>
                </div>
                <div class="lb-foot-row">
                    <button class="lb-foot-btn" @click="toggleTheme"
                            :title="darkMode ? 'Light Mode' : 'Dark Mode'">
                        <span v-html="darkMode ? icons.sun : icons.moon" style="width:14px;height:14px"></span>
                    </button>
                    <button class="lb-foot-btn" @click="shutdownServer" title="Server beenden">
                        <span v-html="icons.power" style="width:14px;height:14px"></span>
                    </button>
                </div>
            </div>

            <!-- Add Category Modal -->
            <div v-if="showAddCategoryModal" class="fixed inset-0 bg-black/30 flex items-center justify-center z-50"
                 @click.self="showAddCategoryModal = false">
                <div class="bg-white rounded-xl shadow-xl p-6 w-full max-w-md">
                    <h4 class="text-base font-semibold text-gray-900 mb-4">Neue Kategorie</h4>
                    <div class="space-y-3">
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">Name</label>
                            <input v-model="newCat.name" placeholder="z.B. Nachhaltigkeit"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">Oberkategorie</label>
                            <select v-model="newCat.parent_id"
                                    class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-accent focus:border-accent outline-none">
                                <option :value="null">Keine (Oberkategorie)</option>
                                <option v-for="c in flatCategories" :key="c.id" :value="c.id">{{ c.name }}</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">Beschreibung</label>
                            <input v-model="newCat.description" placeholder="Optionale Beschreibung"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">Keywords</label>
                            <input v-model="newCat.keywords" placeholder="kommagetrennt, z.B. LCA, EPD"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                    </div>
                    <div class="flex items-center gap-2 mt-5">
                        <button @click="llmSuggestCategory" :disabled="!newCat.name || llmLoading"
                                class="inline-flex items-center gap-1.5 bg-accent-soft border border-accent-soft text-accent-ink px-3 py-2 rounded-lg text-sm font-medium hover:bg-accent-soft disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            <span v-html="icons.sparkle"></span>
                            <span v-if="llmLoading">LLM denkt...</span>
                            <span v-else>LLM-Vorschlag</span>
                        </button>
                        <div class="flex-1"></div>
                        <button @click="showAddCategoryModal = false"
                                class="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors">
                            Abbrechen
                        </button>
                        <button @click="createCategory" :disabled="!newCat.name"
                                class="bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            Erstellen
                        </button>
                    </div>
                </div>
            </div>

            <!-- Edit Category Modal -->
            <div v-if="editCat" class="fixed inset-0 bg-black/30 flex items-center justify-center z-50"
                 @click.self="editCat = null">
                <div class="bg-white rounded-xl shadow-xl p-6 w-full max-w-md">
                    <h4 class="text-base font-semibold text-gray-900 mb-4">Kategorie bearbeiten</h4>
                    <div class="space-y-3">
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">Name</label>
                            <input v-model="editCat.name"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">Beschreibung</label>
                            <input v-model="editCat.description"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">Keywords</label>
                            <input v-model="editCat.keywords"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                    </div>
                    <div class="flex justify-end gap-2 mt-5">
                        <button @click="editCat = null"
                                class="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors">
                            Abbrechen
                        </button>
                        <button @click="saveCategory"
                                class="bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                            Speichern
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
    methods: {
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
            // Plugin-NavItems + Routen (Phase 0b): aus aktiven Plugins
            try {
                const data = await api('/api/plugins/nav');
                this.pluginNavItems = data.items || [];
            } catch (e) {
                this.pluginNavItems = [];
            }
            syncPluginRoutes(this.pluginNavItems);
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
                alert('Fehler: ' + e.message);
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
                alert('Fehler: ' + e.message);
            }
        },
        async deleteCategory(node) {
            if (!confirm(`Kategorie "${node.name}" wirklich loeschen? Unterkategorien werden ebenfalls entfernt.`)) return;
            try {
                await api(`/api/categories/${node.id}`, { method: 'DELETE' });
                await this.load();
            } catch (e) {
                alert('Fehler: ' + e.message);
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
                alert('LLM-Vorschlag fehlgeschlagen: ' + e.message);
            }
            this.llmLoading = false;
        },
        toggleTheme() {
            this.darkMode = !this.darkMode;
            localStorage.setItem('darkMode', String(this.darkMode));
            applyDarkMode(this.darkMode);
        },
        async shutdownServer() {
            if (!confirm('Server wirklich herunterfahren? Die Web-UI wird nicht mehr erreichbar sein.')) return;
            try {
                await api('/api/server/shutdown', { method: 'POST' });
                document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif;color:#666"><div style="text-align:center"><h2>Server beendet</h2><p>Du kannst dieses Fenster schliessen.</p></div></div>';
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
                        {{ resultCount }} {{ resultCount === 1 ? 'Eintrag' : 'Eintraege' }}
                        <template v-if="searchQuery">
                            <span class="lb-page-meta-sep">&middot;</span>
                            <span>fuer &bdquo;{{ searchQuery }}&ldquo;</span>
                        </template>
                        <template v-if="semanticMode && semanticMeta.mode && semanticMeta.mode !== 'semantic'">
                            <span class="lb-page-meta-sep">&middot;</span>
                            <span>{{ semanticMeta.mode === 'hybrid' ? 'Hybrid-Suche' : 'Lexikalischer Fallback' }}</span>
                        </template>
                    </div>
                </div>
                <div class="lb-page-head-r">
                    <button v-if="semanticAvailable" class="lb-btn lb-btn-ghost" :class="{ 'is-on': semanticMode }"
                            @click="toggleSemantic" title="Ueber Embeddings statt Volltext suchen"
                            data-testid="semantic-toggle">
                        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v3m0 12v3m9-9h-3M6 12H3m14.5-6.5-2 2m-9 9-2 2m0-13 2 2m9 9 2 2"/></svg>
                        Semantisch suchen
                    </button>
                    <div class="lb-search">
                        <span class="lb-search-icon" v-html="icons.search"></span>
                        <input v-model="searchQuery" @input="debouncedSearch"
                               type="text" placeholder="Suche in Titel, Autor, Abstract, Notizen..."
                               class="lb-search-input" />
                        <button v-if="searchQuery" @click="clearSearch" class="lb-search-clear" aria-label="Clear">
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
                        Filter
                        <span v-if="hasActiveFilters" class="lb-pill lb-pill-accent">{{ activeFilterCount }}</span>
                    </button>
                    <button class="lb-btn lb-btn-ghost" @click="openResearchChat">
                        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                        Chat
                    </button>
                    <button class="lb-btn lb-btn-ghost" :disabled="dupLoading" @click="findDuplicates">
                        <svg v-if="!dupLoading" xmlns="http://www.w3.org/2000/svg" width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5"><path d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
                        <span v-else class="spinner-sm"></span>
                        Duplikate
                        <span v-if="dupGroups && dupGroups.length" class="lb-pill lb-pill-accent">{{ dupGroups.length }}</span>
                    </button>
                </div>
                <div class="lb-toolbar-r">
                    <div class="lb-sortgroup">
                        <span class="lb-sortgroup-l">Sortierung</span>
                        <select v-model="sortBy" class="lb-select">
                            <option value="year">Jahr</option>
                            <option value="authors">Autor</option>
                            <option value="title">Titel</option>
                            <option value="cited_by_count">Zitationen</option>
                            <option value="created_at">Hinzugefuegt</option>
                        </select>
                        <button class="lb-icon-btn" :title="sortAsc ? 'Aufsteigend' : 'Absteigend'"
                                @click="sortAsc = !sortAsc">
                            {{ sortAsc ? '↑' : '↓' }}
                        </button>
                    </div>
                    <div class="lb-segmented">
                        <button class="lb-seg" :class="{ 'is-on': view === 'list' }" title="Liste"
                                @click="view = 'list'">
                            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
                        </button>
                        <button class="lb-seg" :class="{ 'is-on': view === 'grid' }" title="Raster"
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
                        <span class="lb-field-label">Jahr von</span>
                        <input v-model.number="filters.yearFrom" type="number" placeholder="z.B. 2020" class="lb-input" />
                    </label>
                    <label class="lb-field">
                        <span class="lb-field-label">Jahr bis</span>
                        <input v-model.number="filters.yearTo" type="number" placeholder="z.B. 2025" class="lb-input" />
                    </label>
                    <label class="lb-field">
                        <span class="lb-field-label">Kategorie</span>
                        <select v-model="filters.categoryId" class="lb-input">
                            <option value="">Alle Kategorien</option>
                            <option v-for="c in allCategories" :key="c.id" :value="c.id">{{ c.name }}</option>
                        </select>
                    </label>
                    <label class="lb-field">
                        <span class="lb-field-label">DOI</span>
                        <select v-model="filters.hasDoi" class="lb-input">
                            <option value="">Egal</option>
                            <option value="yes">Mit DOI</option>
                            <option value="no">Ohne DOI</option>
                        </select>
                    </label>
                    <label class="lb-field">
                        <span class="lb-field-label">Herkunft</span>
                        <select v-model="filters.importSource" class="lb-input">
                            <option value="">Alle Quellen</option>
                            <option value="pdf">PDF-Upload</option>
                            <option value="bibtex">BibTeX-Import</option>
                            <option value="ris">RIS-Import</option>
                            <option value="other">Sonstige</option>
                        </select>
                    </label>
                    <label class="lb-field">
                        <span class="lb-field-label">PDF</span>
                        <select v-model="filters.hasPdf" class="lb-input">
                            <option value="">Egal</option>
                            <option value="yes">Mit PDF</option>
                            <option value="no">Ohne PDF</option>
                        </select>
                    </label>
                </div>
                <div v-if="tagCloud.length" class="lb-field" style="margin-top: 14px;">
                    <span class="lb-field-label">Haeufige Kategorien</span>
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
                    <button class="lb-btn lb-btn-text" @click="resetFilters">Alle Filter zuruecksetzen</button>
                </div>
            </div>

            <!-- Selection Action Bar -->
            <div v-if="selectedPapers.length > 0" class="bg-accent-soft border border-accent-soft rounded-lg p-3 mb-4 flex items-center justify-between">
                <span class="text-sm font-medium text-accent-ink">{{ selectedPapers.length }} Paper ausgewaehlt</span>
                <div class="flex items-center gap-2">
                    <!-- Action Dropdown -->
                    <div class="relative" ref="actionDropdownRef">
                        <button @click="showActionDropdown = !showActionDropdown"
                                class="inline-flex items-center gap-1.5 bg-white border border-accent-soft text-accent-ink px-3 py-1.5 rounded-lg text-sm font-medium hover:bg-accent-soft transition-colors">
                            Aktion waehlen
                            <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
                        </button>
                        <div v-if="showActionDropdown" class="absolute right-0 top-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-20 w-56 py-1">
                            <button @click="bulkValidate(); showActionDropdown = false" :disabled="bulkValidating"
                                    class="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2 disabled:opacity-40">
                                <span v-html="icons.refresh"></span>
                                {{ bulkValidating ? 'Validiere...' : 'Metadaten validieren' }}
                            </button>
                            <button @click="bulkFetchAbstracts(); showActionDropdown = false" :disabled="fetchingAbstracts"
                                    class="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2 disabled:opacity-40">
                                <span v-html="icons.pdf"></span>
                                {{ fetchingAbstracts ? 'Lade...' : 'Abstracts holen' }}
                            </button>
                            <button @click="discoverDois(); showActionDropdown = false" :disabled="discoveringDois"
                                    class="w-full text-left px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2 disabled:opacity-40">
                                <span v-html="icons.search"></span>
                                {{ discoveringDois ? 'Suche...' : 'DOIs suchen' }}
                            </button>
                            <div class="border-t border-gray-100 my-1"></div>
                            <button @click="bulkDeleteSelected(); showActionDropdown = false" :disabled="bulkDeleting"
                                    class="w-full text-left px-4 py-2 text-sm text-red-600 hover:bg-red-50 flex items-center gap-2 disabled:opacity-40">
                                {{ bulkDeleting ? 'Lösche...' : 'Ausgewählte löschen' }}
                            </button>
                        </div>
                    </div>
                    <button @click="selectedPapers = []; selectAll = false" class="text-xs text-gray-500 hover:text-gray-700 px-2 py-1">Auswahl aufheben</button>
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
                            <h3 class="lb-row-title">{{ hit.title || 'Kein Titel' }}</h3>
                            <div class="lb-row-meta">
                                <span class="lb-authors">{{ hit.citekey }}</span>
                                <span class="lb-meta-dot">&middot;</span>
                                <span>Score {{ hit.score.toFixed(3) }}</span>
                            </div>
                            <p v-if="hit.abstract_excerpt" class="lb-row-abstract">{{ hit.abstract_excerpt }}</p>
                        </div>
                    </li>
                </ul>
                <div v-else class="lb-empty">
                    <div class="lb-empty-mark">[ ]</div>
                    <div class="lb-empty-title">{{ searchQuery ? 'Keine Treffer' : 'Suchbegriff eingeben' }}</div>
                    <p class="lb-empty-text">Die semantische Suche durchsucht Titel und Abstracts per Embedding.</p>
                </div>
            </template>

            <!-- Select All + Papers -->
            <template v-else-if="filteredPapers.length">
                <div style="display: flex; align-items: center; gap: 10px; margin: 6px 2px 10px; font-family: var(--lb-font-mono); font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--lb-mute);">
                    <label style="display: inline-flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;">
                        <input type="checkbox" v-model="selectAll" @change="toggleSelectAll" />
                        Alle auswaehlen
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
                            <h3 class="lb-row-title">{{ paper.title || 'Kein Titel' }}</h3>
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
                                          :title="'Herkunft: ' + paperSource(paper).label + (paperSource(paper).detail ? ' (' + paperSource(paper).detail + ')' : '') + ' — klicken zum Filtern'"
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
                                    <span v-if="paper.cited_by_count" class="lb-stat" title="Zitationen">
                                        <span class="lb-stat-n">{{ paper.cited_by_count }}</span>
                                        <span class="lb-stat-l">cit.</span>
                                    </span>
                                    <span v-if="paper.page_count" class="lb-stat" title="Seiten">
                                        <span class="lb-stat-n">{{ paper.page_count }}</span>
                                        <span class="lb-stat-l">S.</span>
                                    </span>
                                    <span v-if="paper.filename" class="lb-stat lb-stat-pdf">PDF</span>
                                    <span v-else class="lb-stat lb-stat-nopdf" title="Kein PDF auf diesem Rechner">kein PDF</span>
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
                            <h3 class="lb-pcard-title">{{ paper.title || 'Kein Titel' }}</h3>
                            <div class="lb-pcard-meta">{{ formatAuthors(paper.authors) }}</div>
                            <div class="lb-tags" style="margin: 6px 0 2px;">
                                <span class="lb-tag lb-tag-src" :class="'lb-src-' + paperSource(paper).key"
                                      :title="'Herkunft: ' + paperSource(paper).label + ' — klicken zum Filtern'"
                                      @click.stop="filters.importSource = paperSource(paper).key; showFilters = true">
                                    {{ paperSource(paper).label }}
                                </span>
                                <span v-if="!paper.filename" class="lb-tag lb-stat-nopdf" title="Kein PDF auf diesem Rechner">kein PDF</span>
                            </div>
                            <div class="lb-pcard-foot">
                                <span class="lb-pcard-journal">{{ paper.journal || '' }}</span>
                                <span class="lb-pcard-cit" v-if="paper.cited_by_count">{{ paper.cited_by_count }} cit.</span>
                            </div>
                        </div>
                    </article>
                </div>
            </template>

            <!-- Empty State -->
            <div v-else class="lb-empty">
                <div class="lb-empty-mark">[ ]</div>
                <div class="lb-empty-title">Keine Treffer</div>
                <p class="lb-empty-text">
                    Passe Filter oder Suche an, oder importiere neue PDFs ueber den Import-Tab.
                </p>
            </div>

            <!-- DOI Confirmation Modal -->
            <div v-if="doiCandidates.length > 0" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="doiCandidates = []">
                <div class="bg-white rounded-xl shadow-xl max-w-3xl w-full mx-4 max-h-[85vh] flex flex-col">
                    <div class="flex items-center justify-between p-5 border-b border-gray-200">
                        <div>
                            <h3 class="text-lg font-semibold text-gray-900">DOI-Kandidaten bestaetigen</h3>
                            <p class="text-sm text-gray-500 mt-0.5">{{ doiCandidateIndex + 1 }} von {{ doiCandidates.length }} Kandidaten</p>
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
                                    <h4 class="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">Eigene Metadaten</h4>
                                    <div class="space-y-2">
                                        <div>
                                            <span class="block text-xs text-gray-400">Titel</span>
                                            <span class="text-sm text-gray-900">{{ currentDoiCandidate.local.title || '—' }}</span>
                                        </div>
                                        <div>
                                            <span class="block text-xs text-gray-400">Autoren</span>
                                            <span class="text-sm text-gray-900">{{ currentDoiCandidate.local.authors || '—' }}</span>
                                        </div>
                                        <div>
                                            <span class="block text-xs text-gray-400">Jahr</span>
                                            <span class="text-sm text-gray-900">{{ currentDoiCandidate.local.year || '—' }}</span>
                                        </div>
                                    </div>
                                </div>
                                <!-- CrossRef -->
                                <div class="rounded-lg border border-accent-soft bg-accent-soft p-4">
                                    <h4 class="text-xs font-semibold text-accent uppercase tracking-wide mb-3">DOI-Metadaten (CrossRef)</h4>
                                    <div class="space-y-2">
                                        <div>
                                            <span class="block text-xs text-gray-400">Titel</span>
                                            <span class="text-sm text-gray-900">{{ currentDoiCandidate.crossref.title || '—' }}</span>
                                        </div>
                                        <div>
                                            <span class="block text-xs text-gray-400">Autoren</span>
                                            <span class="text-sm text-gray-900">{{ currentDoiCandidate.crossref.authors || '—' }}</span>
                                        </div>
                                        <div>
                                            <span class="block text-xs text-gray-400">Jahr</span>
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
                            Ablehnen
                        </button>
                        <div class="flex gap-2">
                            <button @click="skipDoiCandidate"
                                    class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 text-gray-600 bg-white hover:bg-gray-100 transition-colors">
                                Ueberspringen
                            </button>
                            <button @click="acceptDoiCandidate"
                                    :disabled="applyingDoi"
                                    class="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-accent text-white hover:bg-accent-ink disabled:opacity-40 transition-colors">
                                <span v-if="applyingDoi" class="spinner" style="width:14px;height:14px;border-width:2px;border-top-color:#fff;"></span>
                                <span v-else v-html="icons.check"></span>
                                Uebernehmen
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
                            <h3 class="text-lg font-semibold text-gray-900">Duplikaterkennung</h3>
                            <p class="text-sm text-gray-500 mt-0.5" v-if="dupGroups">{{ dupGroups.length }} Duplikat-Gruppe(n) gefunden</p>
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
                            <p class="text-sm text-gray-600 mt-2">Keine Duplikate gefunden!</p>
                        </div>
                        <div v-else-if="dupGroups && dupGroups.length > 0" class="space-y-4">
                            <div v-for="(group, gi) in dupGroups" :key="gi" class="border border-accent-soft rounded-lg overflow-hidden">
                                <div class="bg-accent-soft px-4 py-2 flex items-center justify-between">
                                    <span class="text-sm font-medium text-accent-ink">{{ group.reason }}</span>
                                    <span class="text-xs text-accent">{{ group.papers.length }} Paper</span>
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
                                                <span v-if="p.has_file === false" class="text-accent font-medium">Keine Datei!</span>
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
                                            Beide behalten
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
            },
            // A plugin may contribute a project filter (ADR-0004); the core
            // never turns this on by itself, so the column stays hidden.
            projectFilterAvailable: false,
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
            // (LLM_EMBED_MODEL) -- otherwise /api/search/semantic just falls back to BM25
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
            if (this.$route.name === 'category') return this.categoryName || 'Kategorie';
            return 'Alle Paper';
        },
        heading() {
            if (this.$route.name === 'category') {
                return { eyebrow: 'Kategorie', title: this.categoryName || '—' };
            }
            return { eyebrow: 'Bibliothek', title: 'Alle Paper' };
        },
        filterGridColumns() {
            // Jahr von, Jahr bis, [Projekt], Kategorie, DOI, Herkunft, PDF
            return this.projectFilterAvailable
                ? '140px 140px 1fr 1fr 1fr 1fr 1fr'
                : '140px 140px 1fr 1fr 1fr 1fr';
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
            );
        },
        activeFilterCount() {
            let c = 0;
            if (this.filters.yearFrom || this.filters.yearTo) c++;
            if (this.filters.categoryId) c++;
            if (this.filters.hasDoi) c++;
            if (this.filters.importSource) c++;
            if (this.filters.hasPdf) c++;
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
            // The plugin project filter is server-side (cite_keys param in loadPapers).
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
    },
    beforeUnmount() {
        document.removeEventListener('click', this._closeDropdown);
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
            // Semantic-search toggle only shows up once an embedding model is
            // configured (LLM_EMBED_MODEL is not a secret, so /api/settings
            // returns it in plain). No config / request failure -> stay hidden.
            try {
                const settings = await api('/api/settings');
                this.semanticAvailable = !!(settings.LLM_EMBED_MODEL || '').trim();
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
            this.filters = {
                yearFrom: null, yearTo: null, categoryId: '', hasDoi: '', importSource: '', hasPdf: '',
            };
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
                alert('Abstract-Suche fehlgeschlagen: ' + e.message);
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
                alert('Abstract-Suche fehlgeschlagen: ' + e.message);
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
                alert('DOI-Suche fehlgeschlagen: ' + e.message);
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
                alert('Fehler beim Uebernehmen: ' + e.message);
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
            if (!confirm(`${n} ausgewählte Paper wirklich löschen? Zugehörige PDF-Dateien werden ebenfalls entfernt.`)) return;
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
                alert(`${res.deleted} Paper gelöscht.`);
            } catch (e) {
                alert('Löschen fehlgeschlagen: ' + e.message);
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
                alert('Fehler bei Duplikatsuche: ' + e.message);
            }
            this.dupLoading = false;
        },
        async mergeDuplicateGroup(gi) {
            const group = this.dupGroups[gi];
            const keepId = this.dupKeep[gi];
            if (!keepId) return;
            const deleteIds = group.papers.filter(p => p.id !== keepId).map(p => p.id);
            if (!confirm('Wirklich ' + deleteIds.length + ' Duplikat(e) loeschen und mit Paper #' + keepId + ' zusammenfuehren?')) return;
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
                alert('Fehler beim Zusammenfuehren: ' + e.message);
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
                    <span class="lb-mono lb-truncate lb-pdf-name" :title="paper.filename || 'Kein PDF hinterlegt'">
                        {{ paper.filename || 'Kein PDF hinterlegt' }}
                    </span>
                    <!-- Without a file the actions belong to the drop zone below,
                         not up here twice. -->
                    <div class="lb-pdf-actions">
                        <button v-if="paper.filename" class="lb-btn lb-btn-primary" @click="openInApp">PDF oeffnen</button>
                        <input ref="attachPdfInput" type="file" accept="application/pdf,.pdf" style="display:none" @change="onPdfSelected" />
                    </div>
                </header>
                <div class="lb-pdf-body">
                    <iframe v-if="paper.filename" class="lb-pdf-frame" data-testid="pdf-frame"
                            :src="'/api/papers/' + paper.id + '/pdf' + (pdfPage ? '#page=' + pdfPage : '')"
                            loading="lazy"></iframe>
                    <div v-else class="lb-pdf-drop" data-testid="pdf-drop-zone">
                        <p class="lb-pdf-drop-h">Kein PDF hinterlegt</p>
                        <p class="lb-pdf-drop-p">Datei anhaengen, eine frei zugaengliche Fassung holen oder auf Scholar suchen.</p>
                        <div class="lb-pdf-drop-actions">
                            <button class="lb-btn lb-btn-primary" @click="$refs.attachPdfInput.click()" :disabled="attachingPdf">
                                {{ attachingPdf ? 'Haengt an...' : 'PDF anhaengen' }}
                            </button>
                            <button v-if="paper.doi" class="lb-btn lb-btn-ghost" @click="fetchOaPdf" :disabled="fetchingOa">
                                {{ fetchingOa ? 'Suche OA...' : 'PDF aus Open Access' }}
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
                    Zurueck zur Liste
                </button>
                <div class="lb-modal-actions" v-if="paper">
                    <button class="lb-btn lb-btn-ghost" @click="copyBibtex">
                        {{ bibtexCopied ? 'BibTeX kopiert!' : 'BibTeX' }}
                    </button>
                    <a v-if="paper.doi" class="lb-btn lb-btn-ghost"
                       :href="'https://doi.org/' + paper.doi" target="_blank" rel="noopener">
                        DOI oeffnen
                    </a>
                    <!-- Only visible in the two-column band (1100-1440px), where the
                         metadata column is folded away by default. -->
                    <button class="lb-btn lb-btn-ghost lb-meta-toggle" @click="metaOpen = !metaOpen"
                            :aria-pressed="String(metaOpen)" data-testid="meta-toggle">
                        {{ metaOpen ? 'Metadaten ausblenden' : 'Metadaten' }}
                    </button>
                    <div class="relative">
                        <button @click="actionMenuOpen = !actionMenuOpen"
                                class="lb-modal-close" aria-label="Mehr"
                                style="width:32px;">
                            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></svg>
                        </button>
                        <div v-if="actionMenuOpen"
                             class="absolute right-0 top-full mt-1 w-56 bg-white border border-gray-200 rounded-lg shadow-lg z-30 py-1"
                             style="background: var(--lb-bg-elev); border-color: var(--lb-hairline);"
                             @click="actionMenuOpen = false">
                            <button @click="startEditMetadata"
                                    class="w-full flex items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
                                <span v-html="icons.edit"></span> Bearbeiten
                            </button>
                            <button @click="runOcr" :disabled="ocrRunning"
                                    class="w-full flex items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-40">
                                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="15" x2="15" y2="15"/></svg>
                                {{ ocrRunning ? 'OCR laeuft...' : 'OCR starten' }}
                            </button>
                            <button @click="validateMetadata" :disabled="validating"
                                    class="w-full flex items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-40">
                                <span v-html="icons.refresh"></span>
                                {{ validating ? 'Validiere...' : 'Metadaten validieren' }}
                            </button>
                            <button @click="extractReferences()" :disabled="extractingRefs"
                                    class="w-full flex items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-40">
                                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
                                {{ extractingRefs ? 'Extrahiere...' : 'Referenzen extrahieren' }}
                            </button>
                            <router-link :to="'/research-chat?paper_ids=' + paper.id"
                                         class="w-full flex items-center gap-2 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50">
                                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                                Research Chat
                            </router-link>
                            <div class="border-t my-1" style="border-color: var(--lb-hairline);"></div>
                            <button @click="deletePaper"
                                    class="w-full flex items-center gap-2 px-4 py-2 text-sm text-accent hover:bg-accent-soft">
                                <span v-html="icons.trash"></span> Loeschen
                            </button>
                        </div>
                        <div v-if="actionMenuOpen" class="fixed inset-0 z-20" @click="actionMenuOpen = false"></div>
                    </div>
                    <button class="lb-modal-close" @click="close" aria-label="Schliessen">
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
                    <h4 class="lb-detail-h">Metadaten bearbeiten</h4>
                    <div class="space-y-3 mb-4">
                        <div>
                            <label class="block text-xs text-gray-500 mb-1" style="font-family: var(--lb-font-mono); text-transform: uppercase; letter-spacing: 0.06em; color: var(--lb-mute);">Titel</label>
                            <input v-model="editMeta.title" class="lb-input" />
                        </div>
                        <div class="grid grid-cols-2 gap-3">
                            <div>
                                <label class="block text-xs mb-1" style="font-family: var(--lb-font-mono); text-transform: uppercase; letter-spacing: 0.06em; color: var(--lb-mute);">Autoren</label>
                                <input v-model="editMeta.authors" class="lb-input" />
                            </div>
                            <div>
                                <label class="block text-xs mb-1" style="font-family: var(--lb-font-mono); text-transform: uppercase; letter-spacing: 0.06em; color: var(--lb-mute);">Jahr</label>
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
                                <label class="block text-xs mb-1" style="font-family: var(--lb-font-mono); text-transform: uppercase; letter-spacing: 0.06em; color: var(--lb-mute);">Verlag</label>
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
                            <span v-html="icons.check"></span> Speichern
                        </button>
                        <button @click="editingMeta = false" class="lb-btn lb-btn-ghost">Abbrechen</button>
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
                    <h1 class="lb-detail-title">{{ paper.title || 'Kein Titel' }}</h1>
                    <p class="lb-detail-authors" v-if="paper.authors">{{ paper.authors }}</p>
                </template>

                <!-- Abstract -->
                <section v-if="!editingMeta && paper.abstract" class="lb-detail-section">
                    <div class="flex items-center gap-2" style="margin-bottom: 10px;">
                        <h4 class="lb-detail-h" style="margin: 0;">Abstract</h4>
                        <span v-if="paper.abstract_source === 'generated'" class="lb-tag" style="font-size: 9.5px;" title="Dieses Abstract wurde von einer KI generiert">KI-generiert</span>
                        <span v-else-if="paper.abstract_source === 'pdf'" class="lb-tag" style="font-size: 9.5px;" title="Dieses Abstract wurde per KI aus dem PDF extrahiert">KI-extrahiert</span>
                    </div>
                    <p class="lb-detail-abstract">{{ paper.abstract }}</p>
                </section>

                <!-- Notizen (#160) -->
                <section v-if="!editingMeta" class="lb-detail-section" data-testid="paper-notes">
                    <div class="flex items-center justify-between" style="margin-bottom: 10px;">
                        <h4 class="lb-detail-h" style="margin: 0;">Notizen</h4>
                        <div class="flex items-center gap-3">
                            <span v-if="notesStatus" class="lb-notes-status" :data-state="notesSaveState">{{ notesStatus }}</span>
                            <button @click="notesEditing ? showNotes() : editNotes()" class="lb-btn-text" style="font-size: 11.5px;">
                                {{ notesEditing ? 'Ansicht' : 'Bearbeiten' }}
                            </button>
                        </div>
                    </div>
                    <div v-if="notesEditing" class="lb-notes-editor" data-testid="paper-notes-editor">
                        <textarea ref="notesInput" v-model="notesDraft" @input="onNotesTyped()"
                                  rows="6" class="lb-input"
                                  placeholder="Gedanken zu diesem Paper &mdash; Markdown erlaubt."></textarea>
                    </div>
                    <div v-else class="lb-md lb-notes-rendered" data-testid="paper-notes-rendered"
                         title="Zum Bearbeiten klicken" @click="editNotes()" v-html="notesHtml"></div>
                </section>

                <!-- Custom Fields -->
                <section v-if="!editingMeta && paper.custom_fields && paper.custom_fields.length" class="lb-detail-section">
                    <h4 class="lb-detail-h">Benutzerdefinierte Felder</h4>
                    <div class="space-y-4">
                        <div v-for="cf in paper.custom_fields" :key="cf.field_id">
                            <template v-if="cf.field_type === 'text'">
                                <label class="block text-xs mb-1" style="color: var(--lb-mute);">{{ cf.name }}</label>
                                <textarea v-model="customValues[cf.field_id]"
                                          @blur="saveCustomValue(cf.field_id)"
                                          rows="3" :placeholder="cf.name + ' eingeben...'"
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
                                    <option value="">-- Auswahl --</option>
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
                            Extrahierte Referenzen
                            <span v-if="paperRefs.length" style="color: var(--lb-mute);">({{ paperRefs.length }})</span>
                        </h4>
                        <div class="flex items-center gap-2">
                            <button @click="startAddRef" class="lb-btn-text" style="font-size: 11.5px;">+ Manuell</button>
                            <button v-if="paperRefs.length" @click="reExtractReferences" :disabled="extractingRefs" class="lb-btn-text" style="font-size: 11.5px;">Neu extrahieren</button>
                        </div>
                    </div>

                    <div v-if="refExtractionResult" class="mb-4 p-3 rounded-lg text-sm" style="background: var(--lb-bg-soft); border: 1px solid var(--lb-hairline);">
                        <p v-if="refExtractionResult.status === 'ok' || refExtractionResult.status === 'already_extracted'" style="color: var(--lb-ink-2);">
                            <strong>{{ refExtractionResult.total_extracted }}</strong> Referenzen &middot;
                            <strong>{{ refExtractionResult.with_doi }}</strong> mit DOI &middot;
                            <strong>{{ refExtractionResult.in_library }}</strong> in Bibliothek
                        </p>
                        <p v-else style="color: var(--lb-ink-2);">{{ refExtractionResult.error || 'Extraktion fehlgeschlagen' }}</p>
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
                                                 style="color: var(--lb-accent-ink); text-decoration: none;">{{ ref.title || 'Ohne Titel' }}</router-link>
                                    <span v-else>{{ ref.title || 'Ohne Titel' }}</span>
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
                                    <a :href="refScholarUrl(ref)" target="_blank" style="color: var(--lb-accent-ink);" title="Titel + Erstautor auf Google Scholar suchen">Auf Scholar suchen &#8599;</a>
                                </p>
                            </div>
                            <div class="flex items-center gap-2 flex-shrink-0">
                                <span v-if="ref.matched_paper_id" class="lb-tag lb-tag-cat" style="font-size: 10px;" :title="'Match: ' + Math.round((ref.match_confidence || 0) * 100) + '%'">In Bibliothek</span>
                                <span v-else-if="ref.doi" class="lb-tag" style="font-size: 10px;">DOI</span>
                                <span v-else class="lb-tag" style="font-size: 10px; color: var(--lb-mute);">Extern</span>
                                <button @click="startEditRef(ref)" class="opacity-0 group-hover:opacity-100 transition-all" style="color: var(--lb-mute);" title="Bearbeiten">
                                    <span v-html="icons.edit"></span>
                                </button>
                                <button @click="deleteRef(ref)" class="opacity-0 group-hover:opacity-100 transition-all" style="color: var(--lb-mute);" title="Loeschen">
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
                        <p class="font-medium mb-1" style="color: var(--lb-ink);">OCR {{ ocrResult._ocr_chars > 0 ? 'abgeschlossen' : 'ohne Ergebnis' }}</p>
                        <p v-if="ocrResult._ocr_chars > 0" style="color: var(--lb-ink-2);">
                            {{ ocrResult._ocr_chars }} Zeichen extrahiert
                            <span v-if="ocrResult._ocr_searchable"> &middot; PDF ist jetzt durchsuchbar</span>
                            <span v-if="ocrResult._ocr_llm && Object.keys(ocrResult._ocr_llm).length"> &middot; LLM hat Metadaten aktualisiert</span>
                        </p>
                        <p v-else style="color: var(--lb-ink-2);">Kein Text erkannt. PDF evtl. zu schlecht gescannt.</p>
                    </div>

                    <!-- Validate Result Banner -->
                    <div v-if="validateResult" class="lb-aside-banner" style="background: var(--lb-accent-soft);">
                        <button @click="validateResult = null" class="absolute top-2 right-2 opacity-50 hover:opacity-100" style="color: var(--lb-accent-ink);">
                            <span v-html="icons.x"></span>
                        </button>
                        <p class="font-medium mb-1" style="color: var(--lb-accent-ink);">Validierung abgeschlossen</p>
                        <p style="color: var(--lb-accent-ink);">
                            Quelle: {{ validateResult._validate_source }}
                            <span v-if="validateResult._validate_changes && Object.keys(validateResult._validate_changes).length">
                                &middot; {{ Object.keys(validateResult._validate_changes).length }} Felder aktualisiert
                            </span>
                        </p>
                    </div>

                    <div class="lb-aside-block">
                        <h5 class="lb-aside-h">Metadaten</h5>
                        <dl class="lb-aside-dl">
                            <dt>Jahr</dt><dd>{{ paper.year || '&mdash;' }}</dd>
                            <dt>Journal</dt><dd>{{ paper.journal || '&mdash;' }}</dd>
                            <dt>Verlag</dt><dd>{{ paper.publisher || '&mdash;' }}</dd>
                            <dt>DOI</dt><dd class="lb-mono lb-truncate" :title="paper.doi">{{ paper.doi || '&mdash;' }}</dd>
                            <dt>ISBN</dt><dd class="lb-mono lb-truncate" :title="paper.isbn">{{ paper.isbn || '&mdash;' }}</dd>
                            <dt>Zitationen</dt><dd>{{ paper.cited_by_count != null ? paper.cited_by_count : '&mdash;' }}</dd>
                            <dt>Cite Key</dt>
                            <dd v-if="!editingCiteKey" class="lb-mono lb-truncate" :title="paper.cite_key">
                                {{ paper.cite_key || '&mdash;' }}
                                <button @click="startEditCiteKey" class="text-xs ml-1" style="color: var(--lb-mute); cursor: pointer;" title="Cite Key bearbeiten">&#9998;</button>
                            </dd>
                            <dd v-else>
                                <input v-model="citeKeyDraft" @keyup.enter="saveCiteKey" @keydown.esc.stop="editingCiteKey = false"
                                       class="lb-input lb-mono" style="font-size: 12px; padding: 2px 6px; width: 100%;"
                                       :disabled="citeKeySaving" ref="citeKeyInput" />
                                <div class="flex gap-1 mt-1">
                                    <button @click="saveCiteKey" :disabled="citeKeySaving" class="lb-tag" style="cursor:pointer;">{{ citeKeySaving ? '...' : 'OK' }}</button>
                                    <button @click="editingCiteKey = false" class="lb-tag" style="cursor:pointer;">Abbrechen</button>
                                </div>
                                <p v-if="citeKeyError" class="text-xs mt-1" style="color: var(--lb-danger);">{{ citeKeyError }}</p>
                            </dd>
                        </dl>
                    </div>

                    <div class="lb-aside-block">
                        <div class="flex items-center justify-between">
                            <h5 class="lb-aside-h">Kategorien</h5>
                            <div class="relative">
                                <button @click="showCatAdd = !showCatAdd"
                                        class="lb-modal-close" style="width:22px;height:22px;font-weight:600;" title="Kategorie hinzufuegen">+</button>
                                <div v-if="showCatAdd" class="absolute right-0 top-full mt-1 w-56 z-30 p-2"
                                     style="background: var(--lb-bg-elev); border: 1px solid var(--lb-hairline); border-radius: 8px; box-shadow: var(--lb-shadow-lg);">
                                    <select v-model="selectedCategoryId" @change="if(selectedCategoryId){addCategory(); showCatAdd=false;}" class="lb-input" style="font-size: 12px; padding: 6px 8px;">
                                        <option value="">Kategorie waehlen...</option>
                                        <option v-for="c in availableCategories" :key="c.id" :value="c.id">{{ c.name }}</option>
                                    </select>
                                </div>
                                <div v-if="showCatAdd" class="fixed inset-0 z-20" @click="showCatAdd=false"></div>
                            </div>
                        </div>
                        <div class="lb-aside-tags">
                            <button v-for="cat in paper.categories" :key="cat.id" class="lb-tag lb-tag-cat" style="cursor:pointer;" @click="removeCategory(cat.id)" :title="'Kategorie entfernen: ' + cat.name">
                                {{ cat.name }} <span style="opacity:.5;">&times;</span>
                            </button>
                            <span v-if="!paper.categories || !paper.categories.length" class="text-xs italic" style="color: var(--lb-mute);">keine</span>
                        </div>
                    </div>

                </aside>
            </div>

                <!-- PDF-Verarbeitungsoptionen (nach Dateiauswahl, vor Upload) -->
                <div v-if="showAttachModal" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center" @click.self="cancelAttach">
                    <div class="bg-white rounded-xl shadow-xl p-6 max-w-md w-full mx-4">
                        <h3 class="text-lg font-semibold text-gray-900 mb-1">PDF verarbeiten</h3>
                        <p class="text-sm text-gray-500 mb-4 truncate" :title="pendingPdfName">{{ pendingPdfName }}</p>
                        <div class="space-y-2 mb-5">
                            <label class="flex items-start gap-2.5 cursor-pointer">
                                <input type="checkbox" v-model="attachOptions.ocr" class="mt-0.5" :disabled="attachBusy" />
                                <span>
                                    <span class="block text-sm font-medium text-gray-900">Text/OCR</span>
                                    <span class="block text-xs text-gray-500">OCR fuer gescannte PDFs ohne Textebene (langsamer).</span>
                                </span>
                            </label>
                            <label class="flex items-start gap-2.5 cursor-pointer">
                                <input type="checkbox" v-model="attachOptions.categories" class="mt-0.5" :disabled="attachBusy" />
                                <span>
                                    <span class="block text-sm font-medium text-gray-900">Kategorisieren</span>
                                    <span class="block text-xs text-gray-500">KI weist passende Kategorien zu.</span>
                                </span>
                            </label>
                            <label class="flex items-start gap-2.5 cursor-pointer">
                                <input type="checkbox" v-model="attachOptions.chunks" class="mt-0.5" :disabled="attachBusy" />
                                <span>
                                    <span class="block text-sm font-medium text-gray-900">Chunks (Research-Chat)</span>
                                    <span class="block text-xs text-gray-500">PDF fuer die semantische Suche aufbereiten.</span>
                                </span>
                            </label>
                            <label class="flex items-start gap-2.5 cursor-pointer">
                                <input type="checkbox" v-model="attachOptions.references" class="mt-0.5" :disabled="attachBusy" />
                                <span>
                                    <span class="block text-sm font-medium text-gray-900">Referenzen extrahieren</span>
                                    <span class="block text-xs text-gray-500">Literaturverzeichnis aus dem PDF auslesen (langsamer).</span>
                                </span>
                            </label>
                        </div>
                        <p v-if="attachBusy" class="text-sm text-gray-600 mb-3">{{ attachStatus || 'Verarbeite...' }}</p>
                        <div class="flex justify-end gap-2">
                            <button @click="cancelAttach" :disabled="attachBusy" class="lb-btn lb-btn-ghost">Abbrechen</button>
                            <button @click="confirmAttach" :disabled="attachBusy" class="lb-btn lb-btn-primary">
                                {{ attachBusy ? 'Laeuft...' : 'Starten' }}
                            </button>
                        </div>
                    </div>
                </div>

                <!-- Buch erkannt: auf Kapitel zuschneiden (Cover bleibt) -->
                <div v-if="showTrimModal" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center">
                    <div class="bg-white rounded-xl shadow-xl p-6 max-w-md w-full mx-4">
                        <h3 class="text-lg font-semibold text-gray-900 mb-1">Buch erkannt</h3>
                        <p class="text-sm text-gray-600 mb-4">
                            Dieses PDF hat <strong>{{ bookInfo && bookInfo.page_count }}</strong> Seiten &ndash;
                            vermutlich das ganze Buch statt nur des Kapitels.
                        </p>
                        <div v-if="bookInfo && bookInfo.range" class="mb-4 p-3 rounded-lg text-sm" style="background: var(--lb-bg-soft); border: 1px solid var(--lb-hairline);">
                            <p style="color: var(--lb-ink-2);">
                                Vorgeschlagenes Kapitel:
                                <strong v-if="bookInfo.printed_start">S. {{ bookInfo.printed_start }}&ndash;{{ bookInfo.printed_end }}</strong>
                                <strong v-else>{{ bookInfo.chapter_pages }} Seiten</strong>
                                &middot; Cover + {{ bookInfo.chapter_pages }} Seiten
                            </p>
                            <p class="text-xs mt-1" style="color: var(--lb-mute);">
                                Erkennung: {{ bookInfo.method }} ({{ bookInfo.confidence === 'high' ? 'sicher' : 'unsicher – bitte pruefen' }})
                            </p>
                        </div>
                        <p v-else class="mb-3 text-sm" style="color: var(--lb-mute);">
                            Kapitel-Seiten konnten nicht automatisch bestimmt werden. Bitte den
                            PDF-Seitenbereich manuell angeben.
                        </p>
                        <div class="flex items-end gap-3 mb-3">
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">von Seite (PDF)</label>
                                <input v-model.number="trimStart" type="number" min="1" :disabled="attachBusy"
                                       class="w-24 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">bis Seite (PDF)</label>
                                <input v-model.number="trimEnd" type="number" min="1" :disabled="attachBusy"
                                       class="w-24 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                            <label class="flex items-center gap-1.5 text-sm text-gray-700 pb-2 cursor-pointer">
                                <input type="checkbox" v-model="trimKeepCover" :disabled="attachBusy" /> Cover behalten
                            </label>
                        </div>
                        <p v-if="attachBusy" class="text-sm text-gray-600 mb-3">{{ attachStatus || 'Verarbeite...' }}</p>
                        <div class="flex justify-end gap-2">
                            <button @click="keepWholeBook" :disabled="attachBusy" class="lb-btn lb-btn-ghost">Ganzes Buch behalten</button>
                            <button @click="trimAndAttach" :disabled="attachBusy" class="lb-btn lb-btn-primary">Zuschneiden</button>
                        </div>
                    </div>
                </div>

                <!-- SSE Extraction Progress Modal -->
                <div v-if="extractingRefs" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center">
                    <div class="bg-white rounded-xl shadow-xl p-6 max-w-lg w-full mx-4">
                        <h3 class="text-lg font-semibold text-gray-900 mb-4">Referenzen extrahieren</h3>
                        <div class="mb-4">
                            <div class="w-full bg-gray-200 rounded-full h-3 mb-2">
                                <div class="bg-accent h-3 rounded-full transition-all duration-300" :style="{ width: refProgress.percent + '%' }"></div>
                            </div>
                            <p class="text-sm text-gray-600">{{ refProgress.message || 'Starte...' }}</p>
                            <p v-if="refProgress.current && refProgress.total" class="text-xs text-gray-400 mt-1">
                                Referenz {{ refProgress.current }} / {{ refProgress.total }}<span v-if="refProgress.found != null"> &middot; {{ refProgress.found }} von {{ refProgress.current }} erfolgreich geladen</span>
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
                        <h3 class="text-lg font-semibold text-gray-900 mb-4">Referenz bearbeiten</h3>
                        <div class="space-y-3">
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Titel</label>
                                <input v-model="editRefData.title" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Autoren</label>
                                <input v-model="editRefData.authors" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                            <div class="grid grid-cols-2 gap-3">
                                <div>
                                    <label class="block text-xs text-gray-500 mb-1">Jahr</label>
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
                            <button @click="editingRef = null" class="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">Abbrechen</button>
                            <button @click="saveReference" class="px-4 py-2 text-sm text-white bg-accent rounded-lg hover:bg-accent-ink">Speichern</button>
                        </div>
                    </div>
                </div>

                <!-- Add Reference Modal -->
                <div v-if="addingRef" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center">
                    <div class="bg-white rounded-xl shadow-xl p-6 max-w-lg w-full mx-4">
                        <h3 class="text-lg font-semibold text-gray-900 mb-4">Referenz manuell hinzufuegen</h3>
                        <div class="space-y-3">
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Titel *</label>
                                <input v-model="newRefData.title" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Autoren</label>
                                <input v-model="newRefData.authors" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                            </div>
                            <div class="grid grid-cols-2 gap-3">
                                <div>
                                    <label class="block text-xs text-gray-500 mb-1">Jahr</label>
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
                            <button @click="addingRef = false" class="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">Abbrechen</button>
                            <button @click="saveNewReference" :disabled="!newRefData.title" class="px-4 py-2 text-sm text-white bg-accent rounded-lg hover:bg-accent-ink disabled:opacity-40">Hinzufuegen</button>
                        </div>
                    </div>
                </div>

                <!-- Metadata Validation Proposal Modal (HITL review) -->
                <div v-if="validateProposal" class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center" @click.self="closeProposal">
                    <div class="bg-white rounded-xl shadow-xl max-w-2xl w-full mx-4 max-h-[90vh] flex flex-col">
                        <div class="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
                            <h3 class="text-lg font-semibold text-gray-900">Metadaten validieren &mdash; Vorschlaege pruefen</h3>
                            <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium"
                                  :class="validateProposal.confidence === 'high' ? 'bg-accent-soft text-accent-ink' : 'bg-accent-soft text-accent-ink'">
                                {{ validateProposal.confidence === 'high' ? 'Hohe' : 'Niedrige' }} Konfidenz
                            </span>
                        </div>

                        <div class="px-6 py-4 overflow-y-auto flex-1 space-y-4">
                            <!-- Warnings -->
                            <div v-if="validateProposal.warnings.length" class="bg-accent-soft border border-accent-soft rounded-lg p-3">
                                <p class="font-medium text-accent-ink text-sm mb-1">Hinweise</p>
                                <ul class="list-disc list-inside text-sm text-accent-ink space-y-0.5">
                                    <li v-for="(w, i) in validateProposal.warnings" :key="'w'+i">{{ w }}</li>
                                </ul>
                            </div>

                            <!-- Field changes -->
                            <div v-if="proposalFieldList.length">
                                <p class="font-medium text-gray-700 text-sm mb-2">Feldaenderungen</p>
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
                                            Aktuell: <span class="line-through">{{ f.current === null || f.current === '' ? '(leer)' : f.current }}</span>
                                        </div>
                                        <textarea v-if="f.field === 'abstract'" v-model="proposalEdits[f.field]"
                                                  rows="4"
                                                  class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none"></textarea>
                                        <input v-else v-model="proposalEdits[f.field]"
                                               class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent outline-none" />
                                    </div>
                                </div>
                            </div>
                            <div v-else class="text-sm text-gray-500 italic">Keine Feldaenderungen vorgeschlagen.</div>

                            <!-- Category suggestions -->
                            <div v-if="validateProposal.category_suggestions.length">
                                <p class="font-medium text-gray-700 text-sm mb-2">Kategorie-Vorschlaege</p>
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
                            <button @click="closeProposal" class="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">Abbrechen</button>
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
                         : '<p class="lb-notes-empty">Noch keine Notizen &mdash; hier klicken.</p>';
        },
        notesStatus() {
            return {
                dirty: 'ungespeichert…', saving: 'speichert…',
                saved: 'gespeichert', error: 'Speichern fehlgeschlagen',
            }[this.notesSaveState] || '';
        },
        proposalFieldList() {
            if (!this.validateProposal) return [];
            const labels = {
                title: 'Titel', authors: 'Autoren', year: 'Jahr', doi: 'DOI',
                isbn: 'ISBN', abstract: 'Abstract', journal: 'Journal',
                publisher: 'Verlag',
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
                console.error('Notizen speichern fehlgeschlagen:', e);
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
                alert('Fehler beim Oeffnen: ' + e.message);
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
                this.attachStatus = 'PDF wird angehaengt...';
                const formData = new FormData();
                formData.append('file', this.pendingPdfFile);
                const params = new URLSearchParams({
                    do_categories: 'false', do_chunks: 'false', detect_book: 'true',
                });
                const resp = await fetch(`/api/papers/${this.paper.id}/attach-pdf?${params}`, {
                    method: 'POST', body: formData,
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({ detail: 'Unbekannter Fehler' }));
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
                alert('PDF anhaengen fehlgeschlagen: ' + e.message);
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
                alert('Bitte gueltige Seiten angeben (von <= bis).');
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
                this.attachStatus = trim ? 'PDF wird zugeschnitten...' : 'Verarbeite...';
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
                    const err = await resp.json().catch(() => ({ detail: 'Kein OA-PDF gefunden' }));
                    throw new Error(err.detail || 'HTTP ' + resp.status);
                }
                await this.load();
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            } catch (e) {
                alert('Kein Open-Access-PDF gefunden: ' + e.message
                    + '\n\nNutze „Google Scholar" / „DOI öffnen", um die PDF zu finden, und lade sie dann über „PDF anhängen" hoch.');
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
                alert('BibTeX konnte nicht kopiert werden: ' + e.message);
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
            return c ? c.name : ('Kategorie #' + catId);
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
                    alert(data.message || 'Kein Abstract gefunden oder generiert.');
                }
            } catch (e) {
                console.error('Abstract generation error:', e);
                alert('Fehler beim Generieren des Abstracts.');
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
                alert('Fehler beim Speichern: ' + e.message);
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
                alert('Fehler: ' + e.message);
            }
        },
        async removeCategory(catId) {
            try {
                await api(`/api/papers/${this.paper.id}/categories/${catId}`, { method: 'DELETE' });
                await this.load();
                this.refreshSidebar();
            } catch (e) {
                alert('Fehler: ' + e.message);
            }
        },
        async deletePaper() {
            if (!confirm('Paper wirklich loeschen? Die PDF-Datei wird ebenfalls entfernt.')) return;
            try {
                await api(`/api/papers/${this.paper.id}`, { method: 'DELETE' });
                this.$router.push('/');
            } catch (e) {
                alert('Fehler: ' + e.message);
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
                                    message: evt.message || '',
                                    current: evt.current || 0,
                                    total: evt.total || 0,
                                    found: (evt.found !== undefined) ? evt.found : null,
                                    error: null,
                                };
                            } else if (evt.type === 'error') {
                                this.refProgress.error = evt.message;
                                this.refExtractionResult = { status: 'error', error: evt.message };
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
            if (!confirm('Referenzen erneut extrahieren? Die bestehenden Referenzen werden ueberschrieben.')) return;
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
                alert('Fehler beim Speichern: ' + e.message);
            }
        },
        async deleteRef(ref) {
            if (!confirm(`Referenz "${(ref.title || '').substring(0, 60)}" wirklich loeschen?`)) return;
            try {
                await api(`/api/papers/${this.paper.id}/references/${ref.id}`, { method: 'DELETE' });
                this.paperRefs = this.paperRefs.filter(r => r.id !== ref.id);
            } catch (e) {
                alert('Fehler: ' + e.message);
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
                alert('Fehler: ' + e.message);
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
                        <h2 class="text-xl font-semibold text-gray-900">Kategorien-Planner</h2>
                        <p class="text-sm text-gray-500">Kategorien per Drag & Drop organisieren, Beschreibungen und Keywords bearbeiten</p>
                    </div>
                </div>
                <button @click="showAddModal = true"
                        class="inline-flex items-center gap-1.5 bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                    <span v-html="icons.plus"></span>
                    Neue Kategorie
                </button>
            </div>

            <!-- Tab Switcher -->
            <div class="flex gap-1 mb-4 bg-gray-100 rounded-lg p-1 w-fit">
                <button @click="activeTab = 'tree'"
                        class="px-4 py-2 rounded-md text-sm font-medium transition-colors"
                        :class="activeTab === 'tree' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'">
                    Baumansicht
                </button>
                <button @click="activeTab = 'table'"
                        class="px-4 py-2 rounded-md text-sm font-medium transition-colors"
                        :class="activeTab === 'table' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'">
                    Tabellenansicht
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
                    <span class="text-xs text-gray-400">Oberste Ebene (hierher ziehen = Hauptkategorie)</span>
                </div>

                <!-- Tree Nodes -->
                <div class="p-2 min-h-[200px]">
                    <div v-if="tree.length === 0" class="text-center py-8 text-gray-400 text-sm">
                        Keine Kategorien vorhanden
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
                                <span class="text-xs text-gray-400 tabular-nums">{{ node.paper_count || 0 }} Paper</span>
                                <button @click.stop="confirmDelete(node)"
                                        class="p-1 text-gray-300 hover:text-accent rounded opacity-0 group-hover:opacity-100 transition-all"
                                        title="Loeschen">
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
                                            <span class="text-xs text-gray-400 tabular-nums">{{ child.paper_count || 0 }} Paper</span>
                                            <button @click.stop="confirmDelete(child)"
                                                    class="p-1 text-gray-300 hover:text-accent rounded opacity-0 group-hover:opacity-100 transition-all"
                                                    title="Loeschen">
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
                                                    <span class="text-xs text-gray-400 tabular-nums">{{ gc.paper_count || 0 }} Paper</span>
                                                    <button @click.stop="confirmDelete(gc)"
                                                            class="p-1 text-gray-300 hover:text-accent rounded opacity-0 group-hover:opacity-100 transition-all"
                                                            title="Loeschen">
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
                            <th class="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3 w-48">Oberkategorie</th>
                            <th class="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3">Beschreibung</th>
                            <th class="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3 w-64">Keywords</th>
                            <th class="text-center text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3 w-20">Paper</th>
                            <th class="text-center text-xs font-semibold text-gray-500 uppercase tracking-wider px-4 py-3 w-32">Aktionen</th>
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
                                    <option :value="null">— Keine —</option>
                                    <option v-for="opt in getParentOptions(cat)" :key="opt.id" :value="opt.id">{{ opt.name }}</option>
                                </select>
                            </td>
                            <td class="px-4 py-2.5">
                                <input v-if="editingCell === cat.id + '-description'"
                                       v-model="cat.description" @blur="saveField(cat, 'description')" @keydown.enter="saveField(cat, 'description')"
                                       class="w-full border border-accent-soft rounded px-2 py-1 text-sm focus:ring-2 focus:ring-accent outline-none"
                                       placeholder="Beschreibung eingeben..." />
                                <span v-else @click="startEdit(cat, 'description')"
                                      class="text-sm cursor-pointer hover:text-accent block truncate"
                                      :class="cat.description ? 'text-gray-600' : 'text-gray-300 italic'">
                                    {{ cat.description || 'Klicken zum Bearbeiten...' }}
                                </span>
                            </td>
                            <td class="px-4 py-2.5">
                                <input v-if="editingCell === cat.id + '-keywords'"
                                       v-model="cat.keywords" @blur="saveField(cat, 'keywords')" @keydown.enter="saveField(cat, 'keywords')"
                                       class="w-full border border-accent-soft rounded px-2 py-1 text-sm focus:ring-2 focus:ring-accent outline-none"
                                       placeholder="keyword1, keyword2, ..." />
                                <span v-else @click="startEdit(cat, 'keywords')"
                                      class="text-sm cursor-pointer hover:text-accent block truncate"
                                      :class="cat.keywords ? 'text-gray-600' : 'text-gray-300 italic'">
                                    {{ cat.keywords || 'Klicken zum Bearbeiten...' }}
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
                                            title="LLM-Vorschlag fuer Beschreibung & Keywords">
                                        <span v-html="icons.sparkle"></span>
                                        <span v-if="cat._llmLoading">...</span>
                                        <span v-else>LLM</span>
                                    </button>
                                    <button @click="confirmDelete(cat)"
                                            class="p-1 text-gray-300 hover:text-accent rounded transition-colors"
                                            title="Loeschen">
                                        <span v-html="icons.trash"></span>
                                    </button>
                                </div>
                            </td>
                        </tr>
                        <tr v-if="flatTableData.length === 0">
                            <td colspan="6" class="px-4 py-8 text-center text-sm text-gray-400">
                                Keine Kategorien vorhanden
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- Add Category Modal -->
            <div v-if="showAddModal" class="fixed inset-0 bg-black/30 flex items-center justify-center z-50"
                 @click.self="showAddModal = false">
                <div class="bg-white rounded-xl shadow-xl p-6 w-full max-w-md">
                    <h4 class="text-base font-semibold text-gray-900 mb-4">Neue Kategorie</h4>
                    <div class="space-y-3">
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">Name</label>
                            <input v-model="newCat.name" placeholder="z.B. Nachhaltigkeit"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">Oberkategorie</label>
                            <select v-model="newCat.parent_id"
                                    class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-accent focus:border-accent outline-none">
                                <option :value="null">Keine (Oberkategorie)</option>
                                <option v-for="c in flatCategories" :key="c.id" :value="c.id">{{ c.name }}</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">Beschreibung</label>
                            <input v-model="newCat.description" placeholder="Optionale Beschreibung"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">Keywords</label>
                            <input v-model="newCat.keywords" placeholder="kommagetrennt, z.B. LCA, EPD"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                    </div>
                    <div class="flex items-center gap-2 mt-5">
                        <button @click="llmSuggestNew" :disabled="!newCat.name || newCatLlmLoading"
                                class="inline-flex items-center gap-1.5 bg-accent-soft border border-accent-soft text-accent-ink px-3 py-2 rounded-lg text-sm font-medium hover:bg-accent-soft disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            <span v-html="icons.sparkle"></span>
                            <span v-if="newCatLlmLoading">LLM denkt...</span>
                            <span v-else>LLM-Vorschlag</span>
                        </button>
                        <div class="flex-1"></div>
                        <button @click="showAddModal = false"
                                class="px-4 py-2 text-sm text-gray-600 hover:text-gray-800 transition-colors">
                            Abbrechen
                        </button>
                        <button @click="createCategory" :disabled="!newCat.name"
                                class="bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            Erstellen
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
                this.dragStatus = this.dragNode.name + ' → Unterkategorie von ' + node.name;
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
                this.dragStatus = 'Fehler: Kann nicht in eigene Unterkategorie verschieben!';
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
                this.dragStatus = 'Fehler: ' + e.message;
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
                alert('Fehler beim Speichern: ' + e.message);
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
                alert('LLM-Vorschlag fehlgeschlagen: ' + e.message);
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
                alert('LLM-Vorschlag fehlgeschlagen: ' + e.message);
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
                alert('Fehler: ' + e.message);
            }
        },
        async confirmDelete(node) {
            const childCount = this.countDescendants(node);
            let msg = 'Kategorie "' + node.name + '" wirklich loeschen?';
            if (childCount > 0) {
                msg += ' ' + childCount + ' Unterkategorie(n) werden ebenfalls entfernt.';
            }
            if (!confirm(msg)) return;
            try {
                await api('/api/categories/' + node.id, { method: 'DELETE' });
                await this.load();
                window.dispatchEvent(new Event('refresh-sidebar'));
            } catch (e) {
                alert('Fehler: ' + e.message);
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
                PDFs per Drag & Drop hochladen oder aus dem Input-Ordner importieren.
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
                        {{ dragging ? 'PDFs hier ablegen' : 'PDFs hierher ziehen oder klicken' }}
                    </p>
                    <p class="text-xs text-gray-400">Mehrere PDF-Dateien gleichzeitig moeglich</p>
                </div>
            </div>

            <!-- Upload Results (completed papers) -->
            <div v-if="uploadResults.length" class="mb-6">
                <h3 class="text-sm font-semibold text-gray-900 mb-3">Upload-Ergebnisse</h3>
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
                            <router-link :to="{name: 'paper', params: {id: r.paper_id}}" class="text-accent hover:underline">Oeffnen</router-link>
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
                    Aktualisieren
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
                        Importieren
                    </button>
                </div>
            </div>

            <div v-else class="text-center py-16">
                <div class="text-5xl mb-3 opacity-30" v-html="icons.upload" style="display:inline-block;width:48px;height:48px;"></div>
                <p class="text-gray-400 text-sm">Keine PDFs im Input-Ordner vorhanden.</p>
            </div>

            <div v-if="importResults.length" class="mt-6 relative">
                <div class="flex items-center justify-between mb-3">
                    <h3 class="text-sm font-semibold text-gray-900">Import-Ergebnisse</h3>
                    <button @click="importResults = []" class="text-gray-400 hover:text-gray-600 transition-colors" title="Schliessen">
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
                        <h4 class="text-sm font-semibold text-gray-800 mb-3">Automatisierungen</h4>
                        <div class="space-y-2.5 mb-6">
                            <label class="flex items-center gap-3 cursor-pointer group">
                                <input type="checkbox" v-model="uploadOpts.doi" class="w-4 h-4 rounded border-gray-300 text-accent focus:ring-accent" />
                                <div>
                                    <span class="text-sm font-medium text-gray-700 group-hover:text-gray-900">DOI-Suche & CrossRef</span>
                                    <p class="text-xs text-gray-400">Sucht DOI im PDF und holt Metadaten von CrossRef</p>
                                </div>
                            </label>
                            <label class="flex items-center gap-3 cursor-pointer group">
                                <input type="checkbox" v-model="uploadOpts.validate" class="w-4 h-4 rounded border-gray-300 text-accent focus:ring-accent" />
                                <div>
                                    <span class="text-sm font-medium text-gray-700 group-hover:text-gray-900">KI-Metadaten-Validierung</span>
                                    <p class="text-xs text-gray-400">LLM prueft und korrigiert Titel, Autoren, Jahr</p>
                                </div>
                            </label>
                            <label class="flex items-center gap-3 cursor-pointer group">
                                <input type="checkbox" v-model="uploadOpts.categories" class="w-4 h-4 rounded border-gray-300 text-accent focus:ring-accent" />
                                <div>
                                    <span class="text-sm font-medium text-gray-700 group-hover:text-gray-900">Automatische Kategorisierung</span>
                                    <p class="text-xs text-gray-400">LLM weist passende Kategorien zu</p>
                                </div>
                            </label>
                            <label class="flex items-center gap-3 cursor-pointer group">
                                <input type="checkbox" v-model="uploadOpts.abstract" class="w-4 h-4 rounded border-gray-300 text-accent focus:ring-accent" />
                                <div>
                                    <span class="text-sm font-medium text-gray-700 group-hover:text-gray-900">Abstract suchen/generieren</span>
                                    <p class="text-xs text-gray-400">CrossRef, PDF-Extraktion oder KI-Zusammenfassung</p>
                                </div>
                            </label>
                        </div>
                        <div class="flex justify-end gap-2">
                            <button @click="closeUploadDialog" class="px-4 py-2 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50">Abbrechen</button>
                            <button @click="startSmartUpload" class="px-5 py-2 text-sm text-white bg-accent rounded-lg hover:bg-accent-ink font-medium">Importieren</button>
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
                                    <span class="text-xs text-accent">Bereits vorhanden als:</span>
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
                                        <span :class="res.doi ? 'text-gray-700' : 'text-gray-400'">DOI: {{ res.doi || 'nicht gefunden' }}</span>
                                    </div>
                                    <div class="flex items-center gap-1.5">
                                        <span class="w-2 h-2 rounded-full" :class="res.year ? 'bg-accent' : 'bg-gray-300'"></span>
                                        <span :class="res.year ? 'text-gray-700' : 'text-gray-400'">Jahr: {{ res.year || '-' }}</span>
                                    </div>
                                    <div class="flex items-center gap-1.5">
                                        <span class="w-2 h-2 rounded-full" :class="res.abstract ? 'bg-accent' : 'bg-gray-300'"></span>
                                        <span :class="res.abstract ? 'text-gray-700' : 'text-gray-400'">
                                            Abstract: {{ res.abstract ? (res.abstract_source === 'generated' ? 'KI-generiert' : res.abstract_source === 'pdf' ? 'KI-extrahiert' : 'vorhanden') : 'nicht gefunden' }}
                                        </span>
                                    </div>
                                    <div class="flex items-center gap-1.5">
                                        <span class="w-2 h-2 rounded-full" :class="res.journal ? 'bg-accent' : 'bg-gray-300'"></span>
                                        <span :class="res.journal ? 'text-gray-700' : 'text-gray-400'">Journal: {{ res.journal || '-' }}</span>
                                    </div>
                                </div>

                                <div v-if="res.categories && res.categories.length" class="ml-11 mb-3">
                                    <span class="text-xs text-gray-400">Kategorien: </span>
                                    <span v-for="cat in res.categories" :key="cat.id"
                                          class="inline-flex items-center bg-accent-soft text-accent-ink px-2 py-0.5 rounded-full text-xs mr-1">
                                        {{ cat.name }}
                                    </span>
                                </div>
                                <div v-else class="ml-11 mb-3 text-xs text-gray-400">Keine Kategorien zugewiesen</div>

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
                            <button @click="closeUploadDialog" class="px-4 py-2 text-sm text-white bg-accent rounded-lg hover:bg-accent-ink">Schliessen</button>
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
                this.uploadSteps = [{ message: files[i]._inputFolder ? 'Starte Import...' : 'Datei wird hochgeladen...', done: false }];
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
                        const err = await resp.json().catch(() => ({ detail: 'Unbekannter Fehler' }));
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
                                        existing.message = data.message;
                                        if (data.percent >= 95) existing.done = true;
                                    } else {
                                        // mark previous as done
                                        this.uploadSteps.forEach(s => { if (!s.done && !s.error) s.done = true; });
                                        this.uploadSteps.push({ step: data.step, message: data.message, done: false });
                                    }
                                } else if (data.type === 'complete') {
                                    this.uploadSteps.forEach(s => { if (!s.error) s.done = true; });
                                    this.uploadProgress = 100;
                                    lastResult = data;
                                } else if (data.type === 'error') {
                                    this.uploadSteps.forEach(s => { if (!s.done) s.error = true; });
                                    this.uploadSteps.push({ message: data.message, error: true });
                                    lastResult = { error: data.message, filename: files[i].name,
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
// CitaviPage Component (RIS Export/Import)
// =============================================================================

const CitaviPage = {
    template: `
        <div class="p-6 max-w-4xl mx-auto">
            <h2 class="text-xl font-semibold text-gray-900 mb-2">Citavi Export / Import</h2>
            <p class="text-sm text-gray-500 mb-6">
                Paper im RIS-Format exportieren oder importieren. Kompatibel mit Citavi, Zotero, Mendeley und anderen Literaturverwaltungen.
            </p>

            <!-- Export Section -->
            <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                <h3 class="text-base font-semibold text-gray-900 mb-1">Export</h3>
                <p class="text-sm text-gray-500 mb-4">Alle Paper als RIS-Datei herunterladen</p>
                <div class="flex items-center gap-3">
                    <a href="/api/export/ris" download="literatur_export.ris"
                       class="inline-flex items-center gap-2 bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                        <span v-html="icons.download"></span>
                        RIS-Datei exportieren
                    </a>
                    <span class="text-sm text-gray-400">{{ stats.paper_count || 0 }} Paper werden exportiert</span>
                </div>
            </section>

            <!-- Import Section -->
            <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
                <h3 class="text-base font-semibold text-gray-900 mb-1">Import</h3>
                <p class="text-sm text-gray-500 mb-4">RIS-Datei hochladen um Paper zu importieren</p>

                <div class="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center"
                     :class="{ 'border-accent bg-accent-soft': dragOver }"
                     @dragover.prevent="dragOver = true"
                     @dragleave="dragOver = false"
                     @drop.prevent="handleDrop">
                    <div v-if="!importing">
                        <span v-html="icons.upload" class="inline-block text-gray-400 mb-3" style="width:32px;height:32px;"></span>
                        <p class="text-sm text-gray-600 mb-2">RIS-Datei hierher ziehen oder auswaehlen</p>
                        <label class="inline-flex items-center gap-2 bg-white border border-gray-300 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-50 cursor-pointer transition-colors">
                            <span v-html="icons.upload"></span>
                            Datei auswaehlen
                            <input type="file" accept=".ris" @change="handleFileSelect" class="hidden" />
                        </label>
                    </div>
                    <div v-else class="flex items-center justify-center gap-3">
                        <div class="spinner"></div>
                        <span class="text-sm text-gray-600">Importiere...</span>
                    </div>
                </div>

                <!-- Import Results -->
                <div v-if="importResult" class="mt-5">
                    <div class="p-4 rounded-lg" :class="importResult.errors > 0 ? 'bg-accent-soft border border-accent-soft' : 'bg-accent-soft border border-accent-soft'">
                        <p class="font-medium text-sm mb-2" :class="importResult.errors > 0 ? 'text-accent-ink' : 'text-accent-ink'">
                            Import abgeschlossen
                        </p>
                        <div class="text-sm space-y-0.5" :class="importResult.errors > 0 ? 'text-accent-ink' : 'text-accent-ink'">
                            <p>Gesamt: {{ importResult.total }} Eintraege</p>
                            <p>Importiert: {{ importResult.imported }}</p>
                            <p v-if="importResult.skipped">Uebersprungen: {{ importResult.skipped }}</p>
                            <p v-if="importResult.errors">Fehler: {{ importResult.errors }}</p>
                        </div>
                    </div>
                    <div v-if="importResult.results && importResult.results.length" class="mt-3 space-y-1 max-h-64 overflow-y-auto">
                        <div v-for="(r, i) in importResult.results" :key="i"
                             class="flex items-center gap-3 py-2 px-3 rounded-lg text-sm"
                             :class="{
                                'bg-accent-soft text-accent-ink': r.status === 'ok',
                                'bg-accent-soft text-accent-ink': r.status === 'skipped',
                                'bg-accent-soft text-accent-ink': r.status === 'error',
                             }">
                            <span v-if="r.status === 'ok'" v-html="icons.check"></span>
                            <span v-else>-</span>
                            <span class="truncate flex-1">{{ r.title }}</span>
                            <span v-if="r.reason" class="text-xs flex-shrink-0">{{ r.reason }}</span>
                            <span v-if="r.error" class="text-xs flex-shrink-0">{{ r.error }}</span>
                        </div>
                    </div>
                </div>
            </section>
        </div>
    `,
    data() {
        return {
            icons,
            stats: {},
            importing: false,
            importResult: null,
            dragOver: false,
        };
    },
    async created() {
        try {
            this.stats = await api('/api/stats');
        } catch (e) {}
    },
    methods: {
        handleDrop(event) {
            this.dragOver = false;
            const file = event.dataTransfer.files[0];
            if (file && file.name.endsWith('.ris')) {
                this.uploadFile(file);
            }
        },
        handleFileSelect(event) {
            const file = event.target.files[0];
            if (file) {
                this.uploadFile(file);
            }
        },
        async uploadFile(file) {
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
                    const err = await resp.json().catch(() => ({ detail: 'Fehler' }));
                    throw new Error(err.detail || 'Import fehlgeschlagen');
                }
                this.importResult = await resp.json();
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            } catch (e) {
                alert('Import fehlgeschlagen: ' + e.message);
            }
            this.importing = false;
        },
    },
};


// =============================================================================
// SettingsPage Component
// =============================================================================

const SettingsPage = {
    template: `
        <div class="p-6 max-w-3xl mx-auto">
            <h2 class="text-xl font-semibold text-gray-900 mb-6">Einstellungen</h2>

            <!-- Tab Navigation -->
            <div class="flex border-b border-gray-200 mb-6 flex-wrap">
                <button @click="activeTab = 'general'"
                        class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px"
                        :class="activeTab === 'general' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'">
                    Allgemein
                </button>
                <button @click="activeTab = 'appearance'"
                        class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px"
                        :class="activeTab === 'appearance' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'">
                    Erscheinungsbild
                </button>
                <button @click="activeTab = 'columns'"
                        class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px"
                        :class="activeTab === 'columns' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'">
                    Spalten
                </button>
                <button @click="activeTab = 'export'"
                        class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px"
                        :class="activeTab === 'export' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'">
                    Export / Import
                </button>
                <button @click="activeTab = 'license'"
                        data-testid="license-tab"
                        class="px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px"
                        :class="activeTab === 'license' ? 'border-accent text-accent' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'">
                    Lizenz
                </button>
            </div>

            <!-- ========== Allgemein Tab ========== -->
            <div v-if="activeTab === 'general'">

                <!-- Wartung -->
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">Wartung</h3>
                    <p class="text-sm text-gray-500 mb-5">Datenbankwerte fuer alle vorhandenen Paper aktualisieren</p>

                    <!-- Komplett-Refresh -->
                    <div class="mb-5 pb-5 border-b border-gray-100">
                        <p class="text-sm font-medium text-gray-700 mb-1">Komplett-Refresh aller Paper</p>
                        <p class="text-xs text-gray-400 mb-3">Aktualisiert: Seitenanzahl · Metadaten (DOI/CrossRef) · Abstracts · fehlende Kategorien · OpenAlex Zitationen · RAG-Chunks</p>
                        <button @click="fullRefresh"
                                :disabled="fullRefreshRunning"
                                class="flex items-center gap-2 bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-50 transition-colors">
                            <div v-if="fullRefreshRunning" class="spinner" style="width:14px;height:14px;border-width:2px;border-color:white transparent transparent transparent"></div>
                            <span v-else v-html="icons.refresh"></span>
                            Komplett-Refresh starten
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
                                    <div class="text-gray-500">Seitenanzahl</div>
                                </div>
                                <div class="bg-gray-50 rounded-lg p-2 text-center">
                                    <div class="font-semibold text-gray-800">{{ fullRefreshStats.validated }}</div>
                                    <div class="text-gray-500">Metadaten</div>
                                </div>
                                <div class="bg-gray-50 rounded-lg p-2 text-center">
                                    <div class="font-semibold text-gray-800">{{ fullRefreshStats.abstracts }}</div>
                                    <div class="text-gray-500">Abstracts</div>
                                </div>
                                <div class="bg-gray-50 rounded-lg p-2 text-center">
                                    <div class="font-semibold text-gray-800">{{ fullRefreshStats.categorized }}</div>
                                    <div class="text-gray-500">Kategorisiert</div>
                                </div>
                                <div class="bg-gray-50 rounded-lg p-2 text-center">
                                    <div class="font-semibold text-gray-800">{{ fullRefreshStats.openalex }}</div>
                                    <div class="text-gray-500">OpenAlex</div>
                                </div>
                                <div class="bg-gray-50 rounded-lg p-2 text-center">
                                    <div class="font-semibold text-gray-800">{{ fullRefreshStats.chunks }}</div>
                                    <div class="text-gray-500">RAG-Chunks</div>
                                </div>
                            </div>
                            <p v-if="fullRefreshDone" class="text-sm text-accent font-medium mt-3">✓ Refresh abgeschlossen!</p>
                            <p v-if="fullRefreshStats && fullRefreshStats.errors > 0" class="text-xs text-accent mt-1">{{ fullRefreshStats.errors }} Fehler (Details im Server-Log)</p>
                        </div>
                    </div>

                    <!-- Nur Seitenanzahl -->
                    <div class="flex items-center gap-3">
                        <button @click="updatePageCounts"
                                :disabled="pageCountUpdating || fullRefreshRunning"
                                class="flex items-center gap-2 bg-gray-100 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-200 disabled:opacity-50 transition-colors">
                            <div v-if="pageCountUpdating" class="spinner" style="width:14px;height:14px;border-width:2px"></div>
                            <span v-else v-html="icons.refresh"></span>
                            Nur Seitenanzahl aktualisieren
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
                            Kategorie-Verknüpfungen neu aufbauen
                        </button>
                        <span v-if="rebuildLinksResult" class="text-sm" :class="rebuildLinksResult.ok ? 'text-accent' : 'text-accent'">
                            {{ rebuildLinksResult.msg }}
                        </span>
                    </div>

                    <!-- Semantischer Index -->
                    <div class="mt-3 pt-3 border-t border-gray-100">
                        <p class="text-sm font-medium text-gray-700 mb-1">Semantischer Index</p>
                        <p class="text-xs text-gray-400 mb-3">
                            <template v-if="embStatus && embStatus.model">
                                Modell {{ embStatus.model }} · Paper {{ embStatus.indexed }}/{{ embStatus.total }} · Textstellen {{ embStatus.chunks_indexed }}/{{ embStatus.chunks_total }}
                            </template>
                            <template v-else>
                                Kein Embedding-Modell konfiguriert – die semantische Suche bleibt aus (LLM_EMBED_MODEL setzen)
                            </template>
                        </p>
                        <div class="flex items-center gap-3 flex-wrap">
                            <button @click="reindexEmbeddings('papers')"
                                    :disabled="embRunning || fullRefreshRunning"
                                    class="flex items-center gap-2 bg-gray-100 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-200 disabled:opacity-50 transition-colors">
                                <div v-if="embRunning === 'papers'" class="spinner" style="width:14px;height:14px;border-width:2px"></div>
                                <span v-else>🧭</span>
                                Paper indexieren
                            </button>
                            <button @click="reindexEmbeddings('chunks')"
                                    :disabled="embRunning || fullRefreshRunning"
                                    class="flex items-center gap-2 bg-gray-100 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-200 disabled:opacity-50 transition-colors">
                                <div v-if="embRunning === 'chunks'" class="spinner" style="width:14px;height:14px;border-width:2px"></div>
                                <span v-else>🧩</span>
                                Textstellen indexieren
                            </button>
                            <span v-if="embResult" class="text-sm text-accent">{{ embResult.msg }}</span>
                        </div>
                        <p v-if="embRunning" class="text-xs text-gray-400 mt-2">
                            Laeuft – je nach Bibliotheksgroesse dauert das einige Minuten. Bereits Indexiertes wird uebersprungen.
                        </p>
                    </div>
                </section>

                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">Konfiguration</h3>
                    <p class="text-sm text-gray-500 mb-5">Werte aus der .env Datei bearbeiten</p>

                    <!-- PDF-Schutz Toggle -->
                    <div class="flex items-center justify-between mb-5 pb-5 border-b border-gray-100">
                        <div>
                            <p class="text-sm font-medium text-gray-700">PDF-Schutz beim Import entfernen</p>
                            <p class="text-xs text-gray-400 mt-0.5">Bearbeitungseinschraenkungen und Passwortschutz automatisch aufheben</p>
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
                            <select v-if="field.key === 'LLM_PROVIDER' && availableProviders.length"
                                    v-model="settings[field.key]"
                                    class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                                           focus:ring-2 focus:ring-accent focus:border-accent outline-none bg-white">
                                <option v-for="p in availableProviders" :key="p.id" :value="p.id">{{ p.label }}</option>
                            </select>
                            <select v-else-if="field.key === 'LLM_MODEL' && availableModels.length"
                                    v-model="settings[field.key]"
                                    class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                                           focus:ring-2 focus:ring-accent focus:border-accent outline-none bg-white">
                                <option v-for="opt in availableModels" :key="opt" :value="opt">{{ opt }}</option>
                            </select>
                            <select v-else-if="field.key === 'LLM_MODEL_FAST' && availableModels.length"
                                    v-model="settings[field.key]"
                                    class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                                           focus:ring-2 focus:ring-accent focus:border-accent outline-none bg-white">
                                <option value="">— wie Denkmodell —</option>
                                <option v-for="opt in availableModels" :key="opt" :value="opt">{{ opt }}</option>
                            </select>
                            <select v-else-if="field.key === 'LLM_EMBED_MODEL' && availableEmbedModels.length"
                                    v-model="settings[field.key]"
                                    class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                                           focus:ring-2 focus:ring-accent focus:border-accent outline-none bg-white">
                                <option value="">— deaktiviert (lexikalische Suche) —</option>
                                <option v-for="opt in availableEmbedModels" :key="opt" :value="opt">{{ opt }}</option>
                            </select>
                            <template v-else-if="field.key === 'LLM_EMBED_URL'">
                                <input v-model="settings[field.key]"
                                       list="embed-url-presets"
                                       :placeholder="field.placeholder"
                                       @change="loadEmbedModels(settings.LLM_EMBED_URL || '')"
                                       class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                                              focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                                <datalist id="embed-url-presets">
                                    <option v-for="p in embedUrlPresets" :key="p.id" :value="p.url">{{ p.label }}</option>
                                </datalist>
                            </template>
                            <input v-else
                                   v-model="settings[field.key]"
                                   :type="field.secret ? 'password' : 'text'"
                                   :placeholder="field.placeholder"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm
                                          focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                            <p class="text-xs text-gray-400 mt-1">{{ field.help }}</p>
                            <!-- Leeres Dropdown ohne Begruendung ist eine Sackgasse: der Grund
                                 steht sonst nur im Log, das im gebauten .exe niemand sieht. -->
                            <p v-if="field.key === 'LLM_MODEL' && !availableModels.length && modelsError"
                               class="text-xs text-red-500 mt-1 leading-relaxed">
                                Modell-Liste nicht abrufbar — bitte oben eintippen.<br>{{ modelsError }}
                            </p>
                            <div v-if="field.key === 'LLM_MODEL_FAST'" class="mt-2">
                                <button @click="suggestModels" :disabled="suggestingModels"
                                        class="text-xs px-3 py-1.5 rounded-lg border border-accent text-accent
                                               hover:bg-accent hover:text-white transition-colors disabled:opacity-50">
                                    <span v-if="suggestingModels">Wird ermittelt…</span>
                                    <span v-else>✨ Modelle vorschlagen</span>
                                </button>
                                <p v-if="modelSuggestion" class="text-xs text-gray-500 mt-2 leading-relaxed">
                                    <span class="font-medium">Vorschlag {{ modelSuggestion.source === 'llm' ? '(KI)' : '(Namensmuster)' }}:</span><br>
                                    Denken: <span class="font-mono">{{ modelSuggestion.reasoning }}</span>
                                    <template v-if="modelSuggestion.reasoning_reason"> — {{ modelSuggestion.reasoning_reason }}</template><br>
                                    Einfach: <span class="font-mono">{{ modelSuggestion.fast }}</span>
                                    <template v-if="modelSuggestion.fast_reason"> — {{ modelSuggestion.fast_reason }}</template>
                                </p>
                                <p v-if="modelSuggestError" class="text-xs text-red-500 mt-2">{{ modelSuggestError }}</p>
                            </div>
                        </div>
                    </div>

                    <div class="flex items-center gap-3 mt-5">
                        <button @click="saveSettings"
                                class="bg-accent text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                            Speichern
                        </button>
                        <span v-if="settingsSaved" class="text-accent text-sm save-success">Gespeichert!</span>
                    </div>
                </section>
            </div>

            <!-- ========== Erscheinungsbild Tab ========== -->
            <div v-if="activeTab === 'appearance'">
                <!-- Dark Mode -->
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-base font-semibold text-gray-900 mb-1">Dark Mode</h3>
                            <p class="text-sm text-gray-500">Dunkles Erscheinungsbild fuer die Anwendung</p>
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
                            <h3 class="text-base font-semibold text-gray-900 mb-1">Thesis-Analyse</h3>
                            <p class="text-sm text-gray-500">Zeigt im Menue den Reiter &quot;Thesis-Analyse&quot; an, mit dem Abschlussarbeiten geprueft werden koennen</p>
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
                    <h3 class="text-base font-semibold text-gray-900 mb-1">Icon</h3>
                    <p class="text-sm text-gray-500 mb-4">Benutzerdefiniertes Icon fuer Taskleiste und Menue</p>

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
                                    Icon hochladen
                                    <input type="file" accept=".png,.ico,.svg,.jpg,.jpeg,.webp" @change="uploadIcon" class="hidden" />
                                </label>
                                <button v-if="customIconPath" @click="deleteIcon"
                                        class="inline-flex items-center gap-2 bg-white border border-accent-soft text-accent px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-soft transition-colors">
                                    <span v-html="icons.trash"></span>
                                    Entfernen
                                </button>
                            </div>
                            <p class="text-xs text-gray-400">Erlaubte Formate: PNG, ICO, SVG, JPG, WEBP (max. 2 MB)</p>
                        </div>
                    </div>
                </section>

                <div class="mt-4 text-xs text-gray-400 text-center">
                    Aenderungen am Erscheinungsbild werden automatisch gespeichert.
                </div>
            </div>

            <!-- ========== Spalten Tab ========== -->
            <div v-if="activeTab === 'columns'">
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">Benutzerdefinierte Spalten</h3>
                    <p class="text-sm text-gray-500 mb-5">
                        Erstelle benutzerdefinierte Felder, die fuer alle Paper gelten.
                        Z.B. Notizen, Lesefortschritt, Bewertung etc.
                    </p>

                    <!-- Existing fields -->
                    <div v-if="customFields.length" class="space-y-2 mb-5">
                        <div v-for="cf in customFields" :key="cf.id"
                             class="flex items-center gap-3 bg-gray-50 rounded-lg p-3">
                            <span v-html="icons.columns" class="text-gray-400 flex-shrink-0"></span>
                            <div class="flex-1 min-w-0">
                                <p class="text-sm font-medium text-gray-900">{{ cf.name }}</p>
                                <p class="text-xs text-gray-400">
                                    Typ: {{ fieldTypeLabel(cf.field_type) }}
                                    <span v-if="cf.options"> &middot; Optionen: {{ cf.options }}</span>
                                </p>
                            </div>
                            <button @click="deleteField(cf)"
                                    class="p-1.5 text-gray-400 hover:text-accent rounded transition-colors">
                                <span v-html="icons.trash"></span>
                            </button>
                        </div>
                    </div>
                    <div v-else class="text-sm text-gray-400 mb-5">
                        Noch keine benutzerdefinierten Spalten angelegt.
                    </div>

                    <!-- Add new field -->
                    <div class="border-t border-gray-200 pt-5">
                        <h4 class="text-sm font-medium text-gray-900 mb-3">Neues Feld hinzufuegen</h4>
                        <div class="grid grid-cols-2 gap-3 mb-3">
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Name</label>
                                <input v-model="newField.name" placeholder="z.B. Notizen"
                                       class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                            </div>
                            <div>
                                <label class="block text-xs text-gray-500 mb-1">Typ</label>
                                <select v-model="newField.field_type"
                                        class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-accent focus:border-accent outline-none">
                                    <option value="text">Text / Notiz</option>
                                    <option value="number">Zahl</option>
                                    <option value="progress">Fortschritt (0-100%)</option>
                                    <option value="select">Auswahl</option>
                                </select>
                            </div>
                        </div>
                        <div v-if="newField.field_type === 'select'" class="mb-3">
                            <label class="block text-xs text-gray-500 mb-1">Optionen (kommagetrennt)</label>
                            <input v-model="newField.options" placeholder="z.B. Gut, Mittel, Schlecht"
                                   class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none" />
                        </div>
                        <button @click="createField" :disabled="!newField.name"
                                class="inline-flex items-center gap-1.5 bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            <span v-html="icons.plus"></span>
                            Feld erstellen
                        </button>
                    </div>
                </section>
            </div>

            <!-- ========== Export/Import Tab ========== -->
            <div v-if="activeTab === 'export'">
                <!-- Export Section -->
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">RIS Export</h3>
                    <p class="text-sm text-gray-500 mb-4">Paper als RIS-Datei exportieren (kompatibel mit Citavi, Zotero, Mendeley)</p>

                    <!-- Export Filters -->
                    <div class="grid grid-cols-2 gap-3 mb-4">
                        <div>
                            <label class="block text-xs text-gray-500 mb-1">Kategorie</label>
                            <select v-model="exportCategoryId" @change="loadExportPreview"
                                    class="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm bg-white focus:ring-2 focus:ring-accent focus:border-accent outline-none">
                                <option value="">Alle Kategorien</option>
                                <option v-for="c in allCategories" :key="c.id" :value="c.id">{{ c.name }}</option>
                            </select>
                        </div>
                    </div>

                    <div class="flex items-center gap-3 mb-4">
                        <a :href="exportUrl" download="literatur_export.ris"
                           class="inline-flex items-center gap-2 bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                            <span v-html="icons.download"></span>
                            RIS-Datei exportieren
                        </a>
                        <a :href="bibtexExportUrl" download="literatur_export.bib"
                           class="inline-flex items-center gap-2 bg-white border border-accent text-accent px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-soft transition-colors">
                            <span v-html="icons.download"></span>
                            BibTeX exportieren (.bib)
                        </a>
                        <span class="text-sm font-semibold text-gray-700">{{ exportPreview.count ?? stats.paper_count ?? 0 }} Paper</span>
                        <span v-if="exportCategoryId" class="text-xs text-gray-400">(gefiltert)</span>
                    </div>

                    <!-- Export Preview -->
                    <div v-if="exportPreview.papers && exportPreview.papers.length > 0" class="border border-gray-200 rounded-lg overflow-hidden">
                        <div class="bg-gray-50 px-4 py-2 border-b border-gray-200 flex items-center justify-between">
                            <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">Vorschau ({{ exportPreview.count }} Paper)</span>
                            <button @click="exportPreviewExpanded = !exportPreviewExpanded" class="text-xs text-accent hover:text-accent-ink">
                                {{ exportPreviewExpanded ? 'Einklappen' : 'Alle anzeigen' }}
                            </button>
                        </div>
                        <div class="max-h-64 overflow-y-auto" :class="{ 'max-h-none': exportPreviewExpanded }">
                            <table class="w-full text-sm">
                                <tbody>
                                    <tr v-for="paper in exportPreview.papers" :key="paper.id" class="border-b border-gray-100 hover:bg-gray-50">
                                        <td class="px-4 py-1.5 text-gray-800 truncate max-w-md">{{ paper.title || 'Kein Titel' }}</td>
                                        <td class="px-4 py-1.5 text-gray-500 truncate max-w-[180px]">{{ paper.authors || '' }}</td>
                                        <td class="px-4 py-1.5 text-gray-400 w-16 text-center">{{ paper.year || '-' }}</td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </section>

                <!-- Import Section -->
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">RIS Import</h3>
                    <p class="text-sm text-gray-500 mb-4">RIS-Datei hochladen um Paper zu importieren</p>

                    <div class="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center"
                         :class="{ 'border-accent bg-accent-soft': dragOver }"
                         @dragover.prevent="dragOver = true"
                         @dragleave="dragOver = false"
                         @drop.prevent="handleDrop">
                        <div v-if="!importing">
                            <span v-html="icons.upload" class="inline-block text-gray-400 mb-3" style="width:32px;height:32px;"></span>
                            <p class="text-sm text-gray-600 mb-2">RIS-Datei hierher ziehen oder auswaehlen</p>
                            <label class="inline-flex items-center gap-2 bg-white border border-gray-300 text-gray-700 px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-50 cursor-pointer transition-colors">
                                <span v-html="icons.upload"></span>
                                Datei auswaehlen
                                <input type="file" accept=".ris" @change="handleFileSelect" class="hidden" />
                            </label>
                        </div>
                        <div v-else class="flex items-center justify-center gap-3">
                            <div class="spinner"></div>
                            <span class="text-sm text-gray-600">Importiere...</span>
                        </div>
                    </div>

                    <div v-if="importResult" class="mt-5">
                        <div class="p-4 rounded-lg" :class="importResult.errors > 0 ? 'bg-accent-soft border border-accent-soft' : 'bg-accent-soft border border-accent-soft'">
                            <p class="font-medium text-sm mb-2" :class="importResult.errors > 0 ? 'text-accent-ink' : 'text-accent-ink'">
                                Import abgeschlossen
                            </p>
                            <div class="text-sm space-y-0.5" :class="importResult.errors > 0 ? 'text-accent-ink' : 'text-accent-ink'">
                                <p>Gesamt: {{ importResult.total }} Eintraege</p>
                                <p>Importiert: {{ importResult.imported }}</p>
                                <p v-if="importResult.skipped">Uebersprungen: {{ importResult.skipped }}</p>
                                <p v-if="importResult.errors">Fehler: {{ importResult.errors }}</p>
                            </div>
                        </div>
                    </div>
                </section>
            </div>

            <!-- ========== Lizenz Tab (ADR-0015) ========== -->
            <div v-if="activeTab === 'license'" data-testid="license-panel">
                <section class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 mb-6">
                    <h3 class="text-base font-semibold text-gray-900 mb-1">Lizenzschlüssel</h3>

                    <!-- Aktiviert: einmal geprüft, danach nie wieder online. -->
                    <div v-if="licenseActivated" data-testid="license-activated"
                         class="flex items-center gap-2 mt-4 text-sm text-accent-ink bg-accent-soft border border-accent-soft rounded-lg px-4 py-3">
                        <span>✓</span>
                        <span class="font-medium">Lizenz aktiviert<span v-if="licenseKeyMasked"> — Schlüssel {{ licenseKeyMasked }}</span>.</span>
                    </div>
                    <p v-if="licenseActivated" class="text-sm text-gray-500 mt-3">
                        Der Schlüssel wurde einmalig geprüft. LocalBib fragt ihn nie wieder online ab —
                        diese Installation funktioniert dauerhaft offline.
                    </p>

                    <!-- Noch nicht aktiviert: Eingabe + Kauflink. -->
                    <template v-else>
                        <!-- Laufende Testphase (#144). Fehlt sie — Quellinstallation —
                             steht hier nichts: dort gibt es nichts abzuzaehlen. -->
                        <p v-if="licenseTrial && !licenseTrial.expired"
                           data-testid="license-trial"
                           class="text-sm text-accent-ink bg-accent-soft border border-accent-soft rounded-lg px-4 py-3 mb-4">
                            Testphase läuft — noch {{ licenseTrial.days_remaining }}
                            {{ licenseTrial.days_remaining === 1 ? 'Tag' : 'Tage' }}.
                        </p>
                        <!-- Altbestand aus dem früheren Shop: der Weg zum kostenlosen
                             Code steht hier, nicht erst hinter einer Sperre. -->
                        <p v-if="licenseLegacyNote"
                           data-testid="license-legacy-note"
                           class="text-sm bg-amber-50 border border-amber-200 text-amber-900 rounded-lg px-4 py-3 mb-4">
                            {{ licenseLegacyNote }}
                        </p>
                        <p class="text-sm text-gray-500 mb-5">
                            Gib den Lizenzschlüssel ein, den du nach dem Kauf per E-Mail bekommen hast.
                            Er wird genau einmal geprüft und gilt für zwei Geräte.
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
                                <span v-if="licenseLoading">Aktiviere…</span>
                                <span v-else>Aktivieren</span>
                            </button>
                        </div>
                        <p v-if="licenseCheckoutUrl" class="text-sm text-gray-500">
                            Noch keine Lizenz?
                            <a :href="licenseCheckoutUrl" target="_blank" rel="noopener"
                               data-testid="license-buy-link"
                               class="text-accent underline">Lizenz kaufen</a>
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
            fields: [
                {
                    key: 'LITERATUR_BASE_DIR', label: 'Basis-Verzeichnis', secret: false,
                    placeholder: 'C:\\Users\\...\\Bibliothek', help: 'Ordner mit input/, all/, kategorien/ und literatur.db (Neustart noetig)'
                },
                {
                    key: 'LINK_MODE', label: 'Verknuepfungs-Modus', secret: false,
                    placeholder: 'symlink', help: 'symlink, hardlink oder copy (fuer OneDrive: copy empfohlen)'
                },
                {
                    key: 'LLM_PROVIDER', label: 'LLM Anbieter', secret: false,
                    placeholder: 'kiconnect', help: 'Anbieter waehlen – OpenAI, OpenRouter, Groq, DeepSeek, Mistral, KI Connect oder Custom'
                },
                {
                    key: 'LLM_API_KEY', label: 'API Key', secret: true,
                    placeholder: 'Dein API Key', help: 'API-Key des gewaehlten Anbieters (bei Custom/Ollama ggf. leer lassen)'
                },
                {
                    key: 'LLM_BASE_URL', label: 'Basis-URL (nur Custom)', secret: false,
                    placeholder: 'http://localhost:11434/v1', help: 'Nur fuer Anbieter "Custom" – OpenAI-kompatible Basis-URL ohne /chat/completions'
                },
                {
                    key: 'LLM_MODEL', label: 'Modell für Denkaufgaben', secret: false,
                    placeholder: 'gpt-4o', help: 'Wird für Chat, Analyse & Dissertations-Auswertung genutzt (starkes Modell empfohlen)',
                    options: []
                },
                {
                    key: 'LLM_MODEL_FAST', label: 'Modell für einfache Aufgaben', secret: false,
                    placeholder: '', help: 'Wird für Extraktion, Kategorisierung & Metadaten genutzt – schnelleres/günstigeres Modell empfohlen',
                    options: []
                },
                {
                    key: 'LLM_EMBED_MODEL', label: 'Embedding-Modell', secret: false,
                    placeholder: 'qwen3-embedding-8b', help: 'Fuer semantische Suche & Clustering. Leer lassen -> keine Embeddings, App faellt auf lexikalische Suche zurueck'
                },
                {
                    key: 'LLM_EMBED_URL', label: 'Embedding-URL (optional)', secret: false,
                    placeholder: 'http://localhost:11434/v1/embeddings', help: 'Ueberschreibt die aus der LLM-Basis-URL abgeleitete /embeddings-Adresse, z.B. fuer Ollama ohne KI-Connect-Zugriff'
                },
                {
                    key: 'CROSSREF_MAILTO', label: 'CrossRef Email', secret: false,
                    placeholder: 'deine@email.de', help: 'Email fuer CrossRef API (empfohlen fuer bessere Rate-Limits)'
                },
                {
                    key: 'OPENALEX_API_KEY', label: 'OpenAlex API Key (optional)', secret: true,
                    placeholder: 'Nur noetig bei hohem Volumen',
                    help: 'Optionaler Premium-Key von openalex.org. Hebt das Rate-Limit an (Premium-Pool). Leer lassen -> kostenloser Polite-Pool ueber die CrossRef-Email. Nur einfuegen, wenn zusaetzliches Budget gebraucht wird.'
                },
                {
                    key: 'WATCH_INTERVAL', label: 'Watch-Intervall (Sekunden)', secret: false,
                    placeholder: '5', help: 'Wie oft der Watchdog nach neuen PDFs im Input-Ordner schaut'
                },
                {
                    key: 'MAX_OCR_PAGES', label: 'Max. OCR-Seiten', secret: false,
                    placeholder: '50000', help: 'Maximale Seitenzahl fuer Text-Extraktion aus PDFs (Standard: alle Seiten)'
                },
            ],
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
            availableModels: [],
            modelsError: '',
            availableEmbedModels: [],
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
        // Vorschlaege fuer das Embedding-URL-Feld: /embeddings-Endpunkt je
        // Provider-Preset (Custom ohne base_url faellt raus).
        embedUrlPresets() {
            return this.availableProviders
                .filter((p) => p.base_url)
                .map((p) => ({ id: p.id, label: p.label, url: p.base_url.replace(/\/+$/, '') + '/embeddings' }));
        },
    },
    async created() {
        await Promise.all([
            this.loadSettings(),
            this.loadIcon(),
            this.loadCustomFields(),
            this.loadExportMeta(),
            this.loadLicenseStatus(),
            this.loadAvailableModels(),
            this.loadEmbedModels(),
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
        async loadAvailableModels() {
            try {
                const data = await api('/api/llm/models');
                this.availableModels = data.models || [];
                this.modelsError = data.error || '';
            } catch (e) {
                console.error('Models load error:', e);
                this.modelsError = 'Modell-Liste nicht abrufbar (Server nicht erreichbar).';
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
        // `url === undefined` -> gespeicherte Konfiguration; sonst die (noch
        // ungespeicherte) Eingabe aus dem Embedding-URL-Feld, leer = explizit
        // kein Override (Provider-Liste).
        async loadEmbedModels(url) {
            try {
                const q = url === undefined ? '' : `?url=${encodeURIComponent(url)}`;
                const data = await api('/api/llm/embed-models' + q);
                this.availableEmbedModels = data.models || [];
            } catch (e) {
                console.error('Embed-Models load error:', e);
            }
        },
        async suggestModels() {
            this.suggestingModels = true;
            this.modelSuggestError = '';
            try {
                const data = await api('/api/llm/suggest-models', { method: 'POST' });
                if (data.suggestion) {
                    this.modelSuggestion = data.suggestion;
                    // Dropdowns nur vorbefuellen – Nutzer speichert selbst.
                    this.settings.LLM_MODEL = data.suggestion.reasoning;
                    this.settings.LLM_MODEL_FAST = data.suggestion.fast;
                } else {
                    this.modelSuggestion = null;
                    this.modelSuggestError = data.error || 'Kein Vorschlag möglich.';
                }
            } catch (e) {
                this.modelSuggestError = 'Fehler: ' + e.message;
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
                // Modelle des (ggf. gewechselten) Anbieters neu laden
                await this.loadAvailableModels();
            } catch (e) {
                alert('Fehler beim Speichern: ' + e.message);
            }
        },
        async updatePageCounts() {
            this.pageCountUpdating = true;
            this.pageCountResult = null;
            try {
                const res = await api('/api/maintenance/update-page-counts', { method: 'POST' });
                this.pageCountResult = { ok: true, msg: `✓ ${res.updated} Paper aktualisiert, ${res.failed} fehlgeschlagen` };
            } catch (e) {
                this.pageCountResult = { ok: false, msg: 'Fehler: ' + e.message };
            } finally {
                this.pageCountUpdating = false;
            }
        },
        async rebuildLinks() {
            this.rebuildLinksRunning = true;
            this.rebuildLinksResult = null;
            try {
                const res = await api('/api/maintenance/rebuild-links', { method: 'POST' });
                this.rebuildLinksResult = { ok: true, msg: `✓ ${res.rebuilt} Verknüpfungen neu erstellt (${res.removed} alte entfernt)` };
            } catch (e) {
                this.rebuildLinksResult = { ok: false, msg: 'Fehler: ' + e.message };
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
                    this.embResult = { ok: false, msg: 'Kein Embedding-Modell konfiguriert' };
                } else {
                    const what = scope === 'chunks' ? 'Textstellen' : 'Paper';
                    let msg = `✓ ${res.indexed} ${what} indexiert, ${res.skipped} übersprungen`;
                    if (res.errors > 0) msg += `, ${res.errors} fehlgeschlagen (erneut ausführbar)`;
                    this.embResult = { ok: res.errors === 0, msg };
                }
                await this.loadEmbeddingStatus();
            } catch (e) {
                this.embResult = { ok: false, msg: 'Fehler: ' + e.message };
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
                this.fullRefreshMessage = 'Fehler: ' + e.message;
            } finally {
                this.fullRefreshRunning = false;
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
                alert('Fehler beim Speichern: ' + e.message);
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
                    const err = await resp.json().catch(() => ({ detail: 'Fehler' }));
                    throw new Error(err.detail);
                }
                const data = await resp.json();
                this.customIconPath = data.path;
                setFavicon(data.path);
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            } catch (e) {
                alert('Upload fehlgeschlagen: ' + e.message);
            }
        },
        async deleteIcon() {
            try {
                await api('/api/appearance/icon', { method: 'DELETE' });
                this.customIconPath = null;
                setFavicon(null);
                window.dispatchEvent(new CustomEvent('refresh-sidebar'));
            } catch (e) {
                alert('Fehler: ' + e.message);
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
                alert('Fehler: ' + e.message);
            }
        },
        async deleteField(cf) {
            if (!confirm(`Feld "${cf.name}" wirklich loeschen? Alle gespeicherten Werte gehen verloren.`)) return;
            try {
                await api(`/api/custom-fields/${cf.id}`, { method: 'DELETE' });
                await this.loadCustomFields();
            } catch (e) {
                alert('Fehler: ' + e.message);
            }
        },
        fieldTypeLabel(type) {
            const labels = { text: 'Text / Notiz', number: 'Zahl', progress: 'Fortschritt', select: 'Auswahl' };
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
                    const err = await resp.json().catch(() => ({ detail: 'Fehler' }));
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
            this.licenseLegacyNote = data.legacy_note || '';
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
                    this.licenseMessage = 'Lizenz aktiviert. Vielen Dank!';
                    window.dispatchEvent(new CustomEvent('license-activated'));
                } else {
                    this.licenseError = true;
                    this.licenseMessage = result.error || 'Aktivierung fehlgeschlagen.';
                }
            } catch(e) {
                this.licenseError = true;
                this.licenseMessage = 'Aktivierung fehlgeschlagen. Bitte versuche es erneut.';
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
                    <span>🆕 Neue Version verfügbar: <strong>{{ latestVersion }}</strong></span>
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
                            <span v-if="updateRunning">Aktualisiere…</span>
                            <span v-else>Jetzt aktualisieren</span>
                        </button>
                        <a v-if="downloadUrl"
                           :href="downloadUrl" target="_blank" rel="noopener"
                           data-testid="update-manual-link"
                           class="text-accent-ink underline hover:text-accent-ink font-medium">
                            {{ installerUrl ? 'Installer herunterladen' : 'Release-Seite öffnen' }}
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
                            Jetzt kaufen
                        </a>
                    </div>
                    <button @click="dismissBanner"
                            class="hover:text-white transition-colors ml-4 text-lg leading-none"
                            style="color:rgba(255,255,255,0.75);"
                            aria-label="Schließen">×</button>
                </div>
                <!-- LLM-Funktionen aus, weil kein Anbieter konfiguriert ist (#140).
                     Kein Fehler, nur eine Ansage samt Weg dorthin. -->
                <div v-if="llmOffHintVisible"
                     data-testid="llm-off-hint"
                     class="px-4 py-2 text-sm flex items-center justify-between flex-shrink-0 bg-amber-50 border-b border-amber-200 text-amber-900">
                    <span>
                        LLM-Funktionen sind aus — es ist kein Anbieter hinterlegt.
                        <a href="#/settings" class="underline font-medium">Jetzt einrichten</a>
                    </span>
                    <button @click="llmOffHintVisible = false"
                            class="ml-4 text-lg leading-none text-amber-700 hover:text-amber-900"
                            aria-label="Schließen">×</button>
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
                                Freigeschaltet — danke für deine Unterstützung!
                            </h2>
                            <p class="text-sm text-gray-500 mb-3">
                                LocalBib gehört jetzt dir, auf diesem Rechner dauerhaft und ohne
                                weitere Prüfung.
                            </p>
                            <p v-if="gateConfirmKey" class="text-sm text-gray-700 mb-3">
                                Schlüssel <code data-testid="license-gate-confirm-key">{{ gateConfirmKey }}</code>
                            </p>
                            <!-- Nur wenn die Aktivierungsantwort die Zahl tatsaechlich
                                 mitliefert (#156) — geraten wird hier nichts, das Limit
                                 setzt Polar durch. -->
                            <p v-if="gateConfirmRemaining !== null"
                               data-testid="license-gate-confirm-remaining"
                               class="text-sm text-gray-500 mb-3">
                                Noch {{ gateConfirmRemaining }} von 2 Geräten frei.
                            </p>
                            <p class="text-sm text-gray-500 italic mb-5">Viel Spaß beim Arbeiten.</p>
                            <div class="flex justify-end">
                                <button @click="dismissActivationConfirm"
                                        data-testid="license-gate-confirm-dismiss"
                                        class="bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                                    Los geht's
                                </button>
                            </div>
                        </div>
                    </template>
                    <template v-else>
                        <h2 class="text-lg font-semibold text-gray-900 mb-1">Testphase beendet</h2>
                        <p class="text-sm text-gray-500 mb-5">
                            Die 14 Tage sind um. Deine Bibliothek und alle Dateien bleiben
                            unverändert auf deiner Festplatte — zum Weiterarbeiten in dieser
                            Installation brauchst du einen Lizenzschlüssel (einmalig {{ licensePriceDisplay }},
                            zwei Geräte).
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
                                <span v-if="gateLoading">Aktiviere…</span>
                                <span v-else>Aktivieren</span>
                            </button>
                        </div>

                        <p v-if="gateMessage"
                           data-testid="license-gate-message"
                           class="text-sm mb-3 text-red-600">{{ gateMessage }}</p>

                        <p class="text-sm text-gray-500">
                            Noch keine Lizenz?
                            <a v-if="bannerUrl" :href="bannerUrl" target="_blank" rel="noopener"
                               data-testid="license-gate-buy"
                               class="text-accent underline">Lizenz kaufen</a>
                        </p>
                        <p class="text-xs text-gray-400 mt-4">
                            LocalBib ist quelloffen (AGPL): aus dem Quellcode gestartet läuft es
                            ohne Schlüssel und ohne Testphase weiter.
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
                    <h2 class="text-lg font-semibold text-gray-900 mb-1">Willkommen bei LocalBib</h2>
                    <p class="text-sm text-gray-500 mb-5">
                        LocalBib nutzt deinen eigenen LLM-Zugang — es werden keine Schluessel
                        mitgeliefert und nichts ueber fremde Server geschickt. Du kannst das
                        jetzt einrichten oder spaeter in den Einstellungen.
                    </p>

                    <label class="block text-xs font-medium text-gray-700 mb-1">LLM-Anbieter</label>
                    <select v-model="onboardingProvider"
                            data-testid="onboarding-provider"
                            class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4">
                        <option value="">— spaeter einrichten —</option>
                        <option v-for="p in onboardingProviders" :key="p.id" :value="p.id">{{ p.label }}</option>
                    </select>

                    <label class="block text-xs font-medium text-gray-700 mb-1">API-Key</label>
                    <input v-model="onboardingApiKey"
                           data-testid="onboarding-api-key"
                           type="password" placeholder="sk-…"
                           class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4">

                    <label class="block text-xs font-medium text-gray-700 mb-1">
                        E-Mail fuer hoeflichen CrossRef-/OpenAlex-Zugriff (optional)
                    </label>
                    <input v-model="onboardingMailto"
                           data-testid="onboarding-mailto"
                           type="email" placeholder="du@uni.example"
                           class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-1">
                    <p class="text-xs text-gray-400 mb-5">
                        Wird nur als Kennung an CrossRef/OpenAlex mitgeschickt (bessere
                        Rate-Limits). Leer lassen -> es wird keine Adresse gesendet.
                    </p>

                    <div class="flex items-center justify-end gap-3">
                        <button @click="skipOnboarding"
                                data-testid="onboarding-skip"
                                class="text-sm text-gray-500 hover:text-gray-700">
                            Spaeter einrichten
                        </button>
                        <button @click="saveOnboarding"
                                data-testid="onboarding-save"
                                :disabled="onboardingSaving"
                                class="bg-accent text-white text-sm font-semibold px-4 py-2 rounded-lg hover:bg-accent-ink transition-colors disabled:opacity-50">
                            Speichern
                        </button>
                    </div>
                </div>
            </div>
            <!-- Global drag-drop overlay (shown outside ImportPage) -->
            <div v-if="globalDragging" class="fixed inset-0 bg-accent/20 z-40 flex items-center justify-center pointer-events-none">
                <div class="bg-white rounded-2xl shadow-xl p-8 text-center pointer-events-none">
                    <div class="w-16 h-16 mx-auto mb-3 bg-accent-soft text-accent rounded-full flex items-center justify-center">
                        <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                    </div>
                    <p class="text-lg font-semibold text-gray-900">PDFs hier ablegen</p>
                    <p class="text-sm text-gray-500 mt-1">Dateien werden automatisch importiert</p>
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
            licensePriceDisplay: '',
            gateKey: '',
            gateLoading: false,
            gateMessage: '',
            gateConfirming: false,
            gateConfirmKey: '',
            gateConfirmRemaining: null,
            bannerVisible: false,
            bannerUrl: '',
            onboardingVisible: false,
            llmOffHintVisible: false,
            onboardingSaving: false,
            onboardingProvider: '',
            onboardingApiKey: '',
            onboardingMailto: '',
            onboardingProviders: ONBOARDING_FALLBACK_PROVIDERS,
        };
    },
    computed: {
        // Der Kaufhinweis spiegelt die Testphase (#144). Ohne Testphase —
        // Quellinstallation oder bereits aktiviert — bleibt der neutrale Satz.
        bannerMessage() {
            const t = this.licenseTrial;
            const price = this.licensePriceDisplay;
            if (t && !t.expired) {
                const days = t.days_remaining;
                const unit = days === 1 ? 'Tag' : 'Tage';
                return `✨ Testphase: noch ${days} ${unit}. LocalBib freischalten — einmalig ${price}, lebenslange Updates.`;
            }
            return `✨ LocalBib als fertige Installation kaufen — einmalig ${price}, lebenslange Updates.`;
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
            this.updateMessage = 'Update wird geladen…';
            try {
                const result = await api('/api/update/install', { method: 'POST' });
                this.updateMessage = (result && result.message)
                    || 'Update wird installiert — LocalBib startet gleich neu.';
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
            this.licenseLegacyNote = data.legacy_note || '';
            if (data.price_display) this.licensePriceDisplay = data.price_display;
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
                    this.gateMessage = result.error || 'Aktivierung fehlgeschlagen.';
                }
            } catch(e) {
                this.gateMessage = 'Aktivierung fehlgeschlagen. Bitte versuche es erneut.';
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
            try {
                settings = await api('/api/settings') || {};
            } catch(e) { return; /* silent: nie den Start blockieren */ }
            const hasKey = !!(settings.LLM_API_KEY || settings.KICONNECT_API_KEY);
            if (settings.ONBOARDING_COMPLETED === 'true') {
                // Schon gefragt: nicht erneut fragen, aber sagen, dass die
                // LLM-Funktionen aus sind, solange kein Anbieter hinterlegt ist.
                this.llmOffHintVisible = !hasKey;
                return;
            }
            // Bestehende Installationen mit konfiguriertem Key nie behelligen.
            if (hasKey) return;
            this.onboardingMailto = settings.CROSSREF_MAILTO || '';
            this.onboardingVisible = true;
            try {
                const data = await api('/api/llm/providers');
                if (data && data.providers && data.providers.length) {
                    this.onboardingProviders = data.providers;
                }
            } catch(e) { /* Fallback-Liste bleibt stehen */ }
        },
        async persistOnboarding(payload) {
            this.onboardingSaving = true;
            try {
                await api('/api/settings', {
                    method: 'PUT',
                    body: JSON.stringify({ ...payload, ONBOARDING_COMPLETED: 'true' }),
                });
            } catch(e) { /* silent: der Dialog darf nie zur Sackgasse werden */ }
            this.onboardingSaving = false;
            this.onboardingVisible = false;
            // Ohne Key bleiben die LLM-Funktionen aus — das sagt der Hinweis.
            this.llmOffHintVisible = !payload.LLM_API_KEY;
        },
        skipOnboarding() {
            // Nur merken, dass gefragt wurde — nichts konfigurieren.
            return this.persistOnboarding({});
        },
        saveOnboarding() {
            const payload = {};
            if (this.onboardingProvider) {
                payload.LLM_PROVIDER = this.onboardingProvider;
                payload.LLM_API_KEY = this.onboardingApiKey || '';
            }
            payload.CROSSREF_MAILTO = (this.onboardingMailto || '').trim();
            return this.persistOnboarding(payload);
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
                Zurueck
            </button>
            <div class="flex items-center justify-between mb-4 flex-shrink-0">
                <div>
                    <h2 class="text-xl font-semibold text-gray-900 flex items-center gap-2">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-6 h-6 text-accent" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                        Research Chat
                    </h2>
                    <p class="text-sm text-gray-500 mt-0.5">Stelle Fragen zu deinen Papern &mdash; KI antwortet basierend auf den Inhalten</p>
                </div>
                <div class="flex items-center gap-2">
                    <span v-if="paperIds.length" class="text-xs bg-accent-soft text-accent-ink px-2 py-1 rounded-full font-medium">
                        {{ paperIds.length }} Paper &middot; {{ totalChunks }} Chunks
                    </span>
                    <button v-if="messages.length" @click="startNewChat"
                            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-colors"
                            title="Neuen Chat starten">
                        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                        Neuer Chat
                    </button>
                    <button @click="showPaperSelector = true"
                            class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 transition-colors">
                        <span v-html="icons.papers"></span>
                        Paper waehlen
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

            <!-- Chunking Progress -->
            <div v-if="chunking" class="bg-accent-soft border border-accent-soft rounded-lg p-3 mb-3 flex items-center gap-2 flex-shrink-0">
                <div class="spinner-sm"></div>
                <span class="text-sm text-accent-ink">Bereite Texte vor (Chunking)... {{ chunkProgress }}</span>
            </div>

            <!-- Chat Messages -->
            <div class="flex-1 overflow-y-auto bg-white rounded-lg border border-gray-200 mb-3 p-4 space-y-4" ref="chatMessages">
                <div v-if="messages.length === 0" class="flex flex-col items-center justify-center h-full text-gray-400">
                    <svg xmlns="http://www.w3.org/2000/svg" class="w-12 h-12 mb-3 opacity-30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                    <p class="text-sm">Waehle Paper aus und stelle eine Frage</p>
                    <p class="text-xs mt-1">Die KI durchsucht die Inhalte und antwortet mit Quellenangaben</p>
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
                            <p class="text-xs font-medium mb-1" :class="msg.role === 'user' ? 'text-accent-soft' : 'text-gray-500'">Quellen:</p>
                            <div v-for="(s, si) in msg.sources" :key="si" class="text-xs mb-1 group/src relative cursor-pointer"
                                 :class="msg.role === 'user' ? 'text-accent-soft' : 'text-gray-500'"
                                 @click="openPdfModal(s)">
                                <span class="hover:underline font-medium"
                                    :class="msg.role === 'user' ? 'text-accent-soft' : 'text-accent'">
                                    [{{ si + 1 }}] {{ s.title || 'Paper ' + s.paper_id }}
                                </span>
                                <span v-if="s.authors" class="opacity-70"> &mdash; {{ s.authors }}</span>
                                <span v-if="s.pages_referenced && s.pages_referenced.length">, S. {{ s.pages_referenced.join(', ') }}</span>
                                <!-- Hover Preview -->
                                <div v-if="s.previews && s.previews.length"
                                     class="hidden group-hover/src:block absolute left-0 bottom-full mb-1 z-30 w-96 bg-white border border-gray-200 rounded-lg shadow-xl p-3 text-xs text-gray-700 max-h-48 overflow-y-auto">
                                    <div v-for="(pv, pi) in s.previews" :key="pi" class="mb-2 last:mb-0">
                                        <span class="font-semibold text-accent">S. {{ pv.page }}:</span>
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
                    <span class="font-semibold text-accent">S. {{ pv.page }}:</span>
                    <span class="ml-1 text-gray-700">{{ pv.text }}{{ pv.text.length >= 300 ? '...' : '' }}</span>
                </div>
            </div>

            <!-- PDF Preview Modal -->
            <div v-if="pdfModal.show" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[10001]" @click.self="closePdfModal">
                <div class="pdf-preview-modal">
                    <div class="flex items-center gap-3 px-5 py-3.5 border-b border-gray-200 bg-gray-50 rounded-t-2xl flex-shrink-0">
                        <div class="flex-1 min-w-0">
                            <p class="text-sm font-semibold text-gray-900 truncate">{{ pdfModal.paperTitle }}</p>
                            <p class="text-xs text-gray-500 mt-0.5">{{ pdfModal.authors }} &middot; Seite {{ pdfModal.page }}</p>
                        </div>
                        <button @click="openInPaperDetail"
                                class="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-accent-ink bg-accent-soft border border-accent-soft rounded-lg hover:bg-accent-soft transition-colors">
                            <svg xmlns='http://www.w3.org/2000/svg' class='w-3.5 h-3.5' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6'/><polyline points='15 3 21 3 21 9'/><line x1='10' y1='14' x2='21' y2='3'/></svg>
                            Vollansicht
                        </button>
                        <button @click="closePdfModal"
                                class="w-8 h-8 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-600 hover:bg-gray-200 transition-colors"
                                title="Schliessen (ESC)">
                            <svg xmlns='http://www.w3.org/2000/svg' class='w-5 h-5' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><line x1='18' y1='6' x2='6' y2='18'/><line x1='6' y1='6' x2='18' y2='18'/></svg>
                        </button>
                    </div>
                    <div class="flex-1 overflow-hidden">
                        <iframe :src="pdfModal.url" class="w-full h-full border-none"></iframe>
                    </div>
                    <div v-if="pdfModal.highlightText" class="px-5 py-3 border-t border-gray-200 bg-accent-soft rounded-b-2xl flex-shrink-0 max-h-40 overflow-y-auto">
                        <p class="text-xs font-semibold text-accent-ink mb-1">&#128204; Referenzierte Textstelle:</p>
                        <p class="text-xs text-gray-700 leading-relaxed italic">"{{ pdfModal.highlightText.substring(0, 600) }}{{ pdfModal.highlightText.length > 600 ? '...' : '' }}"</p>
                    </div>
                </div>
            </div>

            <!-- Input -->
            <div class="flex-shrink-0">
                <div class="flex gap-2">
                    <input v-model="question" @keydown.enter="askQuestion"
                           :disabled="loading || !paperIds.length"
                           type="text" placeholder="Stelle eine Frage zu den ausgewaehlten Papern..."
                           class="flex-1 border border-gray-300 rounded-lg px-4 py-2.5 text-sm focus:ring-2 focus:ring-accent focus:border-accent outline-none disabled:bg-gray-50 disabled:text-gray-400" />
                    <button @click="askQuestion"
                            :disabled="loading || !question.trim() || !paperIds.length"
                            class="inline-flex items-center gap-1.5 bg-accent text-white px-5 py-2.5 rounded-lg text-sm font-medium hover:bg-accent-ink disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
                        Fragen
                    </button>
                    <button v-if="messages.length" @click="clearChat"
                            class="inline-flex items-center gap-1.5 px-3 py-2.5 rounded-lg text-sm font-medium border border-gray-300 text-gray-600 bg-white hover:bg-gray-50 transition-colors"
                            title="Chat leeren">
                        <span v-html="icons.trash"></span>
                    </button>
                </div>
            </div>

            <!-- Paper Selector Modal -->
            <div v-if="showPaperSelector" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="showPaperSelector = false">
                <div class="bg-white rounded-xl shadow-xl max-w-2xl w-full mx-4 max-h-[80vh] flex flex-col">
                    <div class="flex items-center justify-between p-5 border-b border-gray-200">
                        <h3 class="text-lg font-semibold text-gray-900">Paper auswaehlen</h3>
                        <button @click="showPaperSelector = false" class="text-gray-400 hover:text-gray-600 p-1">
                            <span v-html="icons.x" style="width:20px;height:20px;"></span>
                        </button>
                    </div>
                    <div class="p-4 border-b border-gray-200">
                        <input v-model="paperSearch" type="text" placeholder="Paper suchen..."
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
                            Keine Paper gefunden
                        </div>
                    </div>
                    <div class="p-4 border-t border-gray-200 flex items-center justify-between bg-gray-50 rounded-b-xl">
                        <span class="text-sm text-gray-500">{{ paperIds.length }} ausgewaehlt</span>
                        <button @click="confirmPaperSelection"
                                class="bg-accent text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-accent-ink transition-colors">
                            Uebernehmen
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
        };
    },
    computed: {
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
                    title: (src.title || 'Quelle') + (src.authors ? ' — ' + src.authors : ''),
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
                    this.chunkProgress = unchunked.length + ' Paper werden verarbeitet...';
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
                    content: result.answer,
                    sources: result.sources || [],
                });
            } catch (e) {
                this.messages.push({
                    role: 'assistant',
                    content: 'Fehler: ' + (e.message || 'Unbekannter Fehler'),
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
                    <h2 class="text-xl font-bold text-gray-900">Abschlussarbeit analysieren</h2>
                    <p class="text-sm text-gray-500">PDF hochladen &mdash; wird nicht in die Bibliothek aufgenommen</p>
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
                    <p class="text-base text-gray-600 mb-2 font-medium">PDF der Abschlussarbeit hierher ziehen</p>
                    <p class="text-sm text-gray-400 mb-5">oder Datei auswaehlen</p>
                    <label class="inline-flex items-center gap-2 bg-accent text-white px-6 py-3 rounded-lg text-sm font-medium hover:bg-accent-ink cursor-pointer transition-colors">
                        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                        PDF auswaehlen
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
                        <span class="text-xs text-gray-400">{{ result.total_pages }} Seiten</span>
                    </div>
                    <div class="grid grid-cols-2 sm:grid-cols-5 gap-3">
                        <div class="bg-gray-50 rounded-lg p-3 text-center">
                            <div class="text-2xl font-bold text-gray-800">{{ result.summary.total }}</div>
                            <div class="text-[11px] text-gray-500">Quellen gesamt</div>
                        </div>
                        <div class="bg-accent-soft rounded-lg p-3 text-center">
                            <div class="text-2xl font-bold text-accent">{{ result.summary.found_online }}</div>
                            <div class="text-[11px] text-gray-500">Online gefunden</div>
                        </div>
                        <div class="bg-accent-soft rounded-lg p-3 text-center">
                            <div class="text-2xl font-bold text-accent">{{ result.summary.not_found_online }}</div>
                            <div class="text-[11px] text-gray-500">Nicht online</div>
                        </div>
                        <div class="bg-accent-soft rounded-lg p-3 text-center">
                            <div class="text-2xl font-bold text-accent">{{ result.summary.used_in_text }}</div>
                            <div class="text-[11px] text-gray-500">Im Text zitiert</div>
                        </div>
                        <div class="bg-accent-soft rounded-lg p-3 text-center">
                            <div class="text-2xl font-bold text-accent">{{ result.summary.not_used_in_text }}</div>
                            <div class="text-[11px] text-gray-500">Nicht im Text</div>
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
                                        <th class="pb-2 pr-3">Titel</th>
                                        <th class="pb-2 pr-3">Autoren</th>
                                        <th class="pb-2 pr-3 text-center w-14">Jahr</th>
                                        <th class="pb-2 pr-3 text-center w-16">Online</th>
                                        <th class="pb-2 text-center w-16">Im Text</th>
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
                                            <span v-if="ref.online_found" class="text-accent" title="Online gefunden">
                                                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                                            </span>
                                            <span v-else class="text-accent" title="Nicht online gefunden">
                                                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                                            </span>
                                        </td>
                                        <td class="py-2 text-center">
                                            <span v-if="ref.used_in_text" class="text-accent" title="Im Text zitiert">
                                                <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>
                                            </span>
                                            <span v-else class="text-accent" title="Nicht im Text gefunden">
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
                            Alle Quellen wurden online gefunden!
                        </div>
                        <div v-else class="space-y-2">
                            <p class="text-xs text-gray-500 mb-3">Diese Quellen konnten weder ueber DOI, CrossRef noch OpenAlex online verifiziert werden:</p>
                            <div v-for="ref in result.not_found_online" :key="ref.index"
                                 class="flex items-start gap-3 bg-accent-soft rounded-lg p-3 border border-accent-soft">
                                <span class="text-xs text-accent font-mono mt-0.5 w-6 flex-shrink-0">[{{ ref.index }}]</span>
                                <div class="flex-1 min-w-0">
                                    <p class="text-sm text-gray-900 font-medium">{{ ref.title || 'Kein Titel' }}</p>
                                    <p class="text-xs text-gray-500 mt-0.5">{{ ref.authors }}<span v-if="ref.year"> ({{ ref.year }})</span></p>
                                    <p v-if="ref.journal" class="text-xs text-gray-400">{{ ref.journal }}</p>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Not Used in Text -->
                    <div v-show="tab==='not_cited'" class="p-5">
                        <div v-if="result.not_used_in_text.length === 0" class="text-center py-10 text-gray-400 text-sm">
                            Alle Quellen werden im Text zitiert!
                        </div>
                        <div v-else class="space-y-2">
                            <p class="text-xs text-gray-500 mb-3">Diese Quellen stehen im Literaturverzeichnis, konnten aber nicht als Zitation im Fliesstext gefunden werden:</p>
                            <div v-for="ref in result.not_used_in_text" :key="ref.index"
                                 class="flex items-start gap-3 bg-accent-soft rounded-lg p-3 border border-accent-soft">
                                <span class="text-xs text-accent font-mono mt-0.5 w-6 flex-shrink-0">[{{ ref.index }}]</span>
                                <div class="flex-1 min-w-0">
                                    <p class="text-sm text-gray-900 font-medium">{{ ref.title || 'Kein Titel' }}</p>
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
                        Neue Analyse
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
            this.loadingMessage = 'PDF wird hochgeladen...';

            const steps = [
                { pct: 20, msg: 'Text wird extrahiert...' },
                { pct: 40, msg: 'Literaturverzeichnis wird gesucht...' },
                { pct: 55, msg: 'Referenzen werden per KI extrahiert...' },
                { pct: 70, msg: 'Zitationen im Text werden geprueft...' },
                { pct: 85, msg: 'Online-Verfuegbarkeit wird geprueft...' },
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
                    throw new Error(err.detail || 'Analyse fehlgeschlagen');
                }
                this.result = await resp.json();
                this.progress = 100;
                this.loadingMessage = 'Fertig!';
                setTimeout(() => { this.phase = 'result'; this.tab = 'all'; }, 400);
            } catch (err) {
                clearInterval(iv);
                alert('Fehler bei der Thesis-Analyse: ' + err.message);
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
                    <div class="lb-section-label" style="padding:0;margin-bottom:6px">Pipeline</div>
                    <h3 style="font-family:var(--lb-font-serif);font-size:22px;font-weight:500;margin:0 0 16px;color:var(--lb-ink)">Bulk-Referenz-Extraktion</h3>
                    <div style="margin-bottom:14px">
                        <div style="width:100%;background:var(--lb-bg-soft);border-radius:99px;height:6px;margin-bottom:8px;overflow:hidden">
                            <div style="background:var(--lb-accent);height:6px;border-radius:99px;transition:width .3s ease" :style="{ width: bulkProgress.percent + '%' }"></div>
                        </div>
                        <p style="font-size:13px;color:var(--lb-ink-2);margin:0">{{ bulkProgress.message || 'Starte...' }}</p>
                        <p v-if="bulkProgress.current_paper && bulkProgress.total_papers" class="lb-mono" style="font-size:11px;color:var(--lb-mute);margin:4px 0 0">
                            Paper {{ bulkProgress.current_paper }} / {{ bulkProgress.total_papers }}
                        </p>
                    </div>
                    <div v-if="bulkPaperResults.length" style="flex:1;overflow-y:auto;border-top:1px solid var(--lb-hairline);padding-top:10px;display:flex;flex-direction:column;gap:2px">
                        <div v-for="r in bulkPaperResults" :key="r.paper_id" style="display:flex;align-items:center;gap:8px;font-size:12px;padding:3px 0">
                            <span v-if="r.status === 'ok'" style="color:var(--lb-accent);width:14px">&#10003;</span>
                            <span v-else style="color:var(--lb-mute);width:14px">&#x2013;</span>
                            <span class="lb-truncate" style="flex:1;color:var(--lb-ink-2)">{{ r.title }}</span>
                            <span v-if="r.status === 'ok'" class="lb-mono" style="color:var(--lb-mute);font-size:11px">{{ r.total_extracted }} Refs / {{ r.in_library }} in Bib</span>
                            <span v-else class="lb-mono" style="color:var(--lb-mute);font-size:11px">{{ r.status }}</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Page Head -->
            <header class="lb-page-head">
                <div class="lb-page-head-l">
                    <div class="lb-eyebrow">Analyse</div>
                    <h1 class="lb-page-title">Wissensnetz</h1>
                    <div class="lb-page-meta">
                        <span v-if="stats">{{ stats.own_papers }} eigene Paper</span>
                        <span v-if="stats" class="lb-page-meta-sep">&middot;</span>
                        <span v-if="stats">{{ stats.total_references }} Referenzen</span>
                        <span v-if="!stats">Zitationsnetzwerk und fehlende Primaerquellen via OpenAlex</span>
                    </div>
                </div>
                <div class="lb-page-head-r">
                    <div class="lb-segmented">
                        <button class="lb-seg" :class="{'is-on': mode==='categories'}" @click="setMode('categories')">Kategorien</button>
                        <button class="lb-seg" :class="{'is-on': mode==='years'}" @click="setMode('years')">Jahre</button>
                        <button class="lb-seg" :class="{'is-on': mode==='authors'}" @click="setMode('authors')">Autoren</button>
                    </div>
                </div>
            </header>

            <!-- Toolbar -->
            <div class="lb-toolbar">
                <div class="lb-toolbar-l">
                    <div class="lb-sortgroup">
                        <span class="lb-sortgroup-l">Suchtiefe</span>
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
                        title="Literaturverzeichnisse aller Paper per LLM extrahieren">
                        <span v-if="bulkExtracting" class="spinner-sm"></span>
                        <svg v-else xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>
                        {{ bulkExtracting ? 'Extrahiere...' : 'PDF-Referenzen' }}
                    </button>
                    <button @click="runAnalysis" :disabled="loading" class="lb-btn lb-btn-primary">
                        <span v-if="loading" class="spinner-sm"></span>
                        <span v-else v-html="icons.refresh"></span>
                        {{ loading ? 'Analysiere...' : 'Analyse starten' }}
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
                                placeholder="Paper suchen & navigieren..." class="lb-input" style="padding-left:32px">
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
                        <span class="lb-field-label" style="margin:0 4px 0 8px">Kategorie</span>
                        <select v-model="graphPanelCategory" @change="applyCategoryFilter" class="lb-select" style="border:1px solid var(--lb-hairline);border-radius:7px;background:var(--lb-bg-elev);min-width:160px">
                            <option value="">Alle</option>
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
                                    <input v-model="graphPanelSearch" placeholder="Filtern..." class="lb-input" style="padding:6px 10px;font-size:12px">
                                </div>
                                <div style="max-height:280px;overflow-y:auto">
                                    <button v-for="p in graphPanelFilteredPapers" :key="p.id" @click="navigateToNode(p)"
                                        :style="{background: highlightedNodeId === p.id ? 'var(--lb-accent-soft)' : 'transparent'}"
                                        style="width:100%;text-align:left;padding:7px 12px;font-size:12px;border:0;border-bottom:1px solid var(--lb-hairline);display:flex;align-items:center;gap:8px;cursor:pointer;color:var(--lb-ink-2)">
                                        <span style="width:7px;height:7px;border-radius:50%;flex-shrink:0;background:var(--lb-ink)"></span>
                                        <span class="lb-truncate" style="flex:1">{{ p.title || p.id }}</span>
                                        <span v-if="p.year" class="lb-mono" style="font-size:11px;color:var(--lb-mute);flex-shrink:0">{{ p.year }}</span>
                                    </button>
                                    <div v-if="!graphPanelFilteredPapers.length" style="padding:18px;text-align:center;font-size:12px;color:var(--lb-mute)">Keine Paper gefunden</div>
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
                                    <span v-if="hoverNode.cited_by_count != null">{{ hoverNode.cited_by_count.toLocaleString() }} Zit.</span>
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
                                    <span v-if="selectedNode.cited_by_count">{{ selectedNode.cited_by_count.toLocaleString() }} Zit.</span>
                                    <span v-if="selectedNode.referenced_by_count">{{ selectedNode.referenced_by_count }}× ref.</span>
                                </div>
                                <div v-if="selectedNode.doi" style="margin-bottom:10px">
                                    <a :href="'https://doi.org/' + selectedNode.doi" target="_blank" class="lb-mono" style="font-size:11px;color:var(--lb-accent);text-decoration:none">DOI: {{ selectedNode.doi }}</a>
                                </div>
                                <div v-if="selectedNode.paper_id" style="margin-bottom:10px">
                                    <router-link :to="'/paper/' + selectedNode.paper_id" class="lb-btn lb-btn-primary" style="padding:5px 10px;font-size:11.5px">Details öffnen →</router-link>
                                </div>
                                <div style="border-top:1px solid var(--lb-hairline);padding-top:10px;margin-top:6px">
                                    <div v-if="nodeAbstractLoading" style="display:flex;align-items:center;gap:8px;font-size:12px;color:var(--lb-mute)">
                                        <span class="spinner-sm"></span> Abstract wird geladen...
                                    </div>
                                    <div v-else-if="nodeAbstract" style="font-size:12.5px;line-height:1.55;color:var(--lb-ink-2);max-height:160px;overflow-y:auto">
                                        <span class="lb-section-label" style="padding:0;display:inline">Abstract</span><br>{{ nodeAbstract }}
                                    </div>
                                    <div v-else-if="nodeAbstractError" style="font-size:11.5px;color:var(--lb-mute);font-style:italic">{{ nodeAbstractError }}</div>
                                </div>
                                <button @click="askAboutSelectedNode()" class="lb-btn lb-btn-w" style="margin-top:10px;justify-content:center">
                                    <svg width="13" height="13" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-5l-5 5v-5z"/></svg>
                                    Im Chat fragen
                                </button>
                            </div>
                        </div>

                        <aside class="lb-net-aside">
                            <div class="lb-aside-block">
                                <h4 class="lb-aside-h">Legende</h4>
                                <ul class="lb-legend">
                                    <li><span class="lb-legend-mark lb-legend-paper"></span><span>Eigenes Paper</span></li>
                                    <li><span class="lb-legend-mark" style="background:var(--lb-accent);width:12px;height:12px;border-radius:50%"></span><span>Geteilte Quelle</span></li>
                                    <li><span class="lb-legend-mark lb-legend-cat"></span><span>Größe = Zitationen</span></li>
                                    <li><span class="lb-legend-mark lb-legend-edge"></span><span>Zitiert</span></li>
                                    <li><span class="lb-legend-mark lb-legend-edge-co"></span><span>PDF-Referenz</span></li>
                                </ul>
                            </div>

                            <div class="lb-aside-block">
                                <h4 class="lb-aside-h">Filter</h4>
                                <div style="display:flex;flex-direction:column;gap:14px;margin-top:6px">
                                    <div>
                                        <div style="display:flex;justify-content:space-between;font-size:11.5px;color:var(--lb-ink-3);margin-bottom:4px"><span>Abstand</span><span class="lb-mono" style="color:var(--lb-ink-2)">{{ graphDistance }}</span></div>
                                        <input type="range" v-model.number="graphDistance" min="5" max="120" step="1" style="width:100%;accent-color:var(--lb-accent)">
                                    </div>
                                    <div>
                                        <div style="display:flex;justify-content:space-between;font-size:11.5px;color:var(--lb-ink-3);margin-bottom:4px"><span title="Pfadtiefe für gemeinsame Referenz-Erkennung">Ref.-Tiefe</span><span class="lb-mono" style="color:var(--lb-accent)">{{ minRefs }}</span></div>
                                        <input type="range" v-model.number="minRefs" min="1" max="3" step="1" style="width:100%;accent-color:var(--lb-accent)">
                                    </div>
                                    <div>
                                        <div style="display:flex;justify-content:space-between;font-size:11.5px;color:var(--lb-ink-3);margin-bottom:4px"><span title="Nur Refs mit mind. X Zitationen">Min. Zit.</span><span class="lb-mono" style="color:var(--lb-ink-2)">{{ minCitations || 'Aus' }}</span></div>
                                        <input type="range" v-model.number="minCitations" min="0" max="500" step="10" style="width:100%;accent-color:var(--lb-accent)">
                                    </div>
                                    <div>
                                        <div style="display:flex;justify-content:space-between;font-size:11.5px;color:var(--lb-ink-3);margin-bottom:4px"><span title="Top N pro eigenem Paper">Top-N Refs</span><span class="lb-mono" style="color:var(--lb-ink-2)">{{ topNRefs || 'Alle' }}</span></div>
                                        <input type="range" v-model.number="topNRefs" min="0" max="30" step="1" style="width:100%;accent-color:var(--lb-accent)">
                                    </div>
                                </div>
                            </div>

                            <div class="lb-aside-block" style="margin-top:auto">
                                <p class="lb-mono" style="font-size:10px;color:var(--lb-mute);letter-spacing:0.06em;margin:0">Scroll = Zoom · Drag = Verschieben</p>
                            </div>
                        </aside>
                    </div>
                </div>

                <!-- Paper Chat (below network) -->
                <div v-show="activeTab==='network' && networkData" style="border-top:1px solid var(--lb-hairline);margin-top:24px;padding-top:20px">
                    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
                        <div>
                            <div class="lb-section-label" style="padding:0;margin-bottom:4px">Paper-Chat</div>
                            <div v-if="chatContextPaper" style="font-family:var(--lb-font-serif);font-style:italic;font-size:15px;color:var(--lb-ink-2)" class="lb-truncate">{{ chatContextPaper.title }}</div>
                        </div>
                        <div style="display:flex;align-items:center;gap:10px">
                            <button v-if="chatContextPaper" @click="chatContextPaper=null; clearChat()" class="lb-btn-text" title="Paper-Kontext entfernen">Paper wechseln</button>
                            <button @click="chatOpen = !chatOpen" class="lb-btn-text">{{ chatOpen ? 'Einklappen' : 'Aufklappen' }}</button>
                        </div>
                    </div>

                    <div v-show="chatOpen">
                        <div v-if="!chatContextPaper" class="lb-empty" style="padding:32px 24px;border:1px solid var(--lb-hairline);border-radius:10px;background:var(--lb-bg-elev)">
                            <div class="lb-empty-mark">"</div>
                            <p style="font-size:13.5px;color:var(--lb-ink-3);margin:0 0 4px">Klicke auf ein Paper im Netzwerk und dann auf <span style="color:var(--lb-accent);font-weight:500">„Im Chat fragen"</span>.</p>
                            <p style="font-size:12px;color:var(--lb-mute);margin:0">Eigene und referenzierte Paper sind beide wählbar.</p>
                        </div>

                        <div v-else>
                            <div ref="chatMessages" style="background:var(--lb-bg-elev);border:1px solid var(--lb-hairline);border-radius:10px;margin-bottom:10px;overflow-y:auto;max-height:400px;min-height:140px">
                                <div v-if="chatMessages.length === 0 && !chatStreaming" style="display:flex;align-items:center;justify-content:center;height:140px;color:var(--lb-mute);font-size:12.5px;font-style:italic">
                                    Stelle eine Frage zu „{{ chatContextPaper.title }}"
                                </div>
                                <div v-for="(msg, i) in chatMessages" :key="i" style="padding:14px 18px;border-bottom:1px solid var(--lb-hairline)">
                                    <div class="lb-section-label" style="padding:0;margin-bottom:6px" :style="{color: msg.role === 'user' ? 'var(--lb-accent)' : 'var(--lb-mute)'}">{{ msg.role === 'user' ? 'Du' : 'Assistent' }}</div>
                                    <div :style="{fontFamily: msg.role === 'user' ? 'var(--lb-font-sans)' : 'var(--lb-font-serif)', fontSize: msg.role === 'user' ? '13.5px' : '15px'}" class="lb-md" style="line-height:1.6;color:var(--lb-ink-2)" v-html="renderMarkdown(msg.content)"></div>
                                </div>
                                <div v-if="chatStreaming" style="padding:14px 18px">
                                    <div class="lb-section-label" style="padding:0;margin-bottom:6px;color:var(--lb-mute)">Assistent</div>
                                    <div style="font-family:var(--lb-font-serif);font-size:15px;line-height:1.6;color:var(--lb-ink-2)" class="lb-md" v-html="renderMarkdown(chatStreamContent || 'Denke nach...')"></div>
                                </div>
                            </div>

                            <div style="display:flex;gap:8px">
                                <input v-model="chatInput" @keydown.enter="sendChatMessage" :disabled="chatStreaming"
                                    placeholder="Frage zu diesem Paper stellen..." class="lb-input" style="flex:1">
                                <button @click="sendChatMessage" :disabled="chatStreaming || !chatInput.trim()" class="lb-btn lb-btn-primary">
                                    <span v-if="chatStreaming" class="spinner-sm"></span>
                                    <svg v-else width="14" height="14" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6"/></svg>
                                    Senden
                                </button>
                                <button v-if="chatMessages.length" @click="clearChat" class="lb-icon-btn" title="Chat leeren">
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
                        <p style="font-size:13px;color:var(--lb-ink-3);margin:0">Alle Paper in deiner Bibliothek mit Referenz- und Zitationsdaten.</p>
                        <button @click="updateCitations" :disabled="updatingCitations" class="lb-btn lb-btn-ghost">
                            <span v-if="updatingCitations" class="spinner-sm"></span>
                            <span v-else v-html="icons.refresh"></span>
                            Zitationen aktualisieren
                        </button>
                    </div>
                    <div v-if="!networkData.own_paper_stats || networkData.own_paper_stats.length === 0" class="lb-empty">
                        <div class="lb-empty-mark">∅</div>
                        <p class="lb-empty-text">Keine Daten verfügbar.</p>
                    </div>
                    <div v-else style="overflow-x:auto">
                        <table class="lb-table">
                            <thead><tr>
                                <th class="is-sortable" @click="sortOwn('title')">Titel ↕</th>
                                <th>Autoren</th>
                                <th class="is-sortable" style="text-align:center" @click="sortOwn('year')">Jahr ↕</th>
                                <th class="is-sortable" style="text-align:right" @click="sortOwn('cited_by_count')">Zitationen ↕</th>
                                <th class="is-sortable" style="text-align:right" @click="sortOwn('reference_count')">OA-Refs ↕</th>
                                <th class="is-sortable" style="text-align:right" @click="sortOwn('pdf_reference_count')">PDF-Refs ↕</th>
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
                            <div>Gesamtzitationen: <span class="lb-mono" style="color:var(--lb-ink);font-size:14px">{{ totalOwnCitations.toLocaleString() }}</span></div>
                            <div>Durchschnitt: <span class="lb-mono" style="color:var(--lb-ink);font-size:14px">{{ avgOwnCitations }}</span></div>
                            <div>Max: <span class="lb-mono" style="color:var(--lb-ink);font-size:14px">{{ maxOwnCitations.toLocaleString() }}</span></div>
                        </div>
                    </div>
                </div>

                <!-- Referenced Papers Table -->
                <div v-show="activeTab==='references'">
                    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px">
                        <p style="font-size:13px;color:var(--lb-ink-3);margin:0">Von deiner Literatur zitierte Paper (OpenAlex + PDF-Referenzen).</p>
                        <div style="display:flex;align-items:center;gap:8px">
                            <span class="lb-field-label">Filter</span>
                            <select v-model="refFilter" class="lb-select" style="border:1px solid var(--lb-hairline);border-radius:7px;background:var(--lb-bg-elev)">
                                <option value="all">Alle</option>
                                <option value="not_in_lib">Nicht in Bibliothek</option>
                                <option value="in_lib">In Bibliothek</option>
                            </select>
                        </div>
                    </div>
                    <div v-if="!networkData.referenced_papers || networkData.referenced_papers.length === 0" class="lb-empty">
                        <div class="lb-empty-mark">∅</div>
                        <p class="lb-empty-text">Keine referenzierten Paper gefunden.</p>
                    </div>
                    <div v-else style="overflow-x:auto">
                        <table class="lb-table">
                            <thead><tr>
                                <th style="width:32px">#</th>
                                <th class="is-sortable" @click="sortRef('title')">Titel ↕</th>
                                <th>Autoren</th>
                                <th class="is-sortable" style="text-align:center" @click="sortRef('year')">Jahr ↕</th>
                                <th class="is-sortable" style="text-align:right" @click="sortRef('cited_by_count')">Zitationen ↕</th>
                                <th class="is-sortable" style="text-align:right" @click="sortRef('referenced_by_own')">Von eigenen ↕</th>
                                <th style="text-align:center">In Bibl.</th>
                                <th style="text-align:center">Quelle</th>
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
                                    <td><a v-if="ref.doi" :href="'https://doi.org/' + ref.doi" target="_blank" class="lb-mono" style="font-size:11px;color:var(--lb-accent);text-decoration:none">{{ ref.doi }}</a><a v-else-if="ref.title" :href="scholarUrl(ref)" target="_blank" style="font-size:11px;color:var(--lb-mute);text-decoration:none" title="Titel + Erstautor auf Google Scholar suchen">Scholar &#8599;</a><span v-else style="color:var(--lb-mute-2)">—</span></td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Missing Sources Table -->
                <div v-show="activeTab==='missing'">
                    <div style="margin-bottom:18px">
                        <p style="font-size:13px;color:var(--lb-ink-3);margin:0">Paper, die von mehreren eigenen Papern referenziert werden (OA + PDF), aber nicht in deiner Bibliothek sind.</p>
                    </div>
                    <div v-if="filteredMissing.length === 0" class="lb-empty">
                        <div class="lb-empty-mark">∅</div>
                        <p class="lb-empty-text">Keine fehlenden Primärquellen mit ≥2 Referenzen gefunden.</p>
                    </div>
                    <div v-else style="overflow-x:auto">
                        <table class="lb-table">
                            <thead><tr>
                                <th>Titel</th>
                                <th>Autoren</th>
                                <th style="text-align:center">Jahr</th>
                                <th class="is-sortable" style="text-align:right" @click="sortMissing('referenced_by_count')">Ref. von eigenen ↕</th>
                                <th class="is-sortable" style="text-align:right" @click="sortMissing('cited_by_count')">Zitationen ↕</th>
                                <th>DOI</th>
                            </tr></thead>
                            <tbody>
                                <tr v-for="src in sortedMissing" :key="src.id">
                                    <td class="lb-td-title">{{ src.title }}</td>
                                    <td class="lb-td-authors lb-truncate">{{ src.authors }}</td>
                                    <td class="lb-td-num" style="text-align:center">{{ src.year || '—' }}</td>
                                    <td class="lb-td-num"><span class="lb-num-pill is-accent">{{ src.referenced_by_count }}×</span></td>
                                    <td class="lb-td-num">{{ (src.cited_by_count || 0).toLocaleString() }}</td>
                                    <td><a v-if="src.doi" :href="'https://doi.org/' + src.doi" target="_blank" class="lb-mono" style="font-size:11px;color:var(--lb-accent);text-decoration:none">{{ src.doi }}</a><a v-else-if="src.title" :href="scholarUrl(src)" target="_blank" style="font-size:11px;color:var(--lb-mute);text-decoration:none" title="Titel + Erstautor auf Google Scholar suchen">Scholar &#8599;</a><span v-else style="color:var(--lb-mute-2)">—</span></td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <!-- Empty state -->
            <div v-if="!networkData && !loading" class="lb-empty" style="border:1px solid var(--lb-hairline);border-radius:14px;background:var(--lb-bg-elev);padding:64px 32px">
                <div class="lb-empty-mark">◌</div>
                <h3 class="lb-empty-title">Zitationsanalyse</h3>
                <p class="lb-empty-text">
                    Analysiere die Zusammenhänge deiner Paper über OpenAlex und PDF-Referenzen.
                    Finde gemeinsame Referenzen und identifiziere Primärquellen, die fehlen.
                </p>
                <div style="margin:24px auto 16px;display:inline-flex;align-items:center;gap:10px;font-size:12.5px;color:var(--lb-ink-3)">
                    <span class="lb-field-label" style="margin:0">Suchtiefe</span>
                    <div class="lb-segmented">
                        <button v-for="d in [1,2,3,4,5]" :key="d" @click="depth = d" class="lb-seg" :class="{'is-on': depth === d}" style="min-width:30px;justify-content:center">{{ d }}</button>
                    </div>
                    <span class="lb-mono" style="font-size:11px;color:var(--lb-mute)">{{ depthLabel }}</span>
                </div>
                <div>
                    <button @click="runAnalysis" class="lb-btn lb-btn-primary" style="padding:10px 22px;font-size:13px">
                    Analyse starten
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
            quickQuestions: [
                'Worum geht es in dem Paper?',
                'Was sind die wichtigsten Ergebnisse?',
                'Welche Methoden werden verwendet?',
                'Welche Findings werden besonders haeufig zitiert?',
            ],
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
        statBlocks() {
            const s = this.stats || {};
            return [
                { label: 'Eigene Paper',     value: s.own_papers ?? '—' },
                { label: 'In OpenAlex',      value: s.papers_found_in_openalex ?? '—' },
                { label: 'Referenzen',       value: s.total_references ?? '—' },
                { label: 'PDF-Refs',         value: s.pdf_reference_edges ?? 0 },
                { label: 'Gemeinsam',        value: s.shared_references ?? '—' },
                { label: 'Fehlend ≥2',       value: this.filteredMissing.length },
                { label: 'Suchtiefe',        value: s.depth ?? this.depth ?? 1 },
            ];
        },
        tabDefs() {
            const nd = this.networkData || {};
            return [
                { id: 'network',    label: 'Netzwerk',     count: null },
                { id: 'ownstats',   label: 'Eigene Paper', count: (nd.own_paper_stats || []).length || null },
                { id: 'references', label: 'Referenzen',   count: (nd.referenced_papers || []).length || null },
                { id: 'missing',    label: 'Fehlend',      count: this.filteredMissing.length || null },
            ];
        },
        depthLabel() {
            return { 1: 'Direkte Referenzen', 2: 'Ref. der Referenzen', 3: 'Drei Ebenen', 4: 'Vier Ebenen', 5: 'Fuenf Ebenen' }[this.depth] || '';
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
                this.error = 'Analyse fehlgeschlagen: ' + e.message;
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
                                    message: evt.message || '',
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
                    alert('PDF-Referenzen extrahiert:\n' +
                        r.processed + ' Paper verarbeitet\n' +
                        r.total_references + ' Referenzen gefunden\n' +
                        r.total_in_library + ' davon in Bibliothek');
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
                missing: 'Fehlende Quelle',
                own_ref: 'In Bibliothek',
                external: 'Externe Referenz',
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
                container.innerHTML = '<div class="flex items-center justify-center h-full text-gray-400 text-sm">Keine Netzwerkdaten verfuegbar</div>';
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
                this.nodeAbstractError = 'Kein Abstract verfuegbar (keine DOI)';
                return;
            }

            this.nodeAbstractLoading = true;
            try {
                const data = await api('/api/analysis/node-abstract?doi=' + encodeURIComponent(doi || '') + '&openalex_id=' + encodeURIComponent(oaId || ''));
                if (data.abstract) {
                    this.nodeAbstract = data.abstract;
                } else {
                    this.nodeAbstractError = 'Kein Abstract bei OpenAlex gefunden';
                }
            } catch (e) {
                this.nodeAbstractError = 'Abstract konnte nicht geladen werden';
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
                    this.chatMessages.push({ role: 'assistant', content: 'Fehler: ' + (err.error || resp.statusText) });
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
                    this.chatMessages.push({ role: 'assistant', content: this.chatStreamContent });
                }
            } catch (e) {
                this.chatMessages.push({ role: 'assistant', content: 'Fehler: ' + e.message });
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
// Plugin registry (frontend counterpart of the server-side registry.py)
// =============================================================================

// Maps a plugin view-key (from /api/plugins/nav) to its Vue component.
const PLUGIN_VIEWS = {
};
// Detail sub-routes below a plugin route (issue #115): `path` is appended to
// the nav item's route. They register and unregister together with the parent
// item, so deactivating the plugin removes them residue-free.
const PLUGIN_SUBROUTES = {
};
const _pluginRouteRemovers = {};   // route path -> { remove, name }

// All route records a nav item brings along: the plugin view itself plus its
// detail sub-routes.
function pluginRouteRecords(item) {
    const view = PLUGIN_VIEWS[item.view];
    if (!view) return [];
    const subs = PLUGIN_SUBROUTES[item.view] || [];
    return [
        { path: item.route, component: view, name: 'plugin-' + item.id },
        ...subs.map((s) => ({
            path: item.route + s.path,
            component: s.component,
            name: 'plugin-' + item.id + '-' + s.suffix,
        })),
    ];
}

// Register/unregister plugin routes to match the active nav items. Removing a
// route is residue-free (P3/deactivate): no listener, no view stays behind.
// "Am I on a removed route?" compares route names, not paths — a sub-route
// with params (/plugin/runs/:id/analyse) never equals the concrete path.
function syncPluginRoutes(items) {
    const records = items.flatMap(pluginRouteRecords);
    const active = new Set(records.map((r) => r.path));
    const curName = router.currentRoute.value ? router.currentRoute.value.name : null;
    let currentRemoved = false;
    for (const path of Object.keys(_pluginRouteRemovers)) {
        if (!active.has(path)) {
            const entry = _pluginRouteRemovers[path];
            entry.remove();
            delete _pluginRouteRemovers[path];
            if (curName && curName === entry.name) currentRemoved = true;
        }
    }
    let added = false;
    for (const r of records) {
        if (!_pluginRouteRemovers[r.path]) {
            _pluginRouteRemovers[r.path] = { remove: router.addRoute(r), name: r.name };
            added = true;
        }
    }
    if (currentRemoved) {
        router.push('/');
    } else if (added && router.currentRoute.value.matched.length === 0) {
        // We just registered the route the user deep-linked/refreshed onto;
        // re-resolve the current location so it renders instead of staying blank.
        router.replace(router.currentRoute.value.fullPath);
    }
}


// =============================================================================
// Router
// =============================================================================

const routes = [
    { path: '/', component: PaperList, name: 'home' },
    { path: '/category/:id', component: PaperList, name: 'category' },
    { path: '/paper/:id', component: PaperDetail, name: 'paper' },
    { path: '/import', component: ImportPage, name: 'import' },
    { path: '/analyse', component: AnalysePage, name: 'analyse' },
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
app.use(router);
app.mount('#app');
