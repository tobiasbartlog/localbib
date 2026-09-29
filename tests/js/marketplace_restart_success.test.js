// Marketplace restart button (#193): a successful restart in the frozen
// build. `marketplace_lifecycle.test.js` only covers the "not available from
// source" hint (its `/api/app/restart` mock always answers 409); this file
// mocks a 200 the way the frozen app answers and checks what the banner shows
// while the new process comes back up.
import { beforeAll, describe, expect, it, vi } from 'vitest';

import { bootMarketplace, marketplaceCard } from './helpers.js';

const s = { alpha: { updated: false } };

function reply() {
    const addons = [
        marketplaceCard({
            id: 'alpha', name: 'Alpha', in_index: true, trust: 'official', installed_version: '1.0.0',
            enabled: true, addon_state: 'active',
            offered_version: '2.0.0', update_available: !s.alpha.updated, state: s.alpha.updated ? 'installed' : 'update_available',
            pending_update: s.alpha.updated ? { version: '2.0.0', missing: [] } : null,
        }),
    ];
    return { addons, offline: false, has_index: true, index_date: null, core: {} };
}

const banner = () => document.querySelector('[data-testid="marketplace-restart-banner"]');

let calls;

describe('marketplace restart succeeds', () => {
    beforeAll(async () => {
        calls = await bootMarketplace([
            { method: 'GET', url: '/api/marketplace', reply },
            {
                method: 'POST', url: '/api/marketplace/install/alpha',
                stream: () => {
                    s.alpha.updated = true;
                    return [
                        { type: 'complete', addon: { id: 'alpha', state: 'active' },
                          consent: { id: 'alpha', name: 'Alpha', version: '2.0.0', trust: 'official', origin: 'index',
                                     sha256: '', update: true, staged: true, permissions: [], missing: [] },
                          update: { from: '1.0.0', to: '2.0.0', staged: true, missing: [], restart_required: true } },
                    ];
                },
            },
            { method: 'POST', url: '/api/app/restart', reply: { status: 'restarting' } },
        ]);
    });

    it('switches the banner to "restarting" and hides the button, without the source-mode hint', async () => {
        document.querySelector('[data-testid="marketplace-button-alpha"]').click();
        await vi.waitFor(() => {
            if (!banner()) throw new Error('restart banner not shown');
        });
        expect(document.querySelector('[data-testid="marketplace-restart"]')).toBeTruthy();
        expect(banner().textContent).toContain('Restart LocalBib to finish');

        document.querySelector('[data-testid="marketplace-restart"]').click();
        await vi.waitFor(() => {
            if (!banner().textContent.includes('restarting')) throw new Error('running text not shown');
        });
        expect(calls.some((c) => c.method === 'POST' && c.url === '/api/app/restart')).toBe(true);
        // The button to trigger it again is gone -- only "needed" shows it.
        expect(document.querySelector('[data-testid="marketplace-restart"]')).toBeNull();
        expect(document.querySelector('[data-testid="marketplace-restart-hint"]')).toBeNull();
        expect(banner().textContent).toContain('reloads by itself');
    });
});
