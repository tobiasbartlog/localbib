// An already activated install (#142, ADR-0015): the status comes from local
// state, the key field is gone, and nothing is validated again.
import { describe, it, expect, vi } from 'vitest';
import { bootSettings } from './helpers.js';

describe('Lizenz-Tab bei aktivierter Installation', () => {
    it('meldet "aktiviert" ohne Eingabefeld und ohne Aktivierungs-Request', async () => {
        const calls = await bootSettings([
            {
                method: 'GET',
                url: '/api/license/status',
                reply: { activated: true, key: '••••C570', activated_at: '2026-08-01T09:00:00', checkout_url: '' },
            },
        ], 'license-tab');

        await vi.waitFor(() => {
            if (!document.querySelector('[data-testid="license-activated"]')) {
                throw new Error('Aktivierter Zustand nicht gerendert');
            }
        });
        expect(document.querySelector('[data-testid="license-key-input"]')).toBeNull();
        expect(calls.some((c) => c.url === '/api/license/activate')).toBe(false);
    });
});
