// The four Add-on Slots (issue #190, ADR-0021).
//
// The real fixture Add-on of the contract package (`hello`) occupies the Item
// list filter, the Item detail aside and the Research Chat context with its
// own components, and its settings section — in its Marketplace slide-over
// since #193 — through the core's generic renderer (fields declared in its
// Manifest). A second Add-on written here
// (`solo`, English only) brings its own settings component; a third (`odd`)
// names a slot that does not exist and is rejected.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import {
    addonSwitch, bootApp, findByText, flush, marketplaceCard, openAddonDetail, openMarketplace, typeInto,
} from './helpers.js';

const FIXTURE = resolve(__dirname, '../../plugin_api/tests/fixtures/hello');
const read = (rel) => readFileSync(resolve(FIXTURE, rel), 'utf-8');
const HELLO_FIELDS = JSON.parse(read('plugin.json')).settings;

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
const SOLO = {
    id: 'solo', version: '1.0.0', assets: [], stylesheet: null,
    script: '/plugins/solo/static/solo.js?v=1.0.0',
    locales: { en: '/plugins/solo/static/en.json?v=1.0.0' },
    default_language: 'en', nav: null,
};
const ODD = {
    id: 'odd', version: '1.0.0', assets: [], stylesheet: null,
    script: '/plugins/odd/static/odd.js?v=1.0.0', locales: {}, default_language: 'en', nav: null,
};
const SOLO_JS = `
window.LocalBib.registerPlugin({
    id: 'solo',
    slots: { settings: { key: 'solo.settings', component: {
        props: ['addonId', 'fields', 'values', 'save'],
        template: '<p class="solo-settings">{{ $t("solo.settings.title") }} {{ values.mode }} ({{ addonId }})</p>',
    } } },
});`;
const ODD_JS = `
window.LocalBib.registerPlugin({
    id: 'odd',
    slots: { toolbar: { key: 'odd.bar', component: { template: '<b>odd</b>' } } },
});`;

const PAPERS = [
    { id: 1, title: 'First item', authors: 'Smith, A.', year: 2020, cite_key: 'smith2020', categories: [], custom_fields: [], filename: '' },
    { id: 2, title: 'Second item', authors: 'Jones, B.', year: 2021, cite_key: 'jones2021', categories: [], custom_fields: [], filename: '' },
];

const papersCalls = (calls) => calls.filter((c) => c.method === 'GET' && c.url.startsWith('/api/papers?'));
const filterButton = () => document.querySelector('.lb-toolbar-l button');
const badge = () => filterButton().querySelector('.lb-pill-accent');

function go(hash) {
    window.location.hash = hash;
    window.dispatchEvent(new HashChangeEvent('hashchange'));
}

async function openFilters() {
    const btn = await vi.waitFor(() => {
        const b = filterButton();
        if (!b) throw new Error('item list not rendered');
        return b;
    });
    if (!document.querySelector('.lb-filter-bar')) btn.click();
    await flush();
}

describe('Add-on slots', () => {
    it('renders the four slots for an active Add-on and drops them when it is switched off', async () => {
        const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
        let helloOn = true;
        const helloState = () => ({
            id: 'hello', name: 'Hello', version: '0.1.0', source: 'bundle',
            enabled: helloOn, state: helloOn ? 'active' : 'inactive',
        });
        const soloState = { id: 'solo', name: 'Solo', version: '1.0.0', source: 'bundle', enabled: true, state: 'active' };
        let helloStored = { salutation: '', api_key: '', greetings_folder: '', shout: false };
        const helloSettings = () => ({
            id: 'hello', fields: HELLO_FIELDS,
            values: {
                ...helloStored,
                api_key: { has_key: !!helloStored.api_key, key_hint: helloStored.api_key ? 'hk-…' + helloStored.api_key.slice(-4) : '' },
            },
        });

        window.LB_LANG = 'en';
        window.LB_ADDONS = [HELLO, SOLO, ODD];
        history.replaceState(null, '', '#/');
        const calls = await bootApp([
            { method: 'GET', url: HELLO.script, text: read('frontend/hello.js') },
            { method: 'GET', url: HELLO.locales.de, text: read('frontend/locales/de.json') },
            { method: 'GET', url: HELLO.locales.en, text: read('frontend/locales/en.json') },
            { method: 'GET', url: SOLO.script, text: SOLO_JS },
            { method: 'GET', url: SOLO.locales.en, reply: { 'solo.settings.title': 'Solo speaks English only' } },
            { method: 'GET', url: ODD.script, text: ODD_JS },
            { method: 'GET', url: /^\/api\/papers\?/, reply: PAPERS },
            { method: 'GET', url: '/api/papers', reply: PAPERS },
            { method: 'GET', url: '/api/papers/1', reply: PAPERS[0] },
            { method: 'GET', url: '/api/papers/2', reply: PAPERS[1] },
            { method: 'GET', url: /^\/api\/papers\/\d+\/references/, reply: { references: [], count: 0 } },
            { method: 'GET', url: /^\/api\/papers\/\d+\/bibtex/, reply: { key: '' } },
            { method: 'GET', url: /^\/api\/research-chat\/chunk-status/, reply: { papers: [{ paper_id: 1, chunked: true, chunk_count: 2 }] } },
            { method: 'POST', url: '/api/research-chat/ask', reply: { answer: 'ok', sources: [] } },
            { method: 'GET', url: '/api/plugins/frontend', reply: () => ({ addons: helloOn ? [HELLO, SOLO] : [SOLO] }) },
            { method: 'GET', url: '/api/plugins', reply: () => ({ plugins: [helloState(), soloState], core: {} }) },
            {
                method: 'GET', url: '/api/marketplace',
                reply: () => ({
                    addons: [
                        marketplaceCard({ id: 'hello', name: 'Hello', installed_version: '0.1.0',
                                          enabled: helloOn, addon_state: helloOn ? 'active' : 'inactive' }),
                        marketplaceCard({ id: 'solo', name: 'Solo' }),
                    ],
                    offline: false, has_index: true, index_date: null, core: {},
                }),
            },
            { method: 'POST', url: '/api/plugins/hello/disable', reply: () => { helloOn = false; return helloState(); } },
            { method: 'GET', url: '/api/plugins/hello/settings', reply: () => helloSettings() },
            {
                method: 'PUT', url: '/api/plugins/hello/settings',
                reply: ({ body }) => {
                    for (const [k, v] of Object.entries(body.values)) if (v !== null) helloStored[k] = v;
                    return helloSettings();
                },
            },
            { method: 'GET', url: '/api/plugins/solo/settings', reply: { id: 'solo', fields: [], values: { mode: 'calm' } } },
            { method: 'PUT', url: '/api/settings', reply: {} },
            { method: 'GET', url: '/api/settings', reply: { UI_LANGUAGE: 'en' } },
        ]);

        // An unknown slot name rejects the registration like any namespace breach.
        expect(window.LocalBib.state('odd')).toBe('error');
        expect(errors.mock.calls.some((c) => c.join(' ').includes('"toolbar"'))).toBe(true);
        expect(window.LocalBib.isActive('hello')).toBe(true);

        // --- item-list-filter -------------------------------------------------
        await openFilters();
        const select = await vi.waitFor(() => {
            const s = document.querySelector('select.hello-filter-select');
            if (!s) throw new Error('hello filter not rendered');
            return s;
        });
        expect(findByText('.hello-filter', 'Greeting')).toBeTruthy();
        expect(badge()).toBeNull();
        select.value = 'greeted';
        select.dispatchEvent(new Event('change'));
        await vi.waitFor(() => {
            const last = papersCalls(calls).at(-1);
            if (!last.url.includes('cite_keys=')) throw new Error('no cite_keys request yet');
        });
        expect(papersCalls(calls).at(-1).url).toContain(`cite_keys=${encodeURIComponent('hello2024,hello2025')}&`);
        await flush();
        expect(badge().textContent.trim()).toBe('1');

        // Reset clears the selection and fetches the unfiltered list.
        const before = papersCalls(calls).length;
        findByText('button', 'Reset all filters').click();
        await vi.waitFor(() => {
            if (papersCalls(calls).length === before) throw new Error('no reload after reset');
        });
        expect(papersCalls(calls).at(-1).url).not.toContain('cite_keys');
        await flush();
        expect(badge()).toBeNull();
        expect(document.querySelector('select.hello-filter-select').value).toBe('');

        // --- item-detail-aside ------------------------------------------------
        go('#/paper/1');
        const aside1 = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="metadata-column"] .hello-aside');
            if (!el || el.getAttribute('data-item-id') !== '1') throw new Error('aside for item 1 not rendered');
            return el;
        });
        expect(aside1.textContent).toContain('Greeting for smith2020');
        go('#/paper/2');
        const aside2 = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="metadata-column"] .hello-aside');
            if (!el || el.getAttribute('data-item-id') !== '2') throw new Error('aside for item 2 not rendered');
            return el;
        });
        expect(aside2.textContent).toContain('jones2021');
        expect(aside2).not.toBe(aside1);  // remounted, not patched in place

        // --- research-chat-context --------------------------------------------
        go('#/research-chat?paper_ids=1');
        const toggle = await vi.waitFor(() => {
            const el = document.querySelector('input.hello-chat-toggle');
            if (!el) throw new Error('chat toggle not rendered');
            return el;
        });
        expect(findByText('label.hello-chat', 'Include greetings')).toBeTruthy();
        const input = await vi.waitFor(() => {
            const el = document.querySelector(`input[placeholder="${window.LB_I18N.en['chat.inputPlaceholder']}"]`);
            if (!el || el.disabled) throw new Error('chat input not ready');
            return el;
        });
        const ask = async (question) => {
            typeInto(input, question);
            await flush();
            input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
            await vi.waitFor(() => {
                if (!calls.some((c) => c.url === '/api/research-chat/ask' && c.body.question === question)) {
                    throw new Error('question not sent');
                }
            });
            await flush();
            return calls.filter((c) => c.url === '/api/research-chat/ask').at(-1).body;
        };
        const off = await ask('first question');
        expect(off.extra_context).toBeUndefined();
        toggle.click();
        await flush();
        const on = await ask('second question');
        expect(on.extra_context).toEqual(['Greetings the user has sent so far.']);

        // --- settings in the Marketplace slide-over (#193): generic for hello,
        // own component for solo --------------------------------------------
        // Settings itself carries no Add-on cards any more.
        go('#/settings');
        await vi.waitFor(() => {
            if (!findByText('h2', 'Settings')) throw new Error('settings not rendered');
        });
        findByText('button', window.LB_I18N.en['settings.tabAppearance']).click();
        await flush();
        expect(document.querySelector('[data-testid="addons-section"]')).toBeNull();
        expect(document.querySelector('[data-testid^="addon-settings-"]')).toBeNull();

        const helloSection = async () => {
            await openMarketplace();
            await openAddonDetail('hello');
            return vi.waitFor(() => {
                const el = document.querySelector('[data-testid="marketplace-slideover"] [data-testid="addon-settings-hello"]');
                if (!el || !el.querySelector('[data-field="shout"]')) throw new Error('hello settings not rendered');
                return el;
            });
        };
        const section = await helloSection();
        const labels = [...section.querySelectorAll('label')].map((l) => l.textContent.trim());
        expect(labels).toEqual(['Salutation', 'Greeting service key', 'Greetings folder', 'Greet in capitals']);
        const field = (key) => section.querySelector(`[data-field="${key}"]`);
        expect(field('api_key').getAttribute('type')).toBe('password');

        typeInto(field('salutation'), 'Ahoy');
        typeInto(field('api_key'), 'hk-0123456789ab');
        field('shout').click();
        await flush();
        section.querySelector('[data-testid="addon-settings-save-hello"]').click();
        await vi.waitFor(() => {
            if (!section.querySelector('[data-secret-hint="api_key"]')) throw new Error('secret hint not shown');
        });
        const put = calls.filter((c) => c.method === 'PUT' && c.url === '/api/plugins/hello/settings').at(-1);
        expect(put.body).toEqual({ values: {
            salutation: 'Ahoy', api_key: 'hk-0123456789ab', greetings_folder: '', shout: true,
        } });
        // After saving, the secret is only a hint — the input is empty again.
        expect(field('api_key').value).toBe('');
        expect(section.querySelector('[data-secret-hint="api_key"]').textContent).toContain('hk-…89ab');
        expect(document.body.textContent).not.toContain('hk-0123456789ab');
        // Saving again with the secret left empty sends null (= unchanged).
        section.querySelector('[data-testid="addon-settings-save-hello"]').click();
        await vi.waitFor(() => {
            const n = calls.filter((c) => c.method === 'PUT' && c.url === '/api/plugins/hello/settings').length;
            if (n < 2) throw new Error('second save not sent');
        });
        const again = calls.filter((c) => c.method === 'PUT' && c.url === '/api/plugins/hello/settings').at(-1);
        expect(again.body.values.api_key).toBeNull();
        expect(again.body.values.salutation).toBe('Ahoy');

        // An Add-on's own settings component renders in its slide-over.
        document.querySelector('[data-testid="marketplace-slideover-close"]').click();
        await flush();
        const solo = await openAddonDetail('solo');
        await vi.waitFor(() => {
            if (!solo.querySelector('.solo-settings')) throw new Error('solo settings not rendered');
        });
        expect(findByText('.solo-settings', 'Solo speaks English only calm (solo)')).toBeTruthy();
        document.querySelector('[data-testid="marketplace-slideover-close"]').click();
        await flush();

        // Slot texts follow the UI language; an Add-on without it falls back
        // to its default_language instead of showing key paths.
        go('#/settings');
        await vi.waitFor(() => {
            if (!findByText('h2', 'Settings')) throw new Error('settings not rendered');
        });
        findByText('button', window.LB_I18N.en['settings.tabAppearance']).click();
        await flush();
        const langSelect = [...document.querySelectorAll('select')].find((s) => s.querySelector('option[value="de"]'));
        langSelect.value = 'de';
        langSelect.dispatchEvent(new Event('change'));
        await vi.waitFor(() => {
            if (!findByText('h2', 'Einstellungen')) throw new Error('UI not German yet');
        });
        await helloSection();
        await vi.waitFor(() => {
            const first = document.querySelector('[data-testid="addon-settings-hello"] label');
            if (!first || first.textContent.trim() !== 'Anrede') throw new Error('labels not German yet');
        });
        document.querySelector('[data-testid="marketplace-slideover-close"]').click();
        await flush();
        await openAddonDetail('solo');
        await vi.waitFor(() => {
            if (!document.querySelector('.solo-settings')) throw new Error('solo settings not rendered');
        });
        expect(document.querySelector('.solo-settings').textContent).toContain('Solo speaks English only');
        expect(document.body.textContent).not.toContain('⟦');
        document.querySelector('[data-testid="marketplace-slideover-close"]').click();
        await flush();

        // --- switched off on the card: filter, aside and section are gone -----
        (await addonSwitch('hello')).click();
        await vi.waitFor(() => {
            if (window.LocalBib.isActive('hello')) throw new Error('hello still active');
        });
        await flush();
        expect(document.querySelector('[data-testid="addon-settings-hello"]')).toBeNull();
        go('#/');
        await openFilters();
        await flush();
        expect(document.querySelector('select.hello-filter-select')).toBeNull();
        expect(badge()).toBeNull();
        expect(papersCalls(calls).at(-1).url).not.toContain('cite_keys');
        errors.mockRestore();
    });
});
