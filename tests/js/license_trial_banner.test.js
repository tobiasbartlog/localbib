// The running trial (#144): no gate, and the purchase banner says how many
// days are left instead of nagging with a constant sentence.
import { describe, it, expect, vi } from 'vitest';
import { bootApp } from './helpers.js';

const TRIAL = { days_total: 14, days_remaining: 3, expired: false };

describe('Kaufhinweis während der Testphase', () => {
    it('nennt die verbleibenden Tage und sperrt nichts', async () => {
        await bootApp([
            {
                method: 'GET',
                url: '/api/license/status',
                reply: {
                    activated: false,
                    key: '',
                    checkout_url: 'https://buy.example/localbib',
                    price_display: '29,50 €',
                    is_frozen: true,
                    trial: TRIAL,
                    blocked: false,
                    legacy_supporter: false,
                    legacy_note: '',
                },
            },
            { method: 'GET', url: '/api/banner/state', reply: { show: true, trial: TRIAL, blocked: false } },
        ]);

        const banner = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="banner-message"]');
            if (!el) throw new Error('Kaufhinweis nicht gerendert');
            return el;
        });
        expect(banner.textContent).toContain('noch 3 Tage');
        // Der Preis kommt aus der API-Antwort, nicht aus einem Literal in der SPA.
        expect(banner.textContent).toContain('29,50 €');
        expect(document.querySelector('[data-testid="license-gate"]')).toBeNull();
    });
});
