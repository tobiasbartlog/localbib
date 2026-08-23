// Activation fails (#142): the user reads the German sentence the server sent
// (here: the activation limit) and the state stays "not activated".
import { describe, it, expect, vi } from 'vitest';
import { bootSettings, typeInto, flush } from './helpers.js';

const LIMIT_ERROR =
    'Dieser Lizenzschlüssel ist bereits auf zwei Geräten aktiviert. ' +
    'Gib in deinem Polar-Konto ein Gerät frei und versuche es erneut.';

describe('Lizenz-Aktivierung (Fehlerfall)', () => {
    it('zeigt die deutsche Fehlermeldung des Servers', async () => {
        await bootSettings([
            {
                method: 'GET',
                url: '/api/license/status',
                reply: { activated: false, key: '', activated_at: '', checkout_url: '' },
            },
            {
                method: 'POST',
                url: '/api/license/activate',
                reply: { activated: false, error: LIMIT_ERROR },
            },
        ], 'license-tab');

        typeInto(document.querySelector('[data-testid="license-key-input"]'), 'LB--USED-UP');
        await flush();
        document.querySelector('[data-testid="license-activate"]').click();

        const message = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="license-message"]');
            if (!el) throw new Error('Fehlermeldung nicht gerendert');
            return el;
        });
        expect(message.textContent).toContain('zwei Geräten');
        expect(document.querySelector('[data-testid="license-activated"]')).toBeNull();
        expect(document.querySelector('[data-testid="license-key-input"]')).not.toBeNull();
    });
});
