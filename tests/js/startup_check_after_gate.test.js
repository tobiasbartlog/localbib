// Startup check (#209): the popup waits behind the license gate. While the
// blocking activation dialog stands, no popup; once the customer is through,
// the report shows.
// Own file — app.js self-mounts once per module graph (see helpers.js).
import { describe, it, expect, vi } from 'vitest';
import { bootApp, flush, typeInto } from './helpers.js';
import { BROKEN_REPORT, setUpRoutes } from './startup_check_fixtures.js';

const q = (sel) => document.querySelector(sel);

const BLOCKED = {
    activated: false, key: '', activated_at: '', checkout_url: 'https://buy.example/localbib',
    price_amount: 29, price_currency: 'EUR', is_frozen: true,
    trial: { days_total: 14, days_remaining: 0, expired: true }, blocked: true,
    legacy_supporter: false, legacy_note: '',
};

describe('startup check: after the license gate', () => {
    it('shows the popup only once the gate is passed', async () => {
        sessionStorage.clear();
        const calls = await bootApp([
            { method: 'GET', url: '/api/license/status', reply: BLOCKED },
            { method: 'GET', url: '/api/banner/state', reply: { show: false, trial: BLOCKED.trial, blocked: true } },
            { method: 'POST', url: '/api/license/activate', reply: { activated: true, key: '••••1234', blocked: false, trial: null } },
            ...setUpRoutes(BROKEN_REPORT),
        ]);
        await vi.waitFor(() => {
            if (!q('[data-testid="license-gate"]')) throw new Error('gate not shown');
            if (!calls.some((c) => c.url === '/api/llm/startup-check')) throw new Error('report not read');
        });
        await flush(); await flush();
        expect(q('[data-testid="startup-check-dialog"]')).toBeNull();

        typeInto(q('[data-testid="license-gate-input"]'), 'LB--ABCD-1234');
        await flush();
        q('[data-testid="license-gate-activate"]').click();
        const dismiss = await vi.waitFor(() => {
            const el = q('[data-testid="license-gate-confirm-dismiss"]');
            if (!el) throw new Error('confirmation not shown');
            return el;
        });
        // The confirmation is still part of the gate.
        expect(q('[data-testid="startup-check-dialog"]')).toBeNull();
        dismiss.click();
        await vi.waitFor(() => {
            if (!q('[data-testid="startup-check-dialog"]')) throw new Error('popup not shown after the gate');
        });
        expect(q('[data-testid="license-gate"]')).toBeNull();
    });
});
