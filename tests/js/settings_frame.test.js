// The Settings frame (#207): the section list on the left in four groups, one
// address per section, /settings and unknown slugs on General, and an entry
// per running Add-on — read from the Marketplace's own view — whose page shows
// the Add-on's head, its existing settings component, its granted
// Berechtigungen (read only) and the way back to the Marketplace. The list
// follows switching an Add-on off and on without a reload.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';

import {
    addonSwitch, defaultRoutes, findByText, flush, goTo, installFetchMock, marketplaceCard, openAddonDetail,
    settingsSection,
} from './helpers.js';

const q = (sel) => document.querySelector(sel);
const text = (sel) => (q(sel) ? q(sel).textContent.trim() : null);
const navIds = () => [...document.querySelectorAll('[data-testid="settings-nav"] [data-testid^="settings-nav-"]')]
    .map((el) => el.getAttribute('data-testid').replace('settings-nav-', ''));
const groups = () => [...document.querySelectorAll('[data-testid="settings-nav"] .lb-set-group')]
    .map((el) => el.textContent.trim());

async function waitForSection(slug) {
    await vi.waitFor(() => {
        if (settingsSection() !== slug) throw new Error(`section ${slug} not open (is ${settingsSection()})`);
        if (!q(`[data-settings-section="${slug}"]`)) throw new Error(`section ${slug} not rendered`);
    });
    await flush();
}

describe('settings frame', () => {
    it('groups the sections, gives each an address and lists the running Add-ons', async () => {
        let demoOn = true;
        const cards = () => [
            marketplaceCard({
                id: 'demo', name: 'Demo', tagline: 'Ideas and drafts next to your library.', trust: 'official',
                in_index: true, installed_version: '1.2.0', enabled: demoOn, addon_state: demoOn ? 'active' : 'inactive',
                installed_permissions: [{ key: 'library.read', enforced: true }, { key: 'network', enforced: false }],
            }),
            marketplaceCard({
                id: 'local', name: 'Local', source: 'dev', state: 'dev', trust: '', in_index: false,
                installed_version: '0.1.0', installed_permissions: [],
            }),
            marketplaceCard({ id: 'off', name: 'Off', enabled: false, addon_state: 'inactive' }),
            marketplaceCard({ id: 'store', name: 'Store', installed: false, installed_version: '', state: 'not_installed', addon_state: '' }),
        ];

        document.body.innerHTML = '<div id="app"></div>';
        history.replaceState(null, '', '#/settings');
        const calls = installFetchMock([
            { method: 'GET', url: /^\/api\/marketplace(\?.*)?$/, reply: () => ({ addons: cards(), offline: false, has_index: true, index_date: null, core: {} }) },
            {
                method: 'GET', url: '/api/plugins/demo/settings',
                reply: { id: 'demo', fields: [{ key: 'folder', type: 'path', label: 'demo.settings.folder', default: '' }], values: { folder: 'D:\\Demo' } },
            },
            { method: 'GET', url: '/api/plugins/local/settings', reply: { id: 'local', fields: [], values: {} } },
            { method: 'POST', url: '/api/plugins/demo/disable', reply: () => { demoOn = false; return {}; } },
            { method: 'POST', url: '/api/plugins/demo/enable', reply: () => { demoOn = true; return {}; } },
            { method: 'PUT', url: '/api/settings', reply: { status: 'ok' } },
            ...defaultRoutes(),
        ]);
        await import('../../static/app.js');

        // --- /settings lands on General ---------------------------------------
        await waitForSection('general');
        expect(window.location.hash).toBe('#/settings/general');
        expect(groups()).toEqual(['Settings', 'Add-ons', 'Maintenance', 'System']);
        // Only installed, running Add-ons get an entry, by name; the Marketplace
        // link always closes the group.
        await vi.waitFor(() => {
            if (!q('[data-testid="settings-nav-addon-demo"]')) throw new Error('add-on entries not loaded');
        });
        expect(navIds()).toEqual([
            'general', 'appearance', 'library', 'sources', 'ai-models', 'custom-fields',
            'addon-demo', 'addon-local', 'marketplace',
            'diagnosis', 'refresh', 'backup',
            'about',
        ]);
        expect(text('[data-testid="settings-nav-ai-models"]')).toBe('AI models');
        expect(text('[data-testid="settings-nav-custom-fields"]')).toBe('Custom fields');
        expect(q('[data-testid="settings-nav-marketplace"]').getAttribute('href')).toBe('#/marketplace');
        // The Add-on list is the Marketplace's view, without forcing the network.
        expect(calls.some((c) => c.url === '/api/marketplace?refresh=false')).toBe(true);

        // The active entry is marked, the head names group, title and purpose.
        const general = q('[data-testid="settings-nav-general"]');
        expect(general.classList.contains('is-active')).toBe(true);
        expect(general.getAttribute('aria-current')).toBe('page');
        expect(text('[data-testid="settings-head-group"]')).toBe('Settings');
        expect(text('[data-testid="settings-head-title"]')).toBe('General');
        expect(text('[data-testid="settings-head-description"]')).toContain('Language and which areas');
        // The app sidebar's Settings entry stays marked on every section.
        expect(q('a[href="#/settings"]').classList.contains('is-active')).toBe(true);
        // In a narrow window the app sidebar steps aside on this page only; the
        // list carries the way back.
        expect(q('.lb-root').classList.contains('is-settings')).toBe(true);
        expect(q('[data-testid="settings-back"]').getAttribute('href')).toBe('#/');

        // --- a deep link opens its section, the nav follows ---------------------
        goTo('#/settings/custom-fields');
        await waitForSection('custom-fields');
        expect(text('[data-testid="settings-head-title"]')).toBe('Custom fields');
        expect(q('[data-testid="settings-nav-custom-fields"]').classList.contains('is-active')).toBe(true);
        expect(general.classList.contains('is-active')).toBe(false);

        goTo('#/settings/refresh');
        await waitForSection('refresh');
        expect(text('[data-testid="settings-head-group"]')).toBe('Maintenance');
        expect(text('[data-testid="settings-head-title"]')).toBe('Refresh');

        goTo('#/settings/about');
        await waitForSection('about');
        expect(text('[data-testid="settings-head-group"]')).toBe('System');
        expect(text('[data-testid="settings-head-title"]')).toBe('License & About');

        // --- unknown slugs and Add-ons fall back to General -------------------
        goTo('#/settings/llm');
        await waitForSection('general');
        expect(window.location.hash).toBe('#/settings/general');
        goTo('#/settings/addons/ghost');
        await waitForSection('general');
        expect(window.location.hash).toBe('#/settings/general');

        // --- an Add-on's page ---------------------------------------------------
        q('[data-testid="settings-nav-addon-demo"]').click();
        await waitForSection('addons/demo');
        expect(window.location.hash).toBe('#/settings/addons/demo');
        expect(text('[data-testid="settings-head-group"]')).toBe('Add-on');
        expect(text('[data-testid="settings-head-title"]')).toBe('Demo');
        expect(text('[data-testid="settings-addon-trust"]')).toBe('Official');
        expect(text('[data-testid="settings-addon-version"]')).toBe('1.2.0');
        expect(text('[data-testid="settings-head-description"]')).toBe('Ideas and drafts next to your library.');
        expect(q('[data-testid="settings-nav-addon-demo"]').classList.contains('is-active')).toBe(true);
        // The existing settings component, with the Manifest's fields.
        const field = await vi.waitFor(() => {
            const el = q('[data-testid="addon-settings-demo"] [data-field="folder"]');
            if (!el) throw new Error('add-on settings not rendered');
            return el;
        });
        expect(field.value).toBe('D:\\Demo');
        // Granted Berechtigungen, read only: a sentence and enforced/declared.
        const perms = [...document.querySelectorAll('[data-testid="settings-addon-permissions"] [data-permission]')];
        expect(perms.map((li) => li.getAttribute('data-permission'))).toEqual(['library.read', 'network']);
        expect(perms[0].textContent).toContain('Read items, abstracts and full texts');
        expect(perms[0].textContent).toContain('Enforced');
        expect(perms[1].textContent).toContain('Declared only');
        expect(document.querySelectorAll('[data-testid="settings-addon-permissions"] input, [data-testid="settings-addon-permissions"] button').length).toBe(0);
        // Everything else happens in the Marketplace.
        const manage = q('[data-testid="settings-addon-open-marketplace"]');
        expect(q('[data-testid="settings-addon-manage"]').textContent).toContain('Switch off, update or remove');
        expect(manage.getAttribute('href')).toBe('#/marketplace?addon=demo');

        // A Dev-Suchpfad Add-on is third-party, says it has no settings and asks
        // for no Berechtigung.
        q('[data-testid="settings-nav-addon-local"]').click();
        await waitForSection('addons/local');
        expect(text('[data-testid="settings-head-title"]')).toBe('Local');
        expect(text('[data-testid="settings-addon-trust"]')).toBe('Third-party');
        await vi.waitFor(() => {
            if (!q('[data-testid="settings-addon-no-settings"]')) throw new Error('no-settings note missing');
        });
        expect(q('[data-testid="addon-settings-local"]')).toBeNull();
        expect(q('[data-testid="settings-addon-no-permissions"]')).toBeTruthy();

        // --- the Marketplace opens the Add-on and links back to its settings ---
        goTo('#/settings/addons/demo');
        await waitForSection('addons/demo');
        q('[data-testid="settings-addon-open-marketplace"]').click();
        const over = await vi.waitFor(() => {
            const el = q('[data-testid="marketplace-slideover"]');
            if (!el) throw new Error('slide-over not open');
            return el;
        });
        expect(over.querySelector('[data-testid="marketplace-slideover-name"]').textContent.trim()).toBe('Demo');
        expect(q('.lb-root').classList.contains('is-settings')).toBe(false);
        // No embedded settings any more, a link instead.
        expect(over.querySelector('[data-testid^="addon-settings-"]')).toBeNull();
        const back = over.querySelector('[data-testid="marketplace-settings-link"]');
        expect(back.textContent.trim()).toBe('Settings →');
        expect(back.getAttribute('href')).toBe('#/settings/addons/demo');
        q('[data-testid="marketplace-slideover-close"]').click();
        await flush();
        // A switched-off Add-on has no settings page to link to.
        const offOver = await openAddonDetail('off');
        expect(offOver.querySelector('[data-testid="marketplace-settings-link"]')).toBeNull();
        q('[data-testid="marketplace-slideover-close"]').click();
        await flush();

        // --- switching an Add-on off and on: the list follows, no reload --------
        (await addonSwitch('demo')).click();
        await vi.waitFor(() => {
            if (!calls.some((c) => c.method === 'POST' && c.url === '/api/plugins/demo/disable')) throw new Error('not switched off');
        });
        await flush();
        q('a[href="#/settings"]').click();
        await waitForSection('general');
        await vi.waitFor(() => {
            if (!q('[data-testid="settings-nav-addon-local"]')) throw new Error('add-on entries not loaded');
        });
        expect(q('[data-testid="settings-nav-addon-demo"]')).toBeNull();
        // Its old address falls back to General.
        goTo('#/settings/addons/demo');
        await waitForSection('general');

        goTo('#/marketplace');
        await vi.waitFor(() => {
            if (!findByText('h2', 'Marketplace')) throw new Error('Marketplace not rendered');
        });
        (await addonSwitch('demo')).click();
        await vi.waitFor(() => {
            if (!calls.some((c) => c.method === 'POST' && c.url === '/api/plugins/demo/enable')) throw new Error('not switched on');
        });
        await flush();
        q('a[href="#/settings"]').click();
        await waitForSection('general');
        await vi.waitFor(() => {
            if (!q('[data-testid="settings-nav-addon-demo"]')) throw new Error('demo entry not back');
        });

        // --- the section names follow the UI language -------------------------
        q('[data-testid="settings-language-de"]').click();
        await vi.waitFor(() => {
            if (groups()[0] !== 'Einstellungen') throw new Error('UI not German yet');
        });
        expect(groups()).toEqual(['Einstellungen', 'Add-ons', 'Pflege', 'System']);
        expect(text('[data-testid="settings-head-title"]')).toBe('Allgemein');
        expect(text('[data-testid="settings-nav-ai-models"]')).toBe('KI-Modelle');
        expect(text('[data-testid="settings-nav-custom-fields"]')).toBe('Eigene Felder');
        expect(text('[data-testid="settings-nav-diagnosis"]')).toBe('Diagnose');
        expect(text('[data-testid="settings-nav-refresh"]')).toBe('Aktualisieren');
        expect(text('[data-testid="settings-nav-about"]')).toBe('Lizenz & Info');
        expect(text('[data-testid="settings-nav-marketplace"]')).toBe('Mehr im Marketplace');
        expect(document.body.textContent).not.toContain('⟦');
    });
});
