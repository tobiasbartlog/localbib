// Add-on frontend registration (issue #187, ADR-0021).
//
// The boot loads every active Add-on (window.LB_ADDONS, as the index page
// renders it) before it mounts the router: the real fixture script of the
// contract package (`hello`) plus three written here — one with English only,
// one that throws, one that registers a route outside its namespace. The
// page is opened directly on the Add-on route, in a German UI.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import {
    PLUGIN_NAV_ITEMS, addonSwitch, defaultRoutes, findByText, flush, installFetchMock, marketplaceCard,
} from './helpers.js';

const FIXTURE = resolve(__dirname, '../../plugin_api/tests/fixtures/hello/frontend');
const read = (rel) => readFileSync(resolve(FIXTURE, rel), 'utf-8');

const HELLO = {
    id: 'hello',
    version: '0.1.0',
    assets: [],
    stylesheet: '/plugins/hello/static/hello.css?v=0.1.0',
    script: '/plugins/hello/static/hello.js?v=0.1.0',
    locales: {
        de: '/plugins/hello/static/locales/de.json?v=0.1.0',
        en: '/plugins/hello/static/locales/en.json?v=0.1.0',
    },
    default_language: 'en',
    nav: { id: 'hello', route: '/hello', view: 'hello.main' },
};
const LINGO = {
    id: 'lingo', version: '1.0.0', assets: [], stylesheet: null,
    script: '/plugins/lingo/static/lingo.js?v=1.0.0',
    locales: { en: '/plugins/lingo/static/en.json?v=1.0.0' },
    default_language: 'en',
    nav: { id: 'lingo', route: '/lingo', view: 'lingo.main' },
};
const BOOM = {
    id: 'boom', version: '1.0.0', assets: [], stylesheet: null,
    script: '/plugins/boom/static/boom.js?v=1.0.0', locales: {}, default_language: 'en',
    nav: { id: 'boom', route: '/boom', view: 'boom.main' },
};
const ROGUE = {
    id: 'rogue', version: '1.0.0', assets: [], stylesheet: null,
    script: '/plugins/rogue/static/rogue.js?v=1.0.0', locales: {}, default_language: 'en',
    nav: { id: 'rogue', route: '/rogue', view: 'rogue.main' },
};

const LINGO_JS = `
window.LocalBib.registerPlugin({
    id: 'lingo',
    views: { 'lingo.main': { template: '<p class="lingo-text">{{ $t("lingo.text") }}</p>' } },
});`;
const BOOM_JS = `throw new Error('boom at load');`;
const ROGUE_JS = `
window.LocalBib.registerPlugin({
    id: 'rogue',
    views: { 'rogue.main': { template: '<p>rogue</p>' } },
    subroutes: [{ path: '/settings/rogue', view: 'rogue.main' }],
});`;

// The Add-on switch sits on its Marketplace card (#193).
async function helloSwitch() {
    await vi.waitFor(() => {
        if (!document.querySelector('[data-testid="marketplace-grid"]')) throw new Error('marketplace not rendered');
    });
    return addonSwitch('hello');
}

const navItem = (e, label) => ({ id: e.id, label, icon: '<svg></svg>', route: e.nav.route, view: e.nav.view });

describe('Add-on frontend registration', () => {
    it('loads before the router, enforces the namespace, falls back per language, switches live', async () => {
        const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
        const warnings = vi.spyOn(console, 'warn');
        const changes = [];
        window.addEventListener('lb-plugins-changed', (e) => changes.push(e.detail));

        let helloOn = true;
        const helloState = () => ({
            id: 'hello', name: 'Hello', version: '0.1.0', source: 'bundle',
            enabled: helloOn, state: helloOn ? 'active' : 'inactive',
        });
        const toggle = (on) => () => { helloOn = on; return helloState(); };

        document.body.innerHTML = '<div id="app"></div>';
        window.LB_LANG = 'de';
        window.LB_ADDONS = [HELLO, LINGO, BOOM, ROGUE];
        history.replaceState(null, '', '#/hello');
        const calls = installFetchMock([
            { method: 'GET', url: HELLO.script, text: read('hello.js') },
            { method: 'GET', url: HELLO.locales.de, text: read('locales/de.json') },
            { method: 'GET', url: HELLO.locales.en, text: read('locales/en.json') },
            { method: 'GET', url: LINGO.script, text: LINGO_JS },
            { method: 'GET', url: LINGO.locales.en, reply: { 'lingo.nav': 'Lingo', 'lingo.text': 'Only English here' } },
            { method: 'GET', url: BOOM.script, text: BOOM_JS },
            { method: 'GET', url: ROGUE.script, text: ROGUE_JS },
            {
                method: 'GET',
                url: '/api/plugins/nav',
                reply: () => ({ items: [
                    ...PLUGIN_NAV_ITEMS,
                    ...(helloOn ? [navItem(HELLO, 'hello.nav.label')] : []),
                    navItem(LINGO, 'lingo.nav'), navItem(BOOM, 'boom.nav'), navItem(ROGUE, 'rogue.nav'),
                ] }),
            },
            { method: 'GET', url: '/api/plugins/frontend', reply: () => ({ addons: helloOn ? [HELLO, LINGO] : [LINGO] }) },
            { method: 'GET', url: '/api/plugins', reply: () => ({ plugins: [helloState()], core: {} }) },
            {
                method: 'GET', url: '/api/marketplace',
                reply: () => ({
                    addons: [marketplaceCard({ id: 'hello', name: 'Hello', installed_version: '0.1.0',
                                               enabled: helloOn, addon_state: helloOn ? 'active' : 'inactive' })],
                    offline: false, has_index: true, index_date: null, core: {},
                }),
            },
            { method: 'POST', url: '/api/plugins/hello/disable', reply: toggle(false) },
            { method: 'POST', url: '/api/plugins/hello/enable', reply: toggle(true) },
            // A decided UI language — otherwise the onboarding proposes the browser's.
            { method: 'GET', url: '/api/settings', reply: { UI_LANGUAGE: 'de' } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');

        // A reload on the Add-on route renders its page — in German, the UI
        // language — and never passes through the item list first.
        await vi.waitFor(() => {
            if (!findByText('h1.hello-title', 'Hallo')) throw new Error('hello page not rendered');
        });
        expect(window.location.hash).toBe('#/hello');
        // The route existed when the router resolved its first location.
        expect(warnings.mock.calls.some((c) => String(c[0]).includes('No match found'))).toBe(false);
        expect(calls.some((c) => c.url.startsWith('/api/papers'))).toBe(false);
        expect(document.querySelector('link[data-addon-href="' + HELLO.stylesheet + '"]')).not.toBeNull();
        expect(window.LocalBib.isActive('hello')).toBe(true);

        // The sidebar shows the working Add-ons, labelled from their catalogs.
        await vi.waitFor(() => {
            if (!document.querySelector('a[href="#/lingo"]')) throw new Error('nav not rendered');
        });
        expect(document.querySelector('a[href="#/hello"]').textContent).toContain('Hallo');

        // Subroute.
        document.querySelector('a.hello-link').click();
        await vi.waitFor(() => {
            if (!findByText('h1.hello-title', 'Gruss Nr. 42')) throw new Error('subroute not rendered');
        });

        // German UI, Add-on ships English only: English text, no key paths.
        document.querySelector('a[href="#/lingo"]').click();
        await vi.waitFor(() => {
            if (!findByText('p.lingo-text', 'Only English here')) throw new Error('lingo page not rendered');
        });
        expect(document.body.textContent).not.toContain('⟦');
        expect(document.querySelector('a[href="#/lingo"]').textContent).toContain('Lingo');

        // A throwing script and a namespace breach end in `error`; the app runs on.
        expect(window.LocalBib.state('boom')).toBe('error');
        expect(window.LocalBib.state('rogue')).toBe('error');
        expect(window.LocalBib.isActive('rogue')).toBe(false);
        const logged = errors.mock.calls.map((c) => c.join(' '));
        expect(logged.some((m) => m.includes('"boom"'))).toBe(true);
        expect(logged.some((m) => m.includes('"rogue"') && m.includes('/settings/rogue'))).toBe(true);
        expect(document.querySelector('a[href="#/boom"]')).toBeNull();
        expect(document.querySelector('a[href="#/rogue"]')).toBeNull();

        // Switching off (Marketplace card): route, nav entry and locale namespace go.
        document.querySelector('a[href="#/hello"]').click();
        await vi.waitFor(() => {
            if (!findByText('h1.hello-title', 'Hallo')) throw new Error('hello page not rendered');
        });
        document.querySelector('a[href="#/marketplace"]').click();
        (await helloSwitch()).click();
        await vi.waitFor(() => {
            if (document.querySelector('a[href="#/hello"]')) throw new Error('nav entry still there');
        });
        expect(window.LocalBib.isActive('hello')).toBe(false);
        expect(window.LocalBib.t('hello.view.title')).toBe('⟦hello.view.title⟧');
        expect(changes).toContainEqual({ id: 'hello', active: false });
        // Navigating to the old route (as the back button would) finds nothing.
        history.pushState(null, '', '#/hello');
        window.dispatchEvent(new PopStateEvent('popstate', { state: null }));
        await vi.waitFor(() => {
            if (document.querySelector('[data-testid="marketplace-toggle-hello"]')) throw new Error('still on the marketplace');
        });
        expect(document.querySelector('.hello-view')).toBeNull();

        // Switching on again brings everything back without a reload — and
        // without loading the script a second time.
        document.querySelector('a[href="#/marketplace"]').click();
        (await helloSwitch()).click();
        const link = await vi.waitFor(() => {
            const a = document.querySelector('a[href="#/hello"]');
            if (!a) throw new Error('nav entry not back');
            return a;
        });
        expect(window.LocalBib.isActive('hello')).toBe(true);
        expect(changes).toContainEqual({ id: 'hello', active: true });
        link.click();
        await vi.waitFor(() => {
            if (!findByText('h1.hello-title', 'Hallo')) throw new Error('hello page not back');
        });
        expect(calls.filter((c) => c.url === HELLO.script)).toHaveLength(1);
        errors.mockRestore();
        warnings.mockRestore();
    });
});
