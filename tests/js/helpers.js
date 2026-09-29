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

function jsonResponse(data, status = 200) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => data,
        text: async () => JSON.stringify(data),
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
                return jsonResponse(data, route.status || 200);
            }
        }
        return jsonResponse({ detail: `Unmocked ${method} ${url}` }, 404);
    });
    return calls;
}

// Default API responses for everything the app touches between mount and a
// rendered (empty) plugin page. Tests prepend their own, more specific routes.
export function defaultRoutes() {
    return [
        { method: 'GET', url: '/api/categories/tree', reply: [] },
        { method: 'GET', url: '/api/stats', reply: {} },
        { method: 'GET', url: '/api/categories', reply: [] },
        { method: 'GET', url: /^\/api\/papers\?/, reply: [] },
        { method: 'GET', url: '/api/settings', reply: {} },
        // No LLM connection by default: onboarding pending, every role off,
        // semantic toggle hidden.
        { method: 'GET', url: '/api/llm/status', reply: { reasoning: false, fast: false, embedding: false, connections: 0 } },
        { method: 'GET', url: '/api/llm/config', reply: { connections: [], roles: {}, status: { reasoning: false, fast: false, embedding: false, connections: 0 } } },
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
        // navigates there is unaffected.
        { method: 'GET', url: '/api/marketplace', reply: { addons: [], offline: false, has_index: true, index_date: null, core: {} } },
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

// Boots the SPA and navigates to Settings through the sidebar, then opens
// one settings tab by its data-testid (e.g. 'license-tab'). The plugin-nav sync
// fires a router.replace that would undo an earlier navigation, so wait for the
// plugin nav items (if any) to be rendered before clicking.
export async function bootSettings(extraRoutes = [], tabTestId = null) {
    document.body.innerHTML = '<div id="app"></div>';
    const calls = installFetchMock([...extraRoutes, ...defaultRoutes()]);
    await import('../../static/app.js');
    const settingsLink = await vi.waitFor(() => {
        const link = [...document.querySelectorAll('a')].find(
            (el) => el.getAttribute('href') === '#/settings',
        );
        if (!link || !pluginNavRendered()) throw new Error('Sidebar noch nicht fertig gerendert');
        return link;
    });
    await flush();
    settingsLink.click();
    await vi.waitFor(() => {
        if (!findByText('h2', 'Settings')) throw new Error('Settings view not rendered');
    });
    await flush();
    if (tabTestId) {
        document.querySelector(`[data-testid="${tabTestId}"]`).click();
        await flush();
    }
    return calls;
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

// Vue's v-model listens on `input`; setting .value alone never reaches it.
export function typeInto(el, value) {
    el.value = value;
    el.dispatchEvent(new Event('input'));
}
