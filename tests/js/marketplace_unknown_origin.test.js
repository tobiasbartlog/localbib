// Add-on Herkunft (cloud handoff task 5): an installed Add-on whose recorded
// origin the core does not recognise is never official and never runs
// silently: its card offers the Zustimmungsdialog, which shows the file
// warning and the third-party trust.
// Own file - app.js self-mounts once per module graph (see helpers.js).
import { describe, expect, it, vi } from 'vitest';
import { bootMarketplace, findByText, marketplaceCard } from './helpers.js';

const CARD = marketplaceCard({
    id: 'demo', name: 'Demo', trust: 'official', in_index: true,
    origin: 'unknown', enabled: false, needs_confirmation: true, addon_state: 'inactive',
    installed_permissions: [{ key: 'llm', enforced: true }, { key: 'storage', enforced: false, reserved: true }],
    permissions: [{ key: 'llm', enforced: true }, { key: 'storage', enforced: false, reserved: true }],
    missing_consent: ['llm'],
});

describe('marketplace: unrecognised origin', () => {
    it('asks for consent behind the file warning', async () => {
        await bootMarketplace([
            {
                method: 'GET', url: '/api/marketplace',
                reply: { addons: [CARD], offline: false, has_index: true, index_date: null, core: {} },
            },
        ]);
        const button = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="marketplace-button-demo"]');
            if (!el) throw new Error('no consent button');
            return el;
        });
        button.click();
        const d = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="consent-body"]');
            if (!el) throw new Error('consent dialog not shown');
            return el;
        });
        expect(d.querySelector('[data-testid="consent-file-warning"]')).toBeTruthy();
        expect(findByText('[data-testid="consent-trust"]', 'Third-party')).toBeTruthy();
        // ADD-2: a reserved Berechtigung says it grants nothing, no promise.
        const storage = d.querySelector('[data-testid="consent-permission-storage"]').textContent;
        expect(storage).toContain('Reserved — grants nothing yet');
        expect(storage).not.toContain('Enforced');
    });
});
