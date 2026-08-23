// Shared helpers for the SPA smoke tests (issue #54).
//
// The SPA is one self-mounting browser script (static/app.js): importing it
// evaluates the whole file, registers routes, and mounts onto #app. Each test
// file therefore boots the app exactly once (vitest isolates module graphs per
// file) and drives it through the DOM, with `fetch` replaced by a small
// route-table mock.
import { vi } from 'vitest';


// Nav items the default fetch mock reports from /api/plugins/nav. Empty in a
// plugin-free tree — helpers that wait for the plugin-nav sync check the list
// instead of a hard-coded plugin id.
export const PLUGIN_NAV_ITEMS = [
];

function jsonResponse(data, status = 200) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => data,
    };
}

// Replaces global fetch with a route-table mock.
//
// routes: [{ method, url (string or RegExp), reply (object or ({method,url,body}) => object), status }]
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
        // No embedding model configured by default -> semantic toggle stays hidden.
        { method: 'GET', url: '/api/settings', reply: {} },
        { method: 'GET', url: '/api/plugins/nav', reply: { items: PLUGIN_NAV_ITEMS } },
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

// Boots the SPA and navigates to Einstellungen through the sidebar, then opens
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
        if (!findByText('h2', 'Einstellungen')) throw new Error('Einstellungen nicht gerendert');
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
