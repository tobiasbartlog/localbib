// #156: the remaining-device-slots line on the activation confirmation is
// conditional on the activation response actually carrying the figure — this
// covers the "present" branch; license_gate.test.js covers the "absent" one
// (Polar's reply today carries no such field, so the line must stay hidden).
import { describe, it, expect, vi } from 'vitest';
import { bootApp, typeInto, flush } from './helpers.js';

const BLOCKED_STATUS = {
    activated: false,
    key: '',
    activated_at: '',
    checkout_url: 'https://buy.example/localbib',
    price_display: '29,50 €',
    is_frozen: true,
    trial: { days_total: 14, days_remaining: 0, expired: true },
    blocked: true,
    legacy_supporter: false,
    legacy_note: '',
};

describe('Aktivierungsbestätigung mit Restplätze-Zahl', () => {
    it('zeigt die Restplätze-Zeile, wenn die Antwort die Zahl mitliefert', async () => {
        await bootApp([
            { method: 'GET', url: '/api/license/status', reply: BLOCKED_STATUS },
            { method: 'GET', url: '/api/banner/state', reply: { show: false, trial: BLOCKED_STATUS.trial, blocked: true } },
            {
                method: 'POST',
                url: '/api/license/activate',
                reply: { activated: true, key: '••••BA08', blocked: false, trial: null, activations_remaining: 1 },
            },
        ]);

        await vi.waitFor(() => {
            if (!document.querySelector('[data-testid="license-gate"]')) throw new Error('Aktivierungsdialog nicht gerendert');
        });
        typeInto(document.querySelector('[data-testid="license-gate-input"]'), 'LB--ABCD-1234');
        await flush();
        document.querySelector('[data-testid="license-gate-activate"]').click();
        await flush();

        const remaining = await vi.waitFor(() => {
            const el = document.querySelector('[data-testid="license-gate-confirm-remaining"]');
            if (!el) throw new Error('Restplätze-Zeile nicht gerendert');
            return el;
        });
        expect(remaining.textContent).toContain('1');
    });
});
