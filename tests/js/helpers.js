// Shared helpers for the SPA smoke tests (issue #54).
//
// The SPA is one self-mounting browser script (static/app.js): importing it
// evaluates the whole file, registers routes, and mounts onto #app. Each test
// file therefore boots the app exactly once (vitest isolates module graphs per
// file) and drives it through the DOM, with `fetch` replaced by a small
// route-table mock.
import { vi } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// An Add-on Bundle of this repo (ADR-0021), booted the way the index page
// does with it active: `activate()` names it in `window.LB_ADDONS`, the
// `routes()` serve its real script and locale files through the fetch mock,
// and the core loads them before it mounts the router. `id` is the folder
// under plugins/; the Bundle's tests pass their own id.
export function bundleAddon(id) {
    const root = resolve(__dirname, '../../plugins', id);
    if (!existsSync(resolve(root, 'plugin.json'))) throw new Error(`no Bundle at plugins/${id}`);
    const read = (rel) => readFileSync(resolve(root, rel), 'utf-8');
    const manifest = JSON.parse(read('plugin.json'));
    const frontend = manifest.frontend || {};
    const url = (rel) => `/plugins/${id}/static/${rel.replace(/^frontend\//, '')}?v=${manifest.version}`;
    // What GET /api/plugins/frontend (and the index page) says about it.
    const addon = {
        id,
        version: manifest.version,
        assets: (frontend.assets || []).map(url),
        stylesheet: frontend.stylesheet ? url(frontend.stylesheet) : null,
        script: url(frontend.script),
        locales: Object.fromEntries(
            Object.entries(frontend.locales || {}).map(([lang, rel]) => [lang, url(rel)]),
        ),
        default_language: manifest.default_language,
        nav: manifest.nav ? { id, route: manifest.nav.route, view: manifest.nav.view } : null,
    };
    // What GET /api/plugins/nav reports while it is active.
    const navItem = manifest.nav
        ? { id, label: manifest.nav.label, icon: '<svg></svg>', route: manifest.nav.route, view: manifest.nav.view }
        : null;
    return {
        manifest,
        read,
        addon,
        navItem,
        // The fetch routes that load the Bundle: the real script and locale
        // files; script assets answer empty (their libraries are stubs in
        // setup.js), stylesheets are <link>s jsdom never fetches.
        routes() {
            return [
                { method: 'GET', url: addon.script, text: read(frontend.script) },
                ...Object.entries(frontend.locales || {}).map(([lang, rel]) => (
                    { method: 'GET', url: addon.locales[lang], text: read(rel) })),
                ...addon.assets.filter((u) => /\.js\?/.test(u)).map((u) => ({ method: 'GET', url: u, text: '' })),
                { method: 'GET', url: '/api/plugins/nav', reply: { items: navItem ? [navItem] : [] } },
                { method: 'GET', url: '/api/plugins/frontend', reply: { addons: [addon] } },
            ];
        },
        // Marks the Bundle as active for the next import of app.js.
        activate() {
            window.LB_ADDONS = [addon];
        },
    };
}


// Nav items the default fetch mock reports from /api/plugins/nav: none — the
// core boots without any Add-on. Helpers that wait for the plugin-nav sync
// check the list instead of a hard-coded plugin id.
export const PLUGIN_NAV_ITEMS = [];

function jsonResponse(data, status = 200, headers = {}) {
    return {
        ok: status >= 200 && status < 300,
        status,
        headers: { get: (name) => headers[name] ?? null },
        json: async () => data,
        text: async () => JSON.stringify(data),
        blob: async () => new Blob([JSON.stringify(data)]),
    };
}

// A plain-text reply (an Add-on script the boot evaluates, see setup.js).
function textResponse(text, status = 200) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => JSON.parse(text),
        text: async () => text,
    };
}

// A reply the app reads through `resp.body.getReader()` — the SSE shape every
// streaming endpoint speaks (smart import, migration commit, bulk reference
// extraction). `frames` is the list of event objects; each becomes one
// `data: {...}\n\n` chunk, so the run step is drivable end to end.
function streamResponse(frames, status = 200) {
    const encoder = new TextEncoder();
    const chunks = frames.map((f) => encoder.encode(`data: ${JSON.stringify(f)}\n\n`));
    let i = 0;
    return {
        ok: status >= 200 && status < 300,
        status,
        body: {
            getReader: () => ({
                read: async () =>
                    (i < chunks.length
                        ? { done: false, value: chunks[i++] }
                        : { done: true, value: undefined }),
                cancel: async () => {},
            }),
        },
        json: async () => ({}),
    };
}

// Replaces global fetch with a route-table mock.
//
// routes: [{ method, url (string or RegExp), reply (object or ({method,url,body}) => object), status }]
// A route may carry `stream: [frame, …]` (or a function returning frames)
// instead of `reply`; it then answers an SSE body instead of JSON. A route
// with `text` (string) answers that text — e.g. an Add-on script.
// `reply` functions enable stateful endpoints (e.g. the publications list
// growing after a promote). Returns the recorded calls for assertions.
// Unmocked URLs answer 404 — every caller in app.js degrades gracefully.
export function installFetchMock(routes) {
    const calls = [];
    globalThis.fetch = vi.fn(async (url, options = {}) => {
        const method = (options.method || 'GET').toUpperCase();
        // JSON bodies are parsed for assertions; non-JSON bodies (e.g. FormData
        // from file uploads) are passed through unparsed.
        let body = null;
        if (options.body) {
            try { body = JSON.parse(options.body); } catch { body = options.body; }
        }
        calls.push({ method, url, body });
        for (const route of routes) {
            const matches =
                route.url instanceof RegExp ? route.url.test(url) : route.url === url;
            if (matches && (route.method || 'GET').toUpperCase() === method) {
                if (typeof route.text === 'string') {
                    return textResponse(route.text, route.status || 200);
                }
                if (route.stream) {
                    const frames =
                        typeof route.stream === 'function'
                            ? route.stream({ method, url, body })
                            : route.stream;
                    return streamResponse(frames, route.status || 200);
                }
                const data =
                    typeof route.reply === 'function'
                        ? route.reply({ method, url, body })
                        : route.reply;
                return jsonResponse(data, route.status || 200, route.headers);
            }
        }
        return jsonResponse({ detail: `Unmocked ${method} ${url}` }, 404);
    });
    return calls;
}

// Default API responses for everything the app touches between mount and a
// rendered (empty) plugin page. Tests prepend their own, more specific routes.
// The Item types table (bib_fields) as GET /api/papers/item-types serves it;
// tests/test_bib_write_rules.py keeps the fixture equal to the server's table.
export const ITEM_TYPES = JSON.parse(readFileSync(resolve(__dirname, '../fixtures/item_types.json'), 'utf-8'));

export function defaultRoutes() {
    return [
        { method: 'GET', url: '/api/papers/item-types', reply: ITEM_TYPES },
        { method: 'GET', url: '/api/categories/tree', reply: [] },
        { method: 'GET', url: '/api/stats', reply: {} },
        // Settings' nav counter (#210): the cheap pass, nothing to report by default.
        { method: 'GET', url: '/api/diagnosis?llm=false', reply: { findings: [], counts: {} } },
        { method: 'POST', url: '/api/maintenance/refresh-review/count', reply: { count: 0 } },
        { method: 'GET', url: '/api/categories', reply: [] },
        { method: 'GET', url: /^\/api\/papers\?/, reply: [] },
        // Annotations read from an Item's PDF (#229): none by default.
        {
            method: 'GET',
            url: /^\/api\/papers\/\d+\/annotations$/,
            reply: { file_modified: '2026-10-09T18:40:00+00:00', pages: 12, multiple_authors: false, has_sections: false, annotations: [] },
        },
        // Collections (#201): none by default (sidebar section, list filter).
        { method: 'GET', url: /^\/api\/collections(\?.*)?$/, reply: [] },
        { method: 'GET', url: '/api/settings', reply: {} },
        // "Start with Windows" (#214): not the Windows .exe, the switch stays hidden.
        { method: 'GET', url: '/api/system/autostart', reply: { available: false, enabled: false } },
        // No LLM connection by default: onboarding pending, every role off,
        // semantic toggle hidden.
        { method: 'GET', url: '/api/llm/status', reply: { reasoning: false, fast: false, embedding: false, connections: 0 } },
        { method: 'GET', url: '/api/llm/config', reply: { connections: [], roles: {}, status: { reasoning: false, fast: false, embedding: false, connections: 0 } } },
        // Startup check of the AI models (#209): nothing checked yet, no popup.
        { method: 'GET', url: '/api/llm/startup-check', reply: { checked_at: null, running: false, roles: {} } },
        { method: 'GET', url: '/api/llm/providers', reply: { providers: [
            { id: 'openai', label: 'OpenAI', base_url: 'https://api.openai.com/v1' },
            { id: 'custom', label: 'Custom (OpenAI-compatible)', base_url: '' },
        ] } },
        { method: 'GET', url: '/api/plugins/nav', reply: { items: PLUGIN_NAV_ITEMS } },
        // Add-ons (#187): none installed. The boot list itself is
        // `window.LB_ADDONS` (unset = empty), these answer runtime refreshes.
        { method: 'GET', url: '/api/plugins/frontend', reply: { addons: [] } },
        { method: 'GET', url: '/api/plugins', reply: { plugins: [], core: {} } },
        // Marketplace (#189): empty list by default, so a test that never
        // navigates there is unaffected. The Settings frame reads the same
        // view with `?refresh=false` (#207) for its Add-on entries.
        { method: 'GET', url: /^\/api\/marketplace(\?.*)?$/, reply: { addons: [], offline: false, has_index: true, index_date: null, core: {} } },
    ];
}


// First element matching `selector` whose trimmed text contains `text`.
export function findByText(selector, text) {
    return [...document.querySelectorAll(selector)].find((el) =>
        el.textContent.trim().includes(text),
    );
}

// True once every nav item from /api/plugins/nav has a sidebar link (trivially
// true when no plugin contributes one).
export function pluginNavRendered() {
    const hrefs = [...document.querySelectorAll('a')].map((el) => el.getAttribute('href'));
    return PLUGIN_NAV_ITEMS.every((item) => hrefs.includes('#' + item.route));
}

// Vue flushes its reactivity queue on microtasks; awaiting a macrotask hop
// lets pending fetch handlers AND the subsequent re-render settle.
export function flush() {
    return new Promise((resolve) => setTimeout(resolve, 0));
}

// Boots the SPA and navigates to the Marketplace through the sidebar (#189).
export async function bootMarketplace(extraRoutes = []) {
    document.body.innerHTML = '<div id="app"></div>';
    const calls = installFetchMock([...extraRoutes, ...defaultRoutes()]);
    await import('../../static/app.js');
    const link = await vi.waitFor(() => {
        const el = [...document.querySelectorAll('a')].find(
            (a) => a.getAttribute('href') === '#/marketplace',
        );
        if (!el || !pluginNavRendered()) throw new Error('Sidebar noch nicht fertig gerendert');
        return el;
    });
    await flush();
    link.click();
    await vi.waitFor(() => {
        if (!findByText('h2', 'Marketplace')) throw new Error('Marketplace view not rendered');
    });
    await flush();
    return calls;
}

// One card of GET /api/marketplace (#189/#193) for an installed Add-on,
// defaults filled in; `over` wins.
export function marketplaceCard(over = {}) {
    return {
        id: 'demo', name: 'Demo', tagline: '', description: '', author: '', license: '', homepage: '',
        trust: '', languages: [], tags: [], icon: '', screenshots: [], requires_source: false, size: null,
        in_index: false, source: 'bundle', installed: true, installed_version: '1.0.0',
        installed_not_in_index: false, offered_version: '', update_available: false, state: 'installed',
        incompatible_reason: null,
        addon_state: 'active', installed_permissions: [], permissions: [], versions: [],
        enabled: true, missing_consent: [], error: null, previous_version: '', rollback_available: false,
        pending_update: null, pending_removal: false,
        ...over,
    };
}

// Navigates to the Marketplace through the sidebar (the app already runs).
export async function openMarketplace() {
    const link = await vi.waitFor(() => {
        const el = document.querySelector('a[href="#/marketplace"]');
        if (!el) throw new Error('Marketplace link not rendered');
        return el;
    });
    link.click();
    await vi.waitFor(() => {
        if (!findByText('h2', 'Marketplace')) throw new Error('Marketplace view not rendered');
    });
    await flush();
}

// The switch on an Add-on's Marketplace card (#193), once the card is there.
export function addonSwitch(id) {
    return vi.waitFor(() => {
        const el = document.querySelector(`[data-testid="marketplace-toggle-${id}"]`);
        if (!el) throw new Error(`switch of ${id} not rendered`);
        return el;
    });
}

// Opens an Add-on's slide-over in the Marketplace and returns it.
export async function openAddonDetail(id) {
    const card = await vi.waitFor(() => {
        const el = document.querySelector(`[data-testid="marketplace-card-${id}"]`);
        if (!el) throw new Error(`card of ${id} not rendered`);
        return el;
    });
    card.click();
    return vi.waitFor(() => {
        const el = document.querySelector('[data-testid="marketplace-slideover"]');
        if (!el) throw new Error('slide-over not open');
        return el;
    });
}

// Boots the SPA straight into one Settings section by its address (#207):
// `section` is the part after /settings/ — 'general', 'ai-models', 'about',
// 'addons/<id>' … — exactly what a bookmark or a reload opens. Resolves once
// the frame shows that section and the sidebar (with any plugin nav) is up.
export async function bootSettings(extraRoutes = [], section = 'general') {
    document.body.innerHTML = '<div id="app"></div>';
    history.replaceState(null, '', '#/settings/' + section);
    const calls = installFetchMock([...extraRoutes, ...defaultRoutes()]);
    await import('../../static/app.js');
    await vi.waitFor(() => {
        const frame = document.querySelector('[data-testid="settings-frame"]');
        if (!frame || frame.getAttribute('data-section') !== section) throw new Error(`section ${section} not open`);
        if (!document.querySelector(`[data-settings-section="${section}"]`)) throw new Error(`section ${section} not rendered`);
        if (!document.querySelector('a[href="#/settings"]') || !pluginNavRendered()) throw new Error('Sidebar noch nicht fertig gerendert');
    });
    await flush();
    return calls;
}

// Moves the running app to another address, as a link or the address bar would.
export function goTo(hash) {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
}

// The open Settings section: the frame's address part after /settings/.
export function settingsSection() {
    const frame = document.querySelector('[data-testid="settings-frame"]');
    return frame ? frame.getAttribute('data-section') : null;
}

// Boots the SPA on the default route and waits until the shell (sidebar +
// plugin nav, if any) has rendered. For tests that drive the App root itself
// rather than a page — e.g. the banner and the blocking activation dialog.
export async function bootApp(extraRoutes = []) {
    document.body.innerHTML = '<div id="app"></div>';
    const calls = installFetchMock([...extraRoutes, ...defaultRoutes()]);
    await import('../../static/app.js');
    await vi.waitFor(() => {
        const link = [...document.querySelectorAll('a')].find(
            (el) => el.getAttribute('href') === '#/settings',
        );
        if (!link || !pluginNavRendered()) throw new Error('Sidebar noch nicht fertig gerendert');
    });
    await flush();
    return calls;
}

// "Reduce motion" as the OS would say it (jsdom has no matchMedia): views
// with motion then put their end state in at once — the knowledge network
// settles its layout synchronously instead of ticking on a timer (#241).
export function preferReducedMotion() {
    window.matchMedia = (query) => ({
        matches: /prefers-reduced-motion:\s*reduce/.test(query),
        media: query,
        onchange: null,
        addListener() {},
        removeListener() {},
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent() { return false; },
    });
}

// Boots the SPA straight on the Analysis page (#241), onboarding done, and
// waits until its head and the sidebar are up. `hash` may carry a scope
// (`#/analyse?collection_id=4`).
export async function bootAnalysis(extraRoutes = [], hash = '#/analyse') {
    document.body.innerHTML = '<div id="app"></div>';
    history.replaceState(null, '', hash);
    const calls = installFetchMock([
        ...extraRoutes,
        { method: 'GET', url: '/api/settings', reply: { ONBOARDING_COMPLETED: 'true' } },
        ...defaultRoutes(),
    ]);
    await import('../../static/app.js');
    await vi.waitFor(() => {
        if (!document.querySelector('[data-testid="analysis-meta"]')) throw new Error('analysis not rendered');
        if (!document.querySelector('a[href="#/settings"]') || !pluginNavRendered()) throw new Error('Sidebar noch nicht fertig gerendert');
    });
    await flush();
    return calls;
}

// Holds the requests `match(method, url)` picks until the test answers them,
// for states that only exist while a reply is outstanding (a first run, a
// streaming read). Every held request lands in the returned list as
// `{ method, url, body, signal, reply(data, status), respond(response) }`;
// everything else goes on to the fetch mock underneath.
export function holdRequests(match) {
    const base = globalThis.fetch;
    const held = [];
    globalThis.fetch = vi.fn((url, options = {}) => {
        const method = (options.method || 'GET').toUpperCase();
        if (!match(method, url)) return base(url, options);
        let body = null;
        if (options.body) {
            try { body = JSON.parse(options.body); } catch { body = options.body; }
        }
        return new Promise((resolve) => {
            held.push({
                method, url, body, signal: options.signal || null,
                reply: (data, status = 200) => resolve(jsonResponse(data, status)),
                respond: (response) => resolve(response),
            });
        });
    });
    return held;
}

// A streaming reply the test feeds frame by frame (`push`), ends (`end`) or
// leaves open. Like a real fetch body, a pending read rejects once the
// request's AbortController fires, so "Stop" can be observed (#241).
export function liveStream(signal) {
    const encoder = new TextEncoder();
    const chunks = [];
    let pending = null;
    let ended = false;
    let error = null;
    const settle = () => {
        if (!pending) return;
        const p = pending;
        if (error) { pending = null; p.reject(error); }
        else if (chunks.length) { pending = null; p.resolve({ done: false, value: chunks.shift() }); }
        else if (ended) { pending = null; p.resolve({ done: true, value: undefined }); }
    };
    if (signal) {
        signal.addEventListener('abort', () => {
            error = new DOMException('The operation was aborted.', 'AbortError');
            settle();
        });
    }
    return {
        response: {
            ok: true,
            status: 200,
            body: {
                getReader: () => ({
                    read: () => new Promise((resolve, reject) => { pending = { resolve, reject }; settle(); }),
                    cancel: async () => { ended = true; settle(); },
                }),
            },
            json: async () => ({}),
        },
        push(frame) {
            chunks.push(encoder.encode(`data: ${JSON.stringify(frame)}\n\n`));
            settle();
        },
        end() {
            ended = true;
            settle();
        },
        get aborted() {
            return !!(signal && signal.aborted);
        },
    };
}

// Vue's v-model listens on `input`; setting .value alone never reaches it.
// The textarea of a Markdown field (ADR-0026): the seam's fake renders one
// inside the editor host, which carries the field's id.
export function mdField(id) {
    return document.querySelector(`#${id} textarea`);
}

export function typeInto(el, value) {
    el.value = value;
    el.dispatchEvent(new Event('input'));
}

// The item list's filter bar. A pill by its key — year, pdf, doi, provenance,
// collection, sort, or addon-<id> — and whether it shows as active.
export function filterPill(key) {
    return document.querySelector(`[data-testid="filter-${key}"]`);
}

export function filterPillActive(key) {
    return filterPill(key).closest('.lb-fbar-pw').classList.contains('is-active');
}

// Opens a menu of the Collection head — coll-add or coll-more — unless it is
// open already, and returns it.
export async function openHeadMenu(key) {
    const trigger = await vi.waitFor(() => {
        const el = document.querySelector(`[data-fbar-key="${key}"] [data-fbar-trigger]`);
        if (!el) throw new Error(`menu key "${key}" not rendered`);
        return el;
    });
    if (trigger.getAttribute('aria-expanded') !== 'true') trigger.click();
    return vi.waitFor(() => {
        const menu = document.getElementById(`lb-fbar-pop-${key}`);
        if (!menu) throw new Error(`menu "${key}" not open`);
        return menu;
    });
}

// Opens a pill's popover (unless it is open already) and returns it.
export async function openFilterPill(key) {
    const pill = await vi.waitFor(() => {
        const el = filterPill(key);
        if (!el) throw new Error(`filter pill "${key}" not rendered`);
        return el;
    });
    if (pill.getAttribute('aria-expanded') !== 'true') pill.click();
    return vi.waitFor(() => {
        const pop = document.getElementById(`lb-fbar-pop-${key}`);
        if (!pop) throw new Error(`popover of "${key}" not open`);
        return pop;
    });
}
