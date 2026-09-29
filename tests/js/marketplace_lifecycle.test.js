// Marketplace lifecycle (#193, ADR-0021): the switch on the card, updates
// (restart banner; a new Berechtigung asks for the difference only), the
// states on the card, rollback and removal in the slide-over, the restart
// button (frozen) and its hint (source). One boot, scenarios in order; the
// server is a stateful route table. Ids are generic — this file is exported.
import { beforeAll, describe, expect, it, vi } from 'vitest';

import { addonSwitch, bootMarketplace, findByText, flush, marketplaceCard, openAddonDetail } from './helpers.js';

const s = {
    alpha: { enabled: true, updated: false, removed: false },
    beta: { enabled: false },
    gamma: { staged: false },
    frozen: false,
};

function reply() {
    const addons = [
        marketplaceCard({
            id: 'alpha', name: 'Alpha', in_index: true, trust: 'official', installed_version: '1.0.0',
            enabled: s.alpha.enabled, addon_state: s.alpha.removed ? 'inactive' : (s.alpha.enabled ? 'active' : 'inactive'),
            offered_version: '2.0.0', update_available: !s.alpha.updated, state: s.alpha.updated ? 'installed' : 'update_available',
            pending_update: s.alpha.updated ? { version: '2.0.0', missing: [] } : null,
            previous_version: '0.9.0', rollback_available: true, pending_removal: s.alpha.removed,
        }),
        marketplaceCard({
            id: 'beta', name: 'Beta', enabled: s.beta.enabled, addon_state: s.beta.enabled ? 'active' : 'inactive',
            missing_consent: s.beta.enabled ? [] : ['network'],
            installed_permissions: [{ key: 'network', enforced: false }],
        }),
        marketplaceCard({
            id: 'gamma', name: 'Gamma', in_index: true, installed_version: '1.0.0', offered_version: '1.1.0',
            update_available: !s.gamma.staged, state: s.gamma.staged ? 'installed' : 'update_available',
            pending_update: s.gamma.staged ? { version: '1.1.0', missing: ['network'] } : null,
            versions: [{ version: '1.1.0', permissions: [{ key: 'library.read', enforced: true }, { key: 'network', enforced: false }] }],
        }),
        marketplaceCard({ id: 'broken', name: 'Broken', addon_state: 'error',
                          error: { code: 'error.plugins.importFailed', message: 'ImportError: boom' } }),
        marketplaceCard({ id: 'crash', name: 'Crash', enabled: false, addon_state: 'error',
                          error: { code: 'error.plugins.crashedDuringLoad', message: '' } }),
        marketplaceCard({ id: 'old', name: 'Old', in_index: true, installed_version: '0.5.0', enabled: true,
                          addon_state: 'incompatible', error: { code: 'error.plugins.contractMismatch', message: '' },
                          offered_version: '1.0.0', update_available: true, state: 'update_available' }),
        // A legacy document has a previous_version but lost its recorded
        // Herkunft (4e25609): the rollback endpoint would 409, so no button.
        marketplaceCard({ id: 'orphan', name: 'Orphan', previous_version: '0.4.0', rollback_available: false }),
        marketplaceCard({ id: 'envdev', name: 'EnvDev', source: 'dev', state: 'dev', dev_origin: 'env' }),
        // #199: never installed, incompatible -- the card must say why, not
        // just "Incompatible" (the live bug: an artifact for another Python).
        marketplaceCard({
            id: 'pymismatch', name: 'PyMismatch', in_index: true, installed: false, installed_version: '',
            state: 'incompatible', addon_state: '', enabled: false,
            incompatible_reason: { code: 'python', needed: ['cp313-win_amd64'], have: 'cp314-win_amd64' },
        }),
        // A dev folder claiming an installed id (#8da7248) shadows it and is
        // recorded off until the folder is confirmed; the note explains why.
        marketplaceCard({
            id: 'shadowed', name: 'Shadowed', source: 'dev', state: 'dev', dev_origin: 'document',
            enabled: false, addon_state: 'inactive', needs_confirmation: true, dev_shadow_disabled: true,
        }),
    ];
    return { addons, offline: false, has_index: true, index_date: null, core: {} };
}

const card = (id) => document.querySelector(`[data-testid="marketplace-card-${id}"]`);
const dialog = () => document.querySelector('[data-testid="consent-dialog"]');
const banner = () => document.querySelector('[data-testid="marketplace-restart-banner"]');

let calls;

describe('marketplace lifecycle', () => {
    beforeAll(async () => {
        calls = await bootMarketplace([
            { method: 'GET', url: '/api/marketplace', reply },
            { method: 'POST', url: '/api/plugins/alpha/disable', reply: () => { s.alpha.enabled = false; return {}; } },
            { method: 'POST', url: '/api/plugins/alpha/enable', reply: () => { s.alpha.enabled = true; return {}; } },
            {
                method: 'POST', url: '/api/marketplace/install/alpha',
                stream: () => {
                    s.alpha.updated = true;
                    return [
                        { type: 'progress', step: 'download', received: 10, total: 10 },
                        { type: 'complete', addon: { id: 'alpha', state: 'active' },
                          consent: { id: 'alpha', name: 'Alpha', version: '2.0.0', trust: 'official', origin: 'index',
                                     sha256: '', update: true, staged: true, permissions: [], missing: [] },
                          update: { from: '1.0.0', to: '2.0.0', staged: true, missing: [], restart_required: true } },
                    ];
                },
            },
            {
                method: 'POST', url: '/api/marketplace/install/gamma',
                stream: () => {
                    s.gamma.staged = true;
                    return [
                        { type: 'complete', addon: { id: 'gamma', state: 'active' },
                          consent: { id: 'gamma', name: 'Gamma', version: '1.1.0', trust: 'third-party', origin: 'index',
                                     sha256: '', update: true, staged: true,
                                     permissions: [{ key: 'network', enforced: false }], missing: ['network'] },
                          update: { from: '1.0.0', to: '1.1.0', staged: true, missing: ['network'], restart_required: true } },
                    ];
                },
            },
            { method: 'POST', url: '/api/plugins/gamma/update/consent', reply: { id: 'gamma', restart_required: true } },
            { method: 'POST', url: '/api/plugins/alpha/rollback', reply: { id: 'alpha', restart_required: true } },
            { method: 'POST', url: '/api/plugins/alpha/remove', reply: () => { s.alpha.removed = true; return { id: 'alpha', restart_required: true }; } },
            { method: 'GET', url: '/api/plugins/alpha/settings', reply: { id: 'alpha', fields: [], values: {} } },
            {
                method: 'POST', url: '/api/app/restart',
                status: 409,
                reply: { detail: { code: 'error.restart_unavailable' } },
            },
        ]);
    });

    it('switches an Add-on off and on from its card, without a reload', async () => {
        const marker = document.querySelector('[data-testid="marketplace-grid"]');
        const sw = await addonSwitch('alpha');
        expect(sw.getAttribute('aria-checked')).toBe('true');
        sw.click();
        await vi.waitFor(() => {
            if ((document.querySelector('[data-testid="marketplace-toggle-alpha"]') || {}).getAttribute?.('aria-checked') !== 'false') {
                throw new Error('still on');
            }
        });
        expect(calls.some((c) => c.method === 'POST' && c.url === '/api/plugins/alpha/disable')).toBe(true);
        (await addonSwitch('alpha')).click();
        await vi.waitFor(() => {
            if (document.querySelector('[data-testid="marketplace-toggle-alpha"]').getAttribute('aria-checked') !== 'true') {
                throw new Error('still off');
            }
        });
        expect(calls.some((c) => c.method === 'POST' && c.url === '/api/plugins/alpha/enable')).toBe(true);
        // Same page instance: nothing was reloaded.
        expect(document.querySelector('[data-testid="marketplace-grid"]')).toBe(marker);

        // Switching on without every Berechtigung agreed to asks first.
        (await addonSwitch('beta')).click();
        await vi.waitFor(() => {
            if (!dialog()) throw new Error('consent dialog not shown');
        });
        expect(calls.some((c) => c.url === '/api/plugins/beta/enable')).toBe(false);
        dialog().querySelector('[data-testid="consent-decline"]').click();
        await flush();
    });

    it('shows error, boot marker and incompatible-with-update on the cards', () => {
        expect(card('broken').querySelector('[data-testid="marketplace-note-error-broken"]').textContent)
            .toContain('ImportError: boom');
        expect(card('crash').querySelector('[data-testid="marketplace-note-bootmarker-crash"]').textContent)
            .toContain('LocalBib stopped while this Add-on was loading');
        const old = card('old');
        expect(old.querySelector('[data-testid="marketplace-note-incompatible-old"]').textContent)
            .toContain('another Add-on Contract');
        expect(old.querySelector('[data-testid="marketplace-note-offer-old"]').textContent).toContain('1.0.0');
        expect(old.querySelector('[data-testid="marketplace-button-old"]').textContent.trim()).toBe('Update');
        expect(old.querySelector('[data-testid="marketplace-toggle-old"]')).toBeNull();
        expect(document.body.textContent).not.toContain('⟦');
    });

    it('explains a never-installed incompatible card by its reason (#199)', () => {
        const note = card('pymismatch').querySelector('[data-testid="marketplace-note-incompatibleReason-pymismatch"]');
        expect(note.textContent).toContain('Python 3.13');
        expect(note.textContent).toContain('Python 3.14');
        expect(card('pymismatch').querySelector('[data-testid="marketplace-toggle-pymismatch"]')).toBeNull();
    });

    it('updates without a new permission: the restart banner, and from source the hint', async () => {
        document.querySelector('[data-testid="marketplace-button-alpha"]').click();
        await vi.waitFor(() => {
            if (!banner()) throw new Error('restart banner not shown');
        });
        expect(dialog()).toBeNull();
        expect(card('alpha').querySelector('[data-testid="marketplace-note-pending-alpha"]').textContent)
            .toContain('2.0.0');
        document.querySelector('[data-testid="marketplace-restart"]').click();
        await vi.waitFor(() => {
            if (!document.querySelector('[data-testid="marketplace-restart-hint"]')) throw new Error('hint not shown');
        });
        expect(calls.some((c) => c.method === 'POST' && c.url === '/api/app/restart')).toBe(true);
        expect(document.querySelector('[data-testid="marketplace-restart-hint"]').textContent).toContain('source code');
    });

    it('updates with a new permission: the dialog shows only the difference, the old version runs on', async () => {
        document.querySelector('[data-testid="marketplace-button-gamma"]').click();
        const d = await vi.waitFor(() => {
            const el = dialog();
            if (!el) throw new Error('consent dialog not shown');
            return el;
        });
        expect(d.querySelector('[data-testid="consent-title"]').textContent).toContain('Update Gamma to 1.1.0');
        const rows = [...d.querySelectorAll('[data-testid^="consent-permission-"]')].map((li) => li.dataset.testid);
        expect(rows).toEqual(['consent-permission-network']);
        d.querySelector('[data-testid="consent-accept"]').click();
        await vi.waitFor(() => {
            if (dialog()) throw new Error('dialog still open');
        });
        const agreed = calls.find((c) => c.url === '/api/plugins/gamma/update/consent');
        expect(agreed.body).toEqual({ permissions: ['network'] });
        expect(calls.some((c) => c.url === '/api/plugins/gamma/enable' || c.url === '/api/plugins/gamma/consent')).toBe(false);
        expect(banner()).toBeTruthy();
    });

    it('rolls back and removes from the slide-over', async () => {
        const over = await openAddonDetail('alpha');
        over.querySelector('[data-testid="marketplace-rollback"]').click();
        await vi.waitFor(() => {
            if (!calls.some((c) => c.method === 'POST' && c.url === '/api/plugins/alpha/rollback')) throw new Error('no rollback');
        });
        expect(findByText('[data-testid="marketplace-rollback"]', '0.9.0')).toBeTruthy();

        document.querySelector('[data-testid="marketplace-remove"]').click();
        await flush();
        expect(calls.some((c) => c.url === '/api/plugins/alpha/remove')).toBe(false);
        document.querySelector('[data-testid="marketplace-remove-confirm"]').click();
        await vi.waitFor(() => {
            if (!document.querySelector('[data-testid="marketplace-slideover"] [data-testid="marketplace-note-removal-alpha"]')) {
                throw new Error('removal note not shown');
            }
        });
        expect(document.querySelector('[data-testid="marketplace-remove"]')).toBeNull();
        expect(banner()).toBeTruthy();
    });

    it('offers no rollback when the previous version\'s Herkunft is unknown', async () => {
        const over = await openAddonDetail('orphan');
        expect(over.querySelector('[data-testid="marketplace-rollback"]')).toBeNull();
    });

    it('offers no removal for a Dev-Suchpfad from the environment, only a note', async () => {
        const over = await openAddonDetail('envdev');
        await vi.waitFor(() => {
            if (!over.querySelector('[data-testid="marketplace-dev-env-note"]')) throw new Error('note not shown');
        });
        expect(over.querySelector('[data-testid="marketplace-dev-env-note"]').textContent).toContain('LOCALBIB_PLUGIN_DEV_PATHS');
        expect(over.querySelector('[data-testid="marketplace-remove"]')).toBeNull();
    });

    it('explains on the card and in the slide-over why a dev folder switched the Add-on off', async () => {
        expect(card('shadowed').querySelector('[data-testid="marketplace-note-devShadow-shadowed"]').textContent)
            .toContain('development folder with the same id was added');
        const over = await openAddonDetail('shadowed');
        expect(over.querySelector('[data-testid="marketplace-note-devShadow-shadowed"]').textContent)
            .toContain('development folder with the same id was added');
    });
});
