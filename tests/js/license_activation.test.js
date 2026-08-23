// Smoke test for the Lizenz tab (issue #142, ADR-0015): a pasted key goes to
// /api/license/activate and the panel flips to "Lizenz aktiviert".
import { describe, it, expect, vi } from 'vitest';
import { bootSettings, typeInto, flush } from './helpers.js';

describe('Lizenz-Aktivierung', () => {
    it('aktiviert einen Schlüssel und meldet den Erfolg', async () => {
        const calls = await bootSettings([
            {
                method: 'GET',
                url: '/api/license/status',
                reply: { activated: false, key: '', activated_at: '', checkout_url: 'https://buy.example/localbib' },
            },
            {
                method: 'POST',
                url: '/api/license/activate',
                reply: { activated: true, key: '••••1234', activated_at: '2026-08-22T10:00:00' },
            },
        ], 'license-tab');

        // The buy link points at the checkout URL the server supplied.
        expect(
            document.querySelector('[data-testid="license-buy-link"]').getAttribute('href'),
        ).toBe('https://buy.example/localbib');

        typeInto(document.querySelector('[data-testid="license-key-input"]'), 'LB--ABCD-1234');
        await flush();
        document.querySelector('[data-testid="license-activate"]').click();
        await flush();

        const activate = calls.find((c) => c.url === '/api/license/activate');
        expect(activate.body).toEqual({ key: 'LB--ABCD-1234' });

        await vi.waitFor(() => {
            if (!document.querySelector('[data-testid="license-activated"]')) {
                throw new Error('Aktivierungs-Bestätigung nicht gerendert');
            }
        });
        expect(document.querySelector('[data-testid="license-activated"]').textContent)
            .toContain('••••1234');
        // Das Eingabefeld verschwindet — es gibt nichts mehr zu aktivieren.
        expect(document.querySelector('[data-testid="license-key-input"]')).toBeNull();
    });
});
