// Marketplace view smoke test (#189, ADR-0021): card states, filter chips
// and the slide-over, driven from one mocked GET /api/marketplace response.
// No install action yet - the primary button only shows the Zustand.
//
// One boot for the whole file (vitest shares the module graph, and so the
// already-mounted app, across every `it()` in a file, the same pattern other
// multi-scenario boot tests in this suite use), so every scenario below
// reads the same mounted page instead of re-importing app.js per test.
import { beforeAll, describe, expect, it } from 'vitest';
import { bootMarketplace, findByText, flush } from './helpers.js';

function card(over) {
    return {
        id: 'demo', name: 'Demo', tagline: 'A demo Add-on', description: '',
        author: '', license: '', homepage: '', trust: 'official',
        languages: ['en'], tags: [], icon: '', screenshots: [],
        requires_source: false, size: null, in_index: true, source: '',
        installed: false, installed_version: '', installed_not_in_index: false,
        offered_version: '1.0.0', update_available: false, state: 'not_installed',
        permissions: [], versions: [],
        ...over,
    };
}

const DEMO_ONE = card({
    id: 'demo1', name: 'DemoOne', tagline: 'Research workbench',
    description: '# DemoOne\n\nOrganise ideas and publications.',
    trust: 'official', languages: ['de'], icon: 'assets/demo1/icon.png',
    screenshots: ['assets/demo1/shot1.png'], size: 512000,
    source: 'bundle', installed: true, installed_version: '1.1.0',
    offered_version: '1.2.0', update_available: true, state: 'update_available',
    permissions: [
        { key: 'library.read', enforced: true },
        { key: 'network', enforced: false },
    ],
    versions: [
        { version: '1.2.0', released: '2026-09-20', changelog: 'Werkbank journal view',
          requires_source: false, permissions: [], size: 512000, compatible: true, incompatible_reason: null },
        { version: '1.1.0', released: '2026-08-01', changelog: 'Initial release',
          requires_source: false, permissions: [], size: 480000, compatible: true, incompatible_reason: null },
    ],
});

const DEMO_TWO = card({
    id: 'demo2', name: 'DemoTwo', tagline: 'Trend analysis',
    trust: 'third-party', languages: ['de'], requires_source: true, size: null,
    state: 'not_installed', offered_version: '0.9.0',
});

// A native Bundle with its own numeric stack (#198): official, ~100 MB, installable.
const NATIVE = card({
    id: 'native', name: 'Native', tagline: 'Vendored numeric stack',
    trust: 'official', languages: ['de'], size: 109174596, offered_version: '0.2.0',
});

const WORKSHOP = card({
    id: 'workshop', name: 'Workshop', tagline: 'A Dev-Suchpfad Add-on',
    trust: '', languages: [], in_index: false, source: 'dev',
    installed: true, installed_version: '0.1.0', offered_version: '',
    state: 'dev',
});

describe('marketplace', () => {
    beforeAll(async () => {
        await bootMarketplace([
            {
                method: 'GET', url: '/api/marketplace',
                reply: { addons: [DEMO_ONE, DEMO_TWO, NATIVE, WORKSHOP], offline: false, has_index: true,
                         index_date: '2026-09-20T00:00:00Z', core: {} },
            },
        ]);
    });

    it('renders one card per Add-on with trust and requires-source badges', () => {
        expect(document.querySelector('[data-testid="marketplace-card-demo1"]')).toBeTruthy();
        expect(document.querySelector('[data-testid="marketplace-card-demo2"]')).toBeTruthy();
        expect(document.querySelector('[data-testid="marketplace-card-workshop"]')).toBeTruthy();

        expect(findByText('[data-testid="marketplace-trust-demo1"]', 'Official')).toBeTruthy();
        expect(findByText('[data-testid="marketplace-trust-demo2"]', 'Third-party')).toBeTruthy();
        expect(findByText('[data-testid="marketplace-card-demo2"] span', 'Source only')).toBeTruthy();
        expect(findByText('[data-testid="marketplace-state-demo1"]', 'Update available')).toBeTruthy();
        expect(findByText('[data-testid="marketplace-state-workshop"]', 'Development')).toBeTruthy();
    });

    it('offers an official native Bundle with its size and a clickable Install', () => {
        expect(findByText('[data-testid="marketplace-trust-native"]', 'Official')).toBeTruthy();
        expect(findByText('[data-testid="marketplace-card-native"] span', '104.1 MB')).toBeTruthy();
        const button = document.querySelector('[data-testid="marketplace-button-native"]');
        expect(button.tagName).toBe('BUTTON');
        expect(button.disabled).toBe(false);
        expect(button.textContent.trim()).toBe('Install');
    });

    it('gives a source-only Add-on no Install button and explains the source path', async () => {
        const button = document.querySelector('[data-testid="marketplace-button-demo2"]');
        expect(button.tagName).toBe('SPAN');   // a state label, nothing to click
        document.querySelector('[data-testid="marketplace-card-demo2"]').click();
        await flush();
        const hint = document.querySelector('[data-testid="marketplace-requires-source"]');
        expect(hint.textContent).toContain('runs from source code only');
        document.querySelector('[data-testid="marketplace-slideover-close"]').click();
        await flush();
    });

    it('never loads an icon directly from GitHub - only through the core-served asset URL', () => {
        const img = document.querySelector('[data-testid="marketplace-card-demo1"] img');
        expect(img).toBeTruthy();
        expect(img.getAttribute('src')).toBe('/api/marketplace/assets/assets/demo1/icon.png');
    });

    it('filter chips narrow the grid to Installed / Updates / Development', async () => {
        const grid = () => document.querySelector('[data-testid="marketplace-grid"]');
        const ids = () => [...grid().querySelectorAll('[data-testid^="marketplace-card-"]')].map((el) => el.dataset.testid);
        expect(ids().length).toBe(4);

        document.querySelector('[data-testid="marketplace-filter-installed"]').click();
        await flush();
        expect(ids()).toEqual(['marketplace-card-demo1', 'marketplace-card-workshop']);

        document.querySelector('[data-testid="marketplace-filter-updates"]').click();
        await flush();
        expect(ids()).toEqual(['marketplace-card-demo1']);

        document.querySelector('[data-testid="marketplace-filter-dev"]').click();
        await flush();
        expect(ids()).toEqual(['marketplace-card-workshop']);

        // back to "all" for any test running after this one
        document.querySelector('[data-testid="marketplace-filter-all"]').click();
        await flush();
    });

    it('opens the slide-over with description, permissions and versions, and closes again', async () => {
        document.querySelector('[data-testid="marketplace-card-demo1"]').click();
        await flush();

        const slideover = document.querySelector('[data-testid="marketplace-slideover"]');
        expect(slideover).toBeTruthy();
        expect(findByText('[data-testid="marketplace-slideover-name"]', 'DemoOne')).toBeTruthy();
        // Markdown was rendered (heading text survives, the raw "#" does not).
        expect(slideover.textContent).toContain('Organise ideas and publications');
        expect(slideover.innerHTML).not.toContain('# DemoOne');
        // Permissions with enforced/erklaert.
        expect(findByText('[data-testid="marketplace-permission-library.read"]', 'Enforced')).toBeTruthy();
        expect(findByText('[data-testid="marketplace-permission-network"]', 'Declared only')).toBeTruthy();
        // Versions list.
        expect(slideover.textContent).toContain('1.2.0');
        expect(slideover.textContent).toContain('Werkbank journal view');

        document.querySelector('[data-testid="marketplace-slideover-close"]').click();
        await flush();
        expect(document.querySelector('[data-testid="marketplace-slideover"]')).toBeFalsy();
    });
});
