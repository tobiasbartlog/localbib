// Marketplace install + Zustimmung (#191, ADR-0021).
//
// One boot, three scenarios in order: an Offiziell install from the index
// (byte progress, dialog with the app-rights sentence LAST, declined -> the
// card offers the dialog again), a file install (warning, checksum, dialog
// with the sentence FIRST, agreed -> the Add-on's nav entry appears without a
// reload and its page renders) and "Aus Ordner laden". The Add-on is the
// contract package's `hello` fixture, loaded from its real frontend files.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeAll, describe, expect, it, vi } from 'vitest';

import { bootMarketplace, findByText, flush } from './helpers.js';

const FIXTURE = resolve(__dirname, '../../plugin_api/tests/fixtures/hello/frontend');
const read = (rel) => readFileSync(resolve(FIXTURE, rel), 'utf-8');

const HELLO = {
    id: 'hello', version: '0.1.0', assets: [],
    stylesheet: '/plugins/hello/static/hello.css?v=0.1.0',
    script: '/plugins/hello/static/hello.js?v=0.1.0',
    locales: { en: '/plugins/hello/static/locales/en.json?v=0.1.0' },
    default_language: 'en',
    nav: { id: 'hello', route: '/hello', view: 'hello.main' },
};
const HELLO_NAV = { id: 'hello', label: 'hello.nav.label', icon: '<svg></svg>', route: '/hello', view: 'hello.main' };

const PERMISSIONS = [
    { key: 'library.read', enforced: true },
    { key: 'network', enforced: false },
];

function card(over) {
    return {
        id: 'demo1', name: 'DemoOne', tagline: 'An official demo', description: '',
        author: '', license: '', homepage: '', trust: 'official',
        languages: ['en'], tags: [], icon: '', screenshots: [],
        requires_source: false, size: 2048, in_index: true, source: '',
        installed: false, installed_version: '', installed_not_in_index: false,
        offered_version: '1.0.0', update_available: false, state: 'not_installed',
        addon_state: '', installed_permissions: [], permissions: PERMISSIONS, versions: [],
        ...over,
    };
}

const state = { demo1: 'none', hello: 'none' };

function marketplaceReply() {
    const addons = [];
    addons.push(state.demo1 === 'none'
        ? card({})
        : card({ installed: true, installed_version: '1.0.0', source: 'bundle', state: 'installed',
                 addon_state: 'consent_pending', installed_permissions: PERMISSIONS }));
    if (state.hello !== 'none') {
        addons.push(card({
            id: 'hello', name: 'Hello', trust: '', in_index: false, source: 'bundle', installed: true,
            installed_version: '0.1.0', state: 'installed', offered_version: '',
            addon_state: state.hello === 'active' ? 'active' : 'consent_pending',
        }));
    }
    return { addons, offline: false, has_index: true, index_date: null, core: {} };
}

function dialog() {
    return document.querySelector('[data-testid="consent-dialog"]');
}

// true when `a` comes before `b` in document order
function before(a, b) {
    return !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

let calls;

describe('marketplace install and consent', () => {
    beforeAll(async () => {
        calls = await bootMarketplace([
            { method: 'GET', url: '/api/marketplace', reply: marketplaceReply },
            {
                method: 'POST', url: '/api/marketplace/install/demo1',
                stream: () => {
                    state.demo1 = 'pending';
                    return [
                        { type: 'progress', step: 'download', received: 0, total: 2048 },
                        { type: 'progress', step: 'download', received: 1024, total: 2048 },
                        { type: 'progress', step: 'download', received: 2048, total: 2048 },
                        { type: 'progress', step: 'verify' },
                        { type: 'progress', step: 'extract' },
                        { type: 'progress', step: 'activate' },
                        {
                            type: 'complete',
                            addon: { id: 'demo1', state: 'consent_pending' },
                            consent: { id: 'demo1', name: 'DemoOne', version: '1.0.0', trust: 'official',
                                       origin: 'index', sha256: '', permissions: PERMISSIONS,
                                       missing: ['library.read', 'network'] },
                        },
                    ];
                },
            },
            {
                method: 'POST', url: '/api/marketplace/install-file',
                reply: () => {
                    state.hello = 'pending';
                    return {
                        addon: { id: 'hello', state: 'consent_pending' },
                        consent: { id: 'hello', name: 'Hello', version: '0.1.0', trust: 'third-party',
                                   origin: 'file', sha256: 'ab12cd34', permissions: PERMISSIONS,
                                   missing: ['library.read', 'network'] },
                    };
                },
            },
            { method: 'POST', url: '/api/plugins/hello/consent', reply: { id: 'hello', state: 'consent_pending' } },
            {
                method: 'POST', url: '/api/plugins/hello/enable',
                reply: () => { state.hello = 'active'; return { id: 'hello', state: 'active' }; },
            },
            { method: 'GET', url: '/api/plugins/frontend', reply: () => ({ addons: state.hello === 'active' ? [HELLO] : [] }) },
            { method: 'GET', url: '/api/plugins/nav', reply: () => ({ items: state.hello === 'active' ? [HELLO_NAV] : [] }) },
            { method: 'GET', url: HELLO.script, text: read('hello.js') },
            { method: 'GET', url: HELLO.locales.en, text: read('locales/en.json') },
            {
                method: 'POST', url: '/api/marketplace/dev-path',
                // A folder declaring no Berechtigung: off, yet the dialog is
                // due once (`confirm`) — it carries the all-rights warning.
                reply: ({ body }) => ({
                    addon: { id: 'workshop', state: 'inactive', enabled: false, needs_confirmation: true },
                    consent: { id: 'workshop', name: 'Workshop ' + body.path, version: '0.1.0', trust: 'third-party',
                               origin: 'dev', sha256: '', permissions: [], missing: [], confirm: true },
                }),
            },
        ]);
    });

    it('installs from the index with byte progress; Offiziell puts the rights sentence last; declining keeps consent open', async () => {
        document.querySelector('[data-testid="marketplace-button-demo1"]').click();
        const d = await vi.waitFor(() => {
            const el = dialog();
            if (!el) throw new Error('consent dialog not shown');
            return el;
        });
        expect(calls.some((c) => c.method === 'POST' && c.url === '/api/marketplace/install/demo1'
            && c.body.version === '1.0.0')).toBe(true);
        expect(findByText('[data-testid="consent-trust"]', 'Official')).toBeTruthy();
        expect(d.querySelector('[data-testid="consent-permission-library.read"]').textContent).toContain('Enforced');
        expect(d.querySelector('[data-testid="consent-permission-network"]').textContent).toContain('Declared only');
        const rights = d.querySelector('[data-testid="consent-rights"]');
        expect(rights.textContent).toContain('all the rights of the app');
        expect(before(d.querySelector('[data-testid="consent-permissions"]'), rights)).toBe(true);

        d.querySelector('[data-testid="consent-decline"]').click();
        await vi.waitFor(() => {
            if (dialog()) throw new Error('dialog still open');
            if (!findByText('[data-testid="marketplace-button-demo1"]', 'Review permissions')) throw new Error('card not refreshed');
        });
        expect(calls.some((c) => c.url.includes('/consent'))).toBe(false);
        expect(document.querySelector('[data-testid="marketplace-progress"]')).toBeNull();

        // The card reopens the same dialog.
        document.querySelector('[data-testid="marketplace-button-demo1"]').click();
        await flush();
        expect(dialog()).toBeTruthy();
        dialog().querySelector('[data-testid="consent-decline"]').click();
        await flush();
    });

    it('installs from a file behind a warning, shows the checksum, Drittanbieter puts the rights sentence first, and agreeing activates live', async () => {
        document.querySelector('[data-testid="marketplace-from-file"]').click();
        await flush();
        expect(document.querySelector('[data-testid="marketplace-file-warning"]').textContent).toContain('all the rights of the app');
        const input = document.querySelector('[data-testid="marketplace-file-input"]');
        Object.defineProperty(input, 'files', { value: [new File(['PK'], 'hello-0.1.0.zip')] });
        input.dispatchEvent(new Event('change'));
        await flush();
        document.querySelector('[data-testid="marketplace-file-install"]').click();

        const d = await vi.waitFor(() => {
            const el = dialog();
            if (!el) throw new Error('consent dialog not shown');
            return el;
        });
        expect(d.querySelector('[data-testid="consent-file-warning"]')).toBeTruthy();
        expect(d.querySelector('[data-testid="consent-sha256"]').textContent).toContain('ab12cd34');
        expect(findByText('[data-testid="consent-trust"]', 'Third-party')).toBeTruthy();
        const rights = d.querySelector('[data-testid="consent-rights"]');
        expect(before(rights, d.querySelector('[data-testid="consent-permissions"]'))).toBe(true);
        expect(document.querySelector('a[href="#/hello"]')).toBeNull();

        d.querySelector('[data-testid="consent-accept"]').click();
        const link = await vi.waitFor(() => {
            const a = document.querySelector('a[href="#/hello"]');
            if (!a) throw new Error('nav entry not there');
            return a;
        });
        expect(dialog()).toBeNull();
        const consentCall = calls.find((c) => c.url === '/api/plugins/hello/consent');
        expect(consentCall.body.permissions).toEqual(['library.read', 'network']);
        expect(calls.some((c) => c.url === '/api/plugins/hello/enable')).toBe(true);
        expect(window.LocalBib.isActive('hello')).toBe(true);

        link.click();
        await vi.waitFor(() => {
            if (!findByText('h1.hello-title', 'Hello')) throw new Error('hello page not rendered');
        });
    });

    it('loads an Add-on from a folder into the development list and asks for consent', async () => {
        document.querySelector('a[href="#/marketplace"]').click();
        await vi.waitFor(() => {
            if (!document.querySelector('[data-testid="marketplace-from-folder"]')) throw new Error('marketplace not back');
        });
        document.querySelector('[data-testid="marketplace-from-folder"]').click();
        await flush();
        const input = document.querySelector('[data-testid="marketplace-folder-input"]');
        input.value = 'C:\\src\\workshop';
        input.dispatchEvent(new Event('input'));
        await flush();
        document.querySelector('[data-testid="marketplace-folder-load"]').click();
        await vi.waitFor(() => {
            if (!dialog()) throw new Error('consent dialog not shown');
        });
        const call = calls.find((c) => c.url === '/api/marketplace/dev-path');
        expect(call.body).toEqual({ path: 'C:\\src\\workshop' });
        expect(dialog().querySelector('[data-testid="consent-rights"]').textContent).toContain('all the rights of the app');
        expect(document.querySelector('[data-testid="marketplace-filter-dev"]').className).toContain('is-on');
        dialog().querySelector('[data-testid="consent-decline"]').click();
        await flush();
    });
});
