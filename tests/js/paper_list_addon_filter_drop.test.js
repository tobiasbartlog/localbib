// PaperList's reaction to `lb-plugins-changed` while the list stays open
// (slot "item-list-filter", #190). `plugin_slots.test.js` only exercises the
// drop by disabling the Add-on through Settings and navigating back to a
// freshly mounted list; this file keeps the SAME PaperList instance mounted
// with a selection active and fires the event the app itself dispatches
// (`applyAddons` -> `emitAddonChange`, exposed as `window.LocalBib.events.changed`)
// to prove the handler on the live instance drops the selection and reloads.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { bootApp, flush } from './helpers.js';

const FIXTURE = resolve(__dirname, '../../plugin_api/tests/fixtures/hello');
const read = (rel) => readFileSync(resolve(FIXTURE, rel), 'utf-8');

const HELLO = {
    id: 'hello', version: '0.1.0', assets: [],
    stylesheet: '/plugins/hello/static/hello.css?v=0.1.0',
    script: '/plugins/hello/static/hello.js?v=0.1.0',
    locales: {
        de: '/plugins/hello/static/locales/de.json?v=0.1.0',
        en: '/plugins/hello/static/locales/en.json?v=0.1.0',
    },
    default_language: 'en',
    nav: { id: 'hello', route: '/hello', view: 'hello.main' },
};

const PAPERS = [
    { id: 1, title: 'First item', authors: 'Smith, A.', year: 2020, cite_key: 'smith2020', categories: [], custom_fields: [], filename: '' },
    { id: 2, title: 'Second item', authors: 'Jones, B.', year: 2021, cite_key: 'jones2021', categories: [], custom_fields: [], filename: '' },
];

const papersCalls = (calls) => calls.filter((c) => c.method === 'GET' && c.url.startsWith('/api/papers?'));
const filterButton = () => document.querySelector('.lb-toolbar-l button');
const badge = () => filterButton().querySelector('.lb-pill-accent');

async function openFilters() {
    const btn = await vi.waitFor(() => {
        const b = filterButton();
        if (!b) throw new Error('item list not rendered');
        return b;
    });
    if (!document.querySelector('.lb-filter-bar')) btn.click();
    await flush();
}

describe('PaperList drops an Add-on filter live on lb-plugins-changed', () => {
    it('clears the selection and reloads without cite_keys while the list stays open', async () => {
        window.LB_LANG = 'en';
        window.LB_ADDONS = [HELLO];
        history.replaceState(null, '', '#/');
        const calls = await bootApp([
            { method: 'GET', url: HELLO.script, text: read('frontend/hello.js') },
            { method: 'GET', url: HELLO.locales.de, text: read('frontend/locales/de.json') },
            { method: 'GET', url: HELLO.locales.en, text: read('frontend/locales/en.json') },
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            { method: 'GET', url: '/api/papers', reply: PAPERS },
            { method: 'GET', url: '/api/plugins/frontend', reply: { addons: [HELLO] } },
            {
                method: 'GET', url: '/api/plugins',
                reply: { plugins: [{ id: 'hello', name: 'Hello', version: '0.1.0', source: 'bundle', enabled: true, state: 'active' }], core: {} },
            },
        ]);

        expect(window.LocalBib.isActive('hello')).toBe(true);
        const list = document.querySelector('.lb-toolbar-l');
        await openFilters();
        const select = await vi.waitFor(() => {
            const s = document.querySelector('select.hello-filter-select');
            if (!s) throw new Error('hello filter not rendered');
            return s;
        });
        select.value = 'greeted';
        select.dispatchEvent(new Event('change'));
        await vi.waitFor(() => {
            const last = papersCalls(calls).at(-1);
            if (!last || !last.url.includes('cite_keys=')) throw new Error('no cite_keys request yet');
        });
        expect(papersCalls(calls).at(-1).url).toContain(`cite_keys=${encodeURIComponent('hello2024,hello2025')}&`);
        await flush();
        expect(badge().textContent.trim()).toBe('1');
        const before = papersCalls(calls).length;

        // The exact event `applyAddons`/`emitAddonChange` dispatches on a
        // switch -- fired directly, the list stays the same mounted instance
        // (no route change, no fresh PaperList).
        window.dispatchEvent(new CustomEvent(window.LocalBib.events.changed, { detail: { id: 'hello', active: false } }));

        await vi.waitFor(() => {
            if (papersCalls(calls).length === before) throw new Error('no reload after the drop');
        });
        expect(papersCalls(calls).at(-1).url).not.toContain('cite_keys');
        await flush();
        expect(badge()).toBeNull();
        expect(document.querySelector('select.hello-filter-select').value).toBe('');
        // Still the same list -- not a remount from navigating away and back.
        expect(document.querySelector('.lb-toolbar-l')).toBe(list);
    });
});
